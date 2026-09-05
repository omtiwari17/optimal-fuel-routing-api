import math
from decimal import Decimal
from django.test import TestCase
from core.models import FuelStation
from core.polyline_utils import (
    haversine_distance,
    compute_cumulative_distances,
    sample_checkpoints,
    project_station_onto_route,
    EARTH_RADIUS_MILES,
)
from core.station_matcher import StationMatcher


class PolylineUtilsTestCase(TestCase):
    """
    Unit tests for geometric and polyline sampling utilities in core/polyline_utils.py.
    """

    def test_haversine_same_point(self):
        """Distance between a point and itself must be zero."""
        dist = haversine_distance(40.7128, -74.0060, 40.7128, -74.0060)
        self.assertAlmostEqual(dist, 0.0, places=4)

    def test_haversine_known_distance(self):
        """
        Known geodesic distance between NYC (40.7128, -74.0060)
        and Philadelphia (39.9526, -75.1652) is ~80.5 miles.
        """
        nyc_lat, nyc_lon = 40.7128, -74.0060
        philly_lat, philly_lon = 39.9526, -75.1652
        dist = haversine_distance(nyc_lat, nyc_lon, philly_lat, philly_lon)
        self.assertTrue(79.0 < dist < 82.0, f"Expected ~80.5 miles, got {dist}")

    def test_compute_cumulative_distances_empty(self):
        """Empty coordinates should return empty list."""
        self.assertEqual(compute_cumulative_distances([]), [])

    def test_compute_cumulative_distances_single(self):
        """Single point should return [0.0]."""
        self.assertEqual(compute_cumulative_distances([[-74.0060, 40.7128]]), [0.0])

    def test_compute_cumulative_distances_linear(self):
        """Cumulative distance must be monotonically increasing."""
        # A straight line of 4 points moving North along longitude -90
        coords = [
            [-90.0, 30.0],
            [-90.0, 31.0],
            [-90.0, 32.0],
            [-90.0, 33.0],
        ]
        cum_dists = compute_cumulative_distances(coords)
        self.assertEqual(len(cum_dists), 4)
        self.assertEqual(cum_dists[0], 0.0)
        for i in range(1, len(cum_dists)):
            self.assertGreater(cum_dists[i], cum_dists[i - 1])
        # 1 degree of latitude is roughly 69 miles, so 3 degrees should be ~207 miles
        self.assertTrue(200.0 < cum_dists[-1] < 215.0)

    def test_sample_checkpoints(self):
        """Verify checkpoints sample at given interval and preserve start/end."""
        # 10 points spaced roughly 30 miles apart
        coords = [[-90.0, 30.0 + (i * 0.45)] for i in range(10)]
        cum_dists = compute_cumulative_distances(coords)
        total_miles = cum_dists[-1]

        checkpoints = sample_checkpoints(coords, cum_dists, interval_miles=50.0)
        self.assertGreater(len(checkpoints), 1)
        # Start checkpoint
        self.assertEqual(checkpoints[0]['mile'], 0.0)
        self.assertEqual(checkpoints[0]['index'], 0)
        # End checkpoint
        self.assertEqual(checkpoints[-1]['mile'], round(total_miles, 2))
        self.assertEqual(checkpoints[-1]['index'], len(coords) - 1)

    def test_project_station_onto_route_exact_hit(self):
        """If station is exactly on a route vertex, detour distance should be ~0.0."""
        coords = [
            [-90.0, 35.0],
            [-91.0, 35.5],
            [-92.0, 36.0],
        ]
        cum_dists = compute_cumulative_distances(coords)

        # Station at vertex 1: lat=35.5, lon=-91.0
        detour, mile, idx = project_station_onto_route(35.5, -91.0, coords, cum_dists)
        self.assertEqual(idx, 1)
        self.assertAlmostEqual(detour, 0.0, places=1)
        self.assertEqual(mile, round(cum_dists[1], 2))

    def test_project_station_onto_route_detour(self):
        """Station offset from route should return accurate detour and route mile."""
        coords = [
            [-90.0, 35.0],
            [-91.0, 35.0],
            [-92.0, 35.0],
        ]
        cum_dists = compute_cumulative_distances(coords)

        # Station slightly North of vertex 1: lat=35.1, lon=-91.0 (~6.9 miles North)
        detour, mile, idx = project_station_onto_route(35.1, -91.0, coords, cum_dists)
        self.assertEqual(idx, 1)
        self.assertTrue(6.0 < detour < 8.0, f"Expected detour ~6.9 miles, got {detour}")
        self.assertEqual(mile, round(cum_dists[1], 2))


class StationMatcherTestCase(TestCase):
    """
    Unit tests for StationMatcher in core/station_matcher.py.
    """

    def setUp(self):
        # Create a test route: moving East to West along lat 35.0
        # Longitudes -90.0 to -92.0 (~113 miles)
        self.coords = [
            [-90.0, 35.0],
            [-90.5, 35.0],
            [-91.0, 35.0],
            [-91.5, 35.0],
            [-92.0, 35.0],
        ]

        # Station 1: Near mile ~28 (on route)
        self.st1 = FuelStation.objects.create(
            opis_id=1001,
            name="Station Alpha",
            address="100 Highway 64",
            city="Marion",
            state="AR",
            price_per_gallon=Decimal("3.199"),
            latitude=35.0,
            longitude=-90.5
        )

        # Station 2: Near mile ~56 (5 miles North of route)
        # 1 deg lat ~ 69 miles -> 0.07 deg lat ~ 4.8 miles
        self.st2 = FuelStation.objects.create(
            opis_id=1002,
            name="Station Beta",
            address="200 North Road",
            city="Wynne",
            state="AR",
            price_per_gallon=Decimal("2.999"),
            latitude=35.07,
            longitude=-91.0
        )

        # Station 3: Far away (50 miles North of route) -> must be excluded
        self.st3 = FuelStation.objects.create(
            opis_id=1003,
            name="Station Gamma",
            address="300 Far Away Blvd",
            city="Jonesboro",
            state="AR",
            price_per_gallon=Decimal("2.500"),
            latitude=35.75,
            longitude=-91.0
        )

    def test_empty_coordinates_returns_empty(self):
        """StationMatcher should return empty list for empty or single coordinates."""
        self.assertEqual(StationMatcher.find_stations_along_route([]), [])
        self.assertEqual(StationMatcher.find_stations_along_route([[-90.0, 35.0]]), [])

    def test_find_stations_within_corridor(self):
        """Stations within corridor radius are found; distant stations are excluded."""
        matched = StationMatcher.find_stations_along_route(
            self.coords,
            corridor_radius_miles=10.0,
            checkpoint_interval_miles=25.0
        )

        matched_ids = [m['id'] for m in matched]
        self.assertIn(self.st1.id, matched_ids, "Station Alpha on route should be matched")
        self.assertIn(self.st2.id, matched_ids, "Station Beta (5 mi off route) should be matched")
        self.assertNotIn(self.st3.id, matched_ids, "Station Gamma (50 mi away) must be excluded")

    def test_stations_sorted_by_route_mile(self):
        """Matched stations must be strictly sorted by ascending route_mile."""
        matched = StationMatcher.find_stations_along_route(
            self.coords,
            corridor_radius_miles=10.0,
            checkpoint_interval_miles=25.0
        )
        self.assertEqual(len(matched), 2)
        self.assertLess(matched[0]['route_mile'], matched[1]['route_mile'])
        self.assertEqual(matched[0]['name'], "Station Alpha")
        self.assertEqual(matched[1]['name'], "Station Beta")

    def test_detour_distance_accuracy(self):
        """Detour distance of station on route should be near zero; offset station should reflect distance."""
        matched = StationMatcher.find_stations_along_route(
            self.coords,
            corridor_radius_miles=10.0,
            checkpoint_interval_miles=25.0
        )
        alpha = next(s for s in matched if s['name'] == "Station Alpha")
        beta = next(s for s in matched if s['name'] == "Station Beta")

        self.assertAlmostEqual(alpha['detour_miles'], 0.0, places=1)
        self.assertTrue(4.0 < beta['detour_miles'] < 6.0, f"Expected ~4.8 mi, got {beta['detour_miles']}")
