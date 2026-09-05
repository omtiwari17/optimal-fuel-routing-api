import logging
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response

from core.serializers import (
    RouteRequestSerializer,
    RouteResponseSerializer,
)
from core.routing_client import (
    RoutingClient,
    RoutingAPIError,
    LocationNotFoundError,
    NoRouteFoundError,
)
from core.polyline_utils import compute_cumulative_distances
from core.station_matcher import StationMatcher
from core.fuel_optimizer import FuelOptimizer, RouteNotFeasibleError

logger = logging.getLogger(__name__)


class RouteAPIView(APIView):
    """
    POST /api/route/

    Calculates the driving route between start and finish locations in the USA,
    identifies optimal fuel stops within a vehicle's 500-mile tank range,
    and returns total distance, fuel consumption, costs, and GeoJSON LineString.
    """

    def post(self, request, *args, **kwargs):
        # 1. Validate request payload
        serializer = RouteRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    'error': 'Invalid request parameters.',
                    'details': serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        validated_data = serializer.validated_data
        start_query = validated_data['start']
        finish_query = validated_data['finish']
        corridor_radius = validated_data.get('corridor_radius_miles', 10.0)

        # 2. Initialize routing client
        try:
            client = RoutingClient()
        except RoutingAPIError as exc:
            logger.error("RoutingClient initialization error: %s", exc)
            return Response(
                {'error': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # 3. Geocode start and finish locations
        try:
            start_coords = client.geocode(start_query)
        except LocationNotFoundError as exc:
            return Response(
                {'error': f"Start location error: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RoutingAPIError as exc:
            return Response(
                {'error': f"Geocoding service error: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        try:
            finish_coords = client.geocode(finish_query)
        except LocationNotFoundError as exc:
            return Response(
                {'error': f"Finish location error: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RoutingAPIError as exc:
            return Response(
                {'error': f"Geocoding service error: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # 4. Fetch driving route from OpenRouteService (strictly 1 directions call)
        try:
            route_data = client.get_route(start_coords, finish_coords)
        except NoRouteFoundError as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RoutingAPIError as exc:
            return Response(
                {'error': f"Routing service error: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        coordinates = route_data['coordinates']
        total_distance = route_data['distance_miles']
        geojson_geometry = route_data['geojson']

        # 5. Compute running cumulative mileage along route vertices
        cumulative_distances = compute_cumulative_distances(coordinates)

        # 6. Find candidate fuel stations along the highway corridor from SQLite
        candidate_stations = StationMatcher.find_stations_along_route(
            coordinates=coordinates,
            cumulative_distances=cumulative_distances,
            corridor_radius_miles=corridor_radius,
        )

        # 7. Optimize fuel stops using the greedy lookahead window algorithm
        try:
            optimization_result = FuelOptimizer.optimize(
                candidate_stations=candidate_stations,
                total_distance_miles=total_distance,
            )
        except RouteNotFeasibleError as exc:
            return Response(
                {
                    'error': 'Route not feasible.',
                    'details': str(exc),
                    'distance_miles': total_distance,
                    'route_geojson': geojson_geometry,
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        # 8. Assemble final response payload
        response_data = {
            'distance_miles': optimization_result['distance_miles'],
            'total_cost': optimization_result['total_cost'],
            'total_gallons': optimization_result['total_gallons'],
            'fuel_stops': optimization_result['fuel_stops'],
            'route_geojson': geojson_geometry,
        }

        return Response(response_data, status=status.HTTP_200_OK)
