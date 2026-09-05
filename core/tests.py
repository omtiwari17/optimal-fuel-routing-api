import math
from django.test import TestCase
from core.polyline_utils import (
    haversine_distance,
    compute_cumulative_distances,
    sample_checkpoints,
    project_station_onto_route,
    EARTH_RADIUS_MILES,
)


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
