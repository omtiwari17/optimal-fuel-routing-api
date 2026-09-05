import math
from decimal import Decimal
from typing import List, Dict, Any, Optional
from django.db.models import Q
from core.models import FuelStation
from core.polyline_utils import (
    haversine_distance,
    compute_cumulative_distances,
    sample_checkpoints,
)


class StationMatcher:
    """
    Finds and projects candidate fuel stations along a driving route corridor.

    Features:
    - Queries SQLite efficiently using bounding boxes centered on route checkpoints.
    - Eliminates searching the entire continent on long diagonal routes.
    - Projects candidate stations onto the route using fast windowed equirectangular
      search followed by exact Haversine calculation.
    - Filters stations within a strict corridor radius (default: 10.0 miles off highway).
    - Returns stations sorted by ascending route mile marker.
    """

    DEFAULT_CORRIDOR_RADIUS_MILES = 10.0
    DEFAULT_CHECKPOINT_INTERVAL_MILES = 30.0

    @classmethod
    def find_stations_along_route(
        cls,
        coordinates: List[List[float]],
        cumulative_distances: Optional[List[float]] = None,
        corridor_radius_miles: float = DEFAULT_CORRIDOR_RADIUS_MILES,
        checkpoint_interval_miles: float = DEFAULT_CHECKPOINT_INTERVAL_MILES,
    ) -> List[Dict[str, Any]]:
        """
        Find all fuel stations located within `corridor_radius_miles` of the route.

        Args:
            coordinates: Ordered list of [lon, lat] highway vertices from ORS.
            cumulative_distances: Precomputed running miles along coordinates (optional).
            corridor_radius_miles: Max allowed lateral distance from highway in miles.
            checkpoint_interval_miles: Distance between checkpoint anchors in miles.

        Returns:
            List of station dicts sorted by ascending route_mile:
            [
                {
                    'id': int,
                    'opis_id': int,
                    'name': str,
                    'address': str,
                    'city': str,
                    'state': str,
                    'latitude': float,
                    'longitude': float,
                    'price_per_gallon': float,
                    'route_mile': float,
                    'detour_miles': float,
                },
                ...
            ]
        """
        if not coordinates:
            return []

        # 1. Compute cumulative distances if not provided
        if cumulative_distances is None or len(cumulative_distances) != len(coordinates):
            cumulative_distances = compute_cumulative_distances(coordinates)

        if len(coordinates) < 2:
            return []

        # 2. Sample checkpoints every ~30 miles
        checkpoints = sample_checkpoints(
            coordinates,
            cumulative_distances,
            interval_miles=checkpoint_interval_miles
        )

        # 3. Build spatial bounding box queries around checkpoints
        # Search radius around each checkpoint must cover half the interval plus corridor radius
        search_radius = (checkpoint_interval_miles / 2.0) + corridor_radius_miles
        q_filter = Q()

        for cp in checkpoints:
            cp_lat, cp_lon = cp['lat'], cp['lon']
            cos_lat = math.cos(math.radians(cp_lat))

            # 1 deg latitude ~ 69.0 miles
            # 1 deg longitude ~ 69.0 * cos(lat) miles
            dlat = search_radius / 69.0
            dlon = search_radius / (69.0 * cos_lat)

            q_filter |= Q(
                latitude__gte=cp_lat - dlat,
                latitude__lte=cp_lat + dlat,
                longitude__gte=cp_lon - dlon,
                longitude__lte=cp_lon + dlon,
            )

        # 4. Fetch candidate stations from SQLite within the combined bounding boxes
        candidate_stations = list(
            FuelStation.objects.filter(q_filter)
            .values(
                'id', 'opis_id', 'name', 'address', 'city', 'state',
                'latitude', 'longitude', 'price_per_gallon'
            )
            .distinct()
        )

        if not candidate_stations:
            return []

        # 5. Fast Windowed Projection:
        # Instead of scanning all thousands of route vertices for each station,
        # we identify the closest checkpoint first (O(num_checkpoints)),
        # then only inspect vertices within adjacent checkpoints (O(window_size)).
        lat_scale = 69.0
        num_checkpoints = len(checkpoints)
        num_coords = len(coordinates)
        matched_stations: List[Dict[str, Any]] = []

        for st in candidate_stations:
            st_lat = st['latitude']
            st_lon = st['longitude']
            cos_lat = math.cos(math.radians(st_lat))
            lon_scale = 69.0 * cos_lat

            # Step A: Find closest checkpoint
            best_cp_idx = 0
            min_cp_dist_sq = float('inf')
            for cp_i, cp in enumerate(checkpoints):
                dy = (cp['lat'] - st_lat) * lat_scale
                dx = (cp['lon'] - st_lon) * lon_scale
                d_sq = dx * dx + dy * dy
                if d_sq < min_cp_dist_sq:
                    min_cp_dist_sq = d_sq
                    best_cp_idx = cp_i

            # Step B: Determine vertex window (+/- 1 checkpoint around best checkpoint)
            window_start_cp = max(0, best_cp_idx - 1)
            window_end_cp = min(num_checkpoints - 1, best_cp_idx + 1)
            start_v_idx = checkpoints[window_start_cp]['index']
            end_v_idx = min(num_coords, checkpoints[window_end_cp]['index'] + 1)

            # Step C: Linear sweep only within the candidate vertex window
            best_v_idx = start_v_idx
            min_v_dist_sq = float('inf')
            for v_i in range(start_v_idx, end_v_idx):
                pt = coordinates[v_i]
                dy = (pt[1] - st_lat) * lat_scale
                dx = (pt[0] - st_lon) * lon_scale
                d_sq = dx * dx + dy * dy
                if d_sq < min_v_dist_sq:
                    min_v_dist_sq = d_sq
                    best_v_idx = v_i

            # Step D: Exact Haversine detour calculation
            best_pt = coordinates[best_v_idx]
            detour_dist = haversine_distance(st_lat, st_lon, best_pt[1], best_pt[0])

            # Step E: Filter by corridor radius
            if detour_dist <= corridor_radius_miles:
                route_mile = cumulative_distances[best_v_idx]
                matched_stations.append({
                    'id': st['id'],
                    'opis_id': st['opis_id'],
                    'name': st['name'],
                    'address': st['address'],
                    'city': st['city'],
                    'state': st['state'],
                    'latitude': st_lat,
                    'longitude': st_lon,
                    'price_per_gallon': float(st['price_per_gallon']),
                    'route_mile': round(route_mile, 2),
                    'detour_miles': round(detour_dist, 2),
                })

        # 6. Sort all matched stations by ascending route mile marker
        matched_stations.sort(key=lambda s: s['route_mile'])

        return matched_stations
