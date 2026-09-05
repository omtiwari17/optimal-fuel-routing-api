import csv
import io
import logging
from decimal import Decimal
from pathlib import Path
import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from core.models import FuelStation

logger = logging.getLogger(__name__)

CENSUS_BATCH_URL = 'https://geocoding.geo.census.gov/geocoder/locations/addressbatch'
DEFAULT_BATCH_SIZE = 2500
US_CITIES_URL = 'https://raw.githubusercontent.com/kelvins/US-Cities-Database/main/csv/us_cities.csv'

# Canadian provinces present in North American freight data (out of US routing scope)
CANADIAN_PROVINCES = {'AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE', 'QC', 'SK', 'YT'}

# City name aliases for 6 minor spelling variations or unincorporated neighborhoods
CITY_ALIASES = {
    ('port wentworth', 'GA'): ('savannah', 'GA'),
    ('elizabethport', 'NJ'): ('elizabeth', 'NJ'),
    ('brookpark', 'OH'): ('brook park', 'OH'),
    ('evergreen', 'AL'): (31.4338, -86.9544),
    ('henrico', 'VA'): ('richmond', 'VA'),
    ('university park', 'IL'): (41.4464, -87.6853),
}


class Command(BaseCommand):
    help = (
        'Two-tier offline geocoding pipeline: '
        'Tier 1 = US Census Bureau Batch Geocoder (street-level), '
        'Tier 2 = Offline US City Centroid database (city-level fallback). '
        'Yields ~100% valid coordinates for US stations without runtime API calls.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv',
            type=str,
            default=None,
            help='Path to fuel prices CSV file (defaults to data/ or root)'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Clear existing stations and re-geocode'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=DEFAULT_BATCH_SIZE,
            help='Batch size for Census geocoder requests'
        )

    def handle(self, *args, **options):
        # 1. Check existing stations
        existing_count = FuelStation.objects.count()
        if existing_count > 0 and not options['force']:
            self.stdout.write(
                self.style.WARNING(
                    f'FuelStation table already contains {existing_count} records. '
                    'Use --force to wipe and re-import.'
                )
            )
            return

        if options['force'] and existing_count > 0:
            self.stdout.write(self.style.WARNING(f'Deleting {existing_count} existing station records...'))
            FuelStation.objects.all().delete()

        # 2. Locate CSV file
        csv_path = self._locate_csv(options['csv'])
        if not csv_path or not csv_path.exists():
            self.stderr.write(self.style.ERROR(f'CSV file not found at: {csv_path}'))
            return

        self.stdout.write(self.style.SUCCESS(f'Reading fuel station data from: {csv_path}'))
        with open(csv_path, mode='r', encoding='utf-8') as f:
            reader = list(csv.DictReader(f))

        total_rows = len(reader)
        self.stdout.write(f'Total records in CSV: {total_rows}')

        # 3. TIER 1: Process records in batches via US Census Bureau Batch Geocoder
        self.stdout.write(self.style.NOTICE('\n--- TIER 1: US Census Batch Geocoder (Street-Level) ---'))
        batch_size = options['batch_size']
        tier1_matched = []
        tier1_unmatched = []

        for start_idx in range(0, total_rows, batch_size):
            chunk = reader[start_idx:start_idx + batch_size]
            self.stdout.write(
                f'Submitting batch {start_idx + 1} to {start_idx + len(chunk)} to Census Batch Geocoder...'
            )
            chunk_matches, chunk_unmatched = self._geocode_batch(chunk, start_idx)
            tier1_matched.extend(chunk_matches)
            tier1_unmatched.extend(chunk_unmatched)

        self.stdout.write(self.style.SUCCESS(f'Tier 1 matches (street-level): {len(tier1_matched)}'))
        self.stdout.write(f'Tier 1 unmatched (highway exits / rural): {len(tier1_unmatched)}')

        # 4. TIER 2: Offline City Centroid Fallback
        self.stdout.write(self.style.NOTICE('\n--- TIER 2: Offline City Centroid Fallback ---'))
        city_lookup = self._load_city_lookup()

        tier2_matched = []
        canadian_skipped = []
        truly_unmatched = []

        for row in tier1_unmatched:
            state = row.get('State', '').strip().upper()
            city = row.get('City', '').strip().lower()

            if state in CANADIAN_PROVINCES:
                canadian_skipped.append(row)
                continue

            # Look up city centroid
            coords = city_lookup.get((city, state))
            if not coords and (city, state) in CITY_ALIASES:
                alias = CITY_ALIASES[(city, state)]
                if isinstance(alias, tuple) and isinstance(alias[0], (int, float)):
                    coords = alias
                else:
                    coords = city_lookup.get(alias)

            if coords:
                lat, lon = coords
                try:
                    opis_id_val = int(row['OPIS Truckstop ID']) if row.get('OPIS Truckstop ID') else None
                except ValueError:
                    opis_id_val = None

                try:
                    price_val = Decimal(row['Retail Price'].strip())
                except Exception:
                    price_val = Decimal('0.000')

                tier2_matched.append(
                    FuelStation(
                        opis_id=opis_id_val,
                        name=row.get('Truckstop Name', '').strip(),
                        address=row.get('Address', '').strip(),
                        city=row.get('City', '').strip(),
                        state=state,
                        price_per_gallon=price_val,
                        latitude=lat,
                        longitude=lon
                    )
                )
            else:
                truly_unmatched.append(row)

        self.stdout.write(self.style.SUCCESS(f'Tier 2 fallback matches (city-level): {len(tier2_matched)}'))
        self.stdout.write(f'Non-US Canadian stations excluded: {len(canadian_skipped)}')
        self.stdout.write(f'Truly unmatched US stations: {len(truly_unmatched)}')

        # 5. Bulk insert all stations into SQLite
        all_stations_to_save = tier1_matched + tier2_matched
        self.stdout.write(f'\nSaving {len(all_stations_to_save)} total stations into SQLite database...')
        FuelStation.objects.bulk_create(all_stations_to_save, batch_size=1000)

        # 6. Audit Logging
        log_dir = settings.BASE_DIR / 'data'
        log_dir.mkdir(exist_ok=True)

        unmatched_log = log_dir / 'unmatched_stations.log'
        with open(unmatched_log, 'w', encoding='utf-8') as f:
            f.write("# Truly unmatched US stations\n")
            for item in truly_unmatched:
                f.write(f"{item.get('OPIS Truckstop ID')}, {item.get('Truckstop Name')}, {item.get('Address')}, {item.get('City')}, {item.get('State')}\n")
            f.write("\n# Canadian stations excluded (outside USA routing scope)\n")
            for item in canadian_skipped:
                f.write(f"{item.get('OPIS Truckstop ID')}, {item.get('Truckstop Name')}, {item.get('Address')}, {item.get('City')}, {item.get('State')}\n")

        # 7. Summary
        total_us = total_rows - len(canadian_skipped)
        coverage_pct = (len(all_stations_to_save) / total_us) * 100 if total_us else 0

        self.stdout.write(self.style.SUCCESS('\n=== TWO-TIER GEOCODING PIPELINE COMPLETE ==='))
        self.stdout.write(f'Total CSV records: {total_rows}')
        self.stdout.write(f'Non-US records excluded (Canada): {len(canadian_skipped)}')
        self.stdout.write(f'Total US fuel stations: {total_us}')
        self.stdout.write(self.style.SUCCESS(f'  - Tier 1 (Exact Street Matches via Census): {len(tier1_matched)}'))
        self.stdout.write(self.style.SUCCESS(f'  - Tier 2 (City Centroid Fallback): {len(tier2_matched)}'))
        self.stdout.write(self.style.SUCCESS(f'Total US Stations in SQLite: {len(all_stations_to_save)} ({coverage_pct:.2f}% US coverage)'))
        self.stdout.write(f'Unresolved stations logged to: {unmatched_log}')

    def _locate_csv(self, explicit_path):
        if explicit_path:
            return Path(explicit_path)
        candidates = [
            settings.BASE_DIR / 'data' / 'fuel-prices-for-be-assessment.csv',
            settings.BASE_DIR / 'fuel-prices-for-be-assessment.csv',
        ]
        for p in candidates:
            if p.exists():
                return p
        return candidates[0]

    def _load_city_lookup(self):
        cities_file = settings.BASE_DIR / 'data' / 'us_cities.csv'
        if not cities_file.exists():
            self.stdout.write('Downloading offline US cities dataset for fallback...')
            res = requests.get(US_CITIES_URL, timeout=30)
            res.raise_for_status()
            with open(cities_file, 'wb') as f:
                f.write(res.content)

        lookup = {}
        with open(cities_file, mode='r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                key = (row['CITY'].strip().lower(), row['STATE_CODE'].strip().upper())
                lookup[key] = (float(row['LATITUDE']), float(row['LONGITUDE']))
        return lookup

    def _geocode_batch(self, chunk, start_offset):
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)

        for idx, row in enumerate(chunk):
            global_id = start_offset + idx
            address = row['Address'].strip()
            city = row['City'].strip()
            state = row['State'].strip()
            writer.writerow([global_id, address, city, state, ''])

        files = {'addressFile': ('batch.csv', csv_buffer.getvalue(), 'text/csv')}
        data = {'benchmark': 'Public_AR_Current'}

        try:
            response = requests.post(
                CENSUS_BATCH_URL,
                files=files,
                data=data,
                timeout=180
            )
            response.raise_for_status()
        except requests.RequestException as e:
            self.stderr.write(self.style.ERROR(f'Census Batch request failed: {e}'))
            return [], chunk

        lines = [line for line in response.text.strip().split('\n') if line.strip()]
        csv_reader = csv.reader(lines)

        matched_dict = {}
        for res_row in csv_reader:
            if len(res_row) >= 6 and res_row[2] == 'Match':
                try:
                    g_id = int(res_row[0])
                    coords = res_row[5].split(',')
                    longitude = float(coords[0].strip())
                    latitude = float(coords[1].strip())
                    matched_dict[g_id] = (latitude, longitude)
                except (ValueError, IndexError):
                    continue

        matched_stations = []
        unmatched_rows = []

        for idx, row in enumerate(chunk):
            global_id = start_offset + idx
            if global_id in matched_dict:
                lat, lon = matched_dict[global_id]
                try:
                    opis_id_val = int(row['OPIS Truckstop ID']) if row.get('OPIS Truckstop ID') else None
                except ValueError:
                    opis_id_val = None

                try:
                    price_val = Decimal(row['Retail Price'].strip())
                except Exception:
                    price_val = Decimal('0.000')

                matched_stations.append(
                    FuelStation(
                        opis_id=opis_id_val,
                        name=row['Truckstop Name'].strip(),
                        address=row['Address'].strip(),
                        city=row['City'].strip(),
                        state=row['State'].strip(),
                        price_per_gallon=price_val,
                        latitude=lat,
                        longitude=lon
                    )
                )
            else:
                unmatched_rows.append(row)

        self.stdout.write(f'  -> Matched: {len(matched_stations)}, Unmatched: {len(unmatched_rows)}')
        return matched_stations, unmatched_rows
