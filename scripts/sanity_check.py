import os
import sys
from pathlib import Path
import random

# Add project root to sys.path so config and core can be imported
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from core.models import FuelStation


def run_sanity_check(sample_size=10):
    total = FuelStation.objects.count()
    print(f"Total stations in SQLite: {total}")
    print(f"Sampling {sample_size} random stations to verify coordinates on Google Maps:\n")

    # Pick random stations across the dataset
    all_stations = list(FuelStation.objects.all())
    sample = random.sample(all_stations, min(sample_size, len(all_stations)))

    for idx, s in enumerate(sample, 1):
        maps_link = f"https://www.google.com/maps?q={s.latitude},{s.longitude}"
        print(f"{idx}. {s.name}")
        print(f"   Location:    {s.city}, {s.state}")
        print(f"   Coordinates: ({s.latitude:.4f}, {s.longitude:.4f})")
        print(f"   Diesel Price: ${s.price_per_gallon}/gal")
        print(f"   Google Maps:  {maps_link}\n")


if __name__ == '__main__':
    run_sanity_check(10)
