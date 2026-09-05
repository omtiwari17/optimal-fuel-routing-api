import math
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from core.models import FuelStation
from core.polyline_utils import (
    haversine_distance,
    compute_cumulative_distances,
    sample_checkpoints,
    project_station_onto_route,
    EARTH_RADIUS_MILES,
)
from core.station_matcher import StationMatcher
from core.fuel_optimizer import FuelOptimizer, RouteNotFeasibleError
from core.routing_client import LocationNotFoundError, NoRouteFoundError, RoutingAPIError


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
        self.assertTrue(200.0 < cum_dists[-1] < 215.0)

    def test_sample_checkpoints(self):
        """Verify checkpoints sample at given interval and preserve start/end."""
        coords = [[-90.0, 30.0 + (i * 0.45)] for i in range(10)]
        cum_dists = compute_cumulative_distances(coords)
        total_miles = cum_dists[-1]

        checkpoints = sample_checkpoints(coords, cum_dists, interval_miles=50.0)
        self.assertGreater(len(checkpoints), 1)
        self.assertEqual(checkpoints[0]['mile'], 0.0)
        self.assertEqual(checkpoints[0]['index'], 0)
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

        detour, mile, idx = project_station_onto_route(35.1, -91.0, coords, cum_dists)
        self.assertEqual(idx, 1)
        self.assertTrue(6.0 < detour < 8.0, f"Expected detour ~6.9 miles, got {detour}")
        self.assertEqual(mile, round(cum_dists[1], 2))


class StationMatcherTestCase(TestCase):
    """
    Unit tests for StationMatcher in core/station_matcher.py.
    """

    def setUp(self):
        self.coords = [
            [-90.0, 35.0],
            [-90.5, 35.0],
            [-91.0, 35.0],
            [-91.5, 35.0],
            [-92.0, 35.0],
        ]

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
        self.assertIn(self.st1.id, matched_ids)
        self.assertIn(self.st2.id, matched_ids)
        self.assertNotIn(self.st3.id, matched_ids)

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


class FuelOptimizerTestCase(TestCase):
    """
    Unit tests for FuelOptimizer in core/fuel_optimizer.py.
    """

    def test_short_route_no_stops_needed(self):
        """Trips <= 500 miles require 0 stops because vehicle starts with a full tank."""
        result = FuelOptimizer.optimize(
            candidate_stations=[
                {'id': 1, 'name': 'S1', 'city': 'C1', 'state': 'MO', 'latitude': 37.0, 'longitude': -93.0, 'price_per_gallon': 3.19, 'route_mile': 150.0}
            ],
            total_distance_miles=350.0,
            max_range_miles=500.0,
            mpg=10.0
        )
        self.assertEqual(result['distance_miles'], 350.0)
        self.assertEqual(result['total_gallons'], 35.0)
        self.assertEqual(result['total_cost'], 0.0)
        self.assertEqual(len(result['fuel_stops']), 0)

    def test_two_stop_optimization(self):
        """Route of 950 miles selects cheapest reachable stations."""
        candidates = [
            {'id': 1, 'name': 'A', 'city': 'CA', 'state': 'MO', 'latitude': 37.0, 'longitude': -93.0, 'price_per_gallon': 3.80, 'route_mile': 200.0},
            {'id': 2, 'name': 'B', 'city': 'CB', 'state': 'KS', 'latitude': 38.0, 'longitude': -95.0, 'price_per_gallon': 3.20, 'route_mile': 380.0},
            {'id': 3, 'name': 'C', 'city': 'CC', 'state': 'KS', 'latitude': 38.5, 'longitude': -96.0, 'price_per_gallon': 3.60, 'route_mile': 450.0},
            {'id': 4, 'name': 'D', 'city': 'CD', 'state': 'KS', 'latitude': 39.0, 'longitude': -98.0, 'price_per_gallon': 3.45, 'route_mile': 600.0},
            {'id': 5, 'name': 'E', 'city': 'CE', 'state': 'CO', 'latitude': 39.5, 'longitude': -102.0, 'price_per_gallon': 3.15, 'route_mile': 750.0},
        ]

        result = FuelOptimizer.optimize(
            candidate_stations=candidates,
            total_distance_miles=950.0,
            max_range_miles=500.0,
            mpg=10.0
        )

        self.assertEqual(len(result['fuel_stops']), 2)
        stop1 = result['fuel_stops'][0]
        stop2 = result['fuel_stops'][1]

        self.assertEqual(stop1['name'], 'B')
        self.assertEqual(stop1['distance_from_start_miles'], 380.0)
        self.assertEqual(stop1['gallons_purchased'], 38.0)
        self.assertEqual(stop1['cost'], 121.60)

        self.assertEqual(stop2['name'], 'E')
        self.assertEqual(stop2['distance_from_start_miles'], 750.0)
        self.assertEqual(stop2['leg_distance_miles'], 370.0)
        self.assertEqual(stop2['gallons_purchased'], 37.0)
        self.assertEqual(stop2['cost'], 116.55)

        self.assertEqual(result['total_cost'], 301.15)
        self.assertEqual(result['total_gallons'], 95.0)

    def test_tie_breaking_picks_furthest(self):
        """When two stations in reachable window have identical prices, pick the furthest one."""
        candidates = [
            {'id': 1, 'name': 'Station Near', 'city': 'C1', 'state': 'MO', 'latitude': 37.0, 'longitude': -93.0, 'price_per_gallon': 3.00, 'route_mile': 250.0},
            {'id': 2, 'name': 'Station Far', 'city': 'C2', 'state': 'KS', 'latitude': 38.0, 'longitude': -95.0, 'price_per_gallon': 3.00, 'route_mile': 400.0},
        ]
        result = FuelOptimizer.optimize(
            candidate_stations=candidates,
            total_distance_miles=800.0,
            max_range_miles=500.0,
            mpg=10.0
        )
        self.assertEqual(result['fuel_stops'][0]['name'], 'Station Far')
        self.assertEqual(result['fuel_stops'][0]['distance_from_start_miles'], 400.0)

    def test_gap_exceeds_tank_range_raises_error(self):
        """If gap between reachable stations exceeds 500 miles, raise RouteNotFeasibleError."""
        candidates = [
            {'id': 1, 'name': 'S1', 'city': 'C1', 'state': 'MO', 'latitude': 37.0, 'longitude': -93.0, 'price_per_gallon': 3.20, 'route_mile': 300.0},
            {'id': 2, 'name': 'S2', 'city': 'C2', 'state': 'CO', 'latitude': 39.0, 'longitude': -102.0, 'price_per_gallon': 3.10, 'route_mile': 850.0},
        ]
        with self.assertRaises(RouteNotFeasibleError):
            FuelOptimizer.optimize(
                candidate_stations=candidates,
                total_distance_miles=1000.0,
                max_range_miles=500.0,
                mpg=10.0
            )

    def test_no_stations_on_long_route_raises_error(self):
        """Long route with zero stations must raise RouteNotFeasibleError."""
        with self.assertRaises(RouteNotFeasibleError):
            FuelOptimizer.optimize(
                candidate_stations=[],
                total_distance_miles=800.0,
                max_range_miles=500.0,
                mpg=10.0
            )


class RouteAPITestCase(TestCase):
    """
    End-to-end API tests for POST /api/route/ endpoint.
    """

    def setUp(self):
        self.client = APIClient()
        self.url = '/api/route/'

    def test_missing_payload_returns_400(self):
        """POST with missing or empty payload returns 400 Bad Request."""
        response = self.client.post(self.url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('details', response.data)

    def test_identical_start_finish_returns_400(self):
        """POST with identical start and finish returns 400 Bad Request."""
        response = self.client.post(
            self.url,
            {'start': 'Denver, CO', 'finish': 'Denver, CO'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('details', response.data)

    @patch('core.views.RoutingClient')
    def test_location_not_found_returns_400(self, mock_routing_client_cls):
        """When an address cannot be geocoded, return 400 Bad Request."""
        mock_instance = MagicMock()
        mock_instance.geocode.side_effect = LocationNotFoundError("Could not find location.")
        mock_routing_client_cls.return_value = mock_instance

        response = self.client.post(
            self.url,
            {'start': 'FakeUnknownCity12345', 'finish': 'Denver, CO'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    @patch('core.views.RoutingClient')
    def test_no_route_found_returns_400(self, mock_routing_client_cls):
        """When routing engine cannot connect points by road, return 400 Bad Request."""
        mock_instance = MagicMock()
        mock_instance.geocode.side_effect = [(21.3069, -157.8583), (37.7749, -122.4194)]
        mock_instance.get_route.side_effect = NoRouteFoundError("No driving route.")
        mock_routing_client_cls.return_value = mock_instance

        response = self.client.post(
            self.url,
            {'start': 'Honolulu, HI', 'finish': 'San Francisco, CA'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    @patch('core.views.RoutingClient')
    def test_successful_route_api_call(self, mock_routing_client_cls):
        """End-to-end test of successful POST /api/route/ response structure."""
        # Create candidate stations along the route ensuring <= 500 mi gaps
        FuelStation.objects.create(
            opis_id=5001,
            name="Test Express Stop 1",
            address="Exit 100",
            city="Midway",
            state="KS",
            price_per_gallon=Decimal("3.159"),
            latitude=38.5,
            longitude=-97.0
        )
        FuelStation.objects.create(
            opis_id=5002,
            name="Test Express Stop 2",
            address="Exit 250",
            city="Colby",
            state="KS",
            price_per_gallon=Decimal("3.059"),
            latitude=39.2,
            longitude=-101.5
        )

        mock_instance = MagicMock()
        mock_instance.geocode.side_effect = [
            (37.2, -93.3),  # Springfield, MO
            (39.7, -105.0), # Denver, CO
        ]
        mock_instance.get_route.return_value = {
            'distance_miles': 680.0,
            'duration_hours': 10.5,
            'geojson': {
                'type': 'LineString',
                'coordinates': [
                    [-93.3, 37.2],
                    [-97.0, 38.5],
                    [-101.5, 39.2],
                    [-105.0, 39.7],
                ]
            },
            'coordinates': [
                [-93.3, 37.2],
                [-97.0, 38.5],
                [-101.5, 39.2],
                [-105.0, 39.7],
            ]
        }
        mock_routing_client_cls.return_value = mock_instance

        response = self.client.post(
            self.url,
            {'start': 'Springfield, MO', 'finish': 'Denver, CO'},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertIn('distance_miles', data)
        self.assertIn('total_cost', data)
        self.assertIn('total_gallons', data)
        self.assertIn('fuel_stops', data)
        self.assertIn('route_geojson', data)
        self.assertEqual(data['distance_miles'], 680.0)
        self.assertEqual(data['total_gallons'], 68.0)
        self.assertEqual(data['route_geojson']['type'], 'LineString')
