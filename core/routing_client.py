import re
import requests
from django.conf import settings


class RoutingAPIError(Exception):
    """Base exception for external routing and geocoding failures."""
    pass


class LocationNotFoundError(RoutingAPIError):
    """Raised when an address or city cannot be resolved to coordinates."""
    pass


class NoRouteFoundError(RoutingAPIError):
    """Raised when no driving route can be found between coordinates."""
    pass


class RoutingClient:
    """
    Client for OpenRouteService (ORS) APIs.
    - Geocodes start and finish locations (restricted to the USA).
    - Calls the Directions API strictly once per route request.
    """
    GEOCODE_URL = 'https://api.openrouteservice.org/geocode/search'
    DIRECTIONS_URL = 'https://api.openrouteservice.org/v2/directions/driving-car/geojson'
    TIMEOUT = 15  # seconds
    METERS_TO_MILES = 1.0 / 1609.344

    def __init__(self, api_key: str = None):
        self.api_key = api_key or getattr(settings, 'ORS_API_KEY', '')
        if not self.api_key:
            raise RoutingAPIError(
                "OpenRouteService API key is missing. Please set ORS_API_KEY in your .env file."
            )

    def geocode(self, query: str) -> tuple[float, float]:
        """
        Geocodes a location query (e.g. 'Springfield, MO') into (latitude, longitude).
        If the query is already formatted as 'lat, lon', parses it directly without an API call.
        """
        query = query.strip()

        # 1. Fast check: is query already "lat, lon" or "lat,lon"?
        coord_match = re.match(r'^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$', query)
        if coord_match:
            lat = float(coord_match.group(1))
            lon = float(coord_match.group(2))
            if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                return lat, lon

        # 2. Call OpenRouteService Geocoding API
        params = {
            'api_key': self.api_key,
            'text': query,
            'size': 1,
            'boundary.country': 'USA',  # Restrict results strictly to the United States
        }

        try:
            response = requests.get(self.GEOCODE_URL, params=params, timeout=self.TIMEOUT)
        except requests.RequestException as exc:
            raise RoutingAPIError(f"Geocoding request failed due to network error: {exc}") from exc

        if response.status_code == 401 or response.status_code == 403:
            raise RoutingAPIError("OpenRouteService authentication failed. Check your ORS_API_KEY.")

        if response.status_code != 200:
            raise RoutingAPIError(f"Geocoding API error (HTTP {response.status_code}): {response.text}")

        data = response.json()
        features = data.get('features', [])
        if not features:
            raise LocationNotFoundError(f"Could not resolve location: '{query}'. Please provide a valid US city/state.")

        # ORS returns coordinates in GeoJSON format: [longitude, latitude]
        coords = features[0]['geometry']['coordinates']
        lon, lat = float(coords[0]), float(coords[1])
        return lat, lon

    def get_route(self, start_coords: tuple[float, float], finish_coords: tuple[float, float]) -> dict:
        """
        Calls OpenRouteService Directions API strictly ONCE to retrieve driving route.
        
        Args:
            start_coords: (latitude, longitude) of starting point.
            finish_coords: (latitude, longitude) of destination.
            
        Returns:
            dict containing:
                - distance_miles (float): Total driving distance in miles.
                - duration_hours (float): Estimated travel time in hours.
                - geojson (dict): GeoJSON LineString geometry {"type": "LineString", "coordinates": [[lon, lat], ...]}.
                - coordinates (list): List of [lon, lat] points along the route.
        """
        start_lat, start_lon = start_coords
        finish_lat, finish_lon = finish_coords

        # OpenRouteService expects [[start_lon, start_lat], [finish_lon, finish_lat]]
        payload = {
            'coordinates': [
                [start_lon, start_lat],
                [finish_lon, finish_lat]
            ]
        }

        headers = {
            'Authorization': self.api_key,
            'Content-Type': 'application/json',
        }

        try:
            response = requests.post(
                self.DIRECTIONS_URL,
                json=payload,
                headers=headers,
                timeout=self.TIMEOUT
            )
        except requests.RequestException as exc:
            raise RoutingAPIError(f"Routing request failed due to network error: {exc}") from exc

        if response.status_code == 401 or response.status_code == 403:
            raise RoutingAPIError("OpenRouteService authentication failed. Check your ORS_API_KEY.")

        if response.status_code in (400, 404):
            error_data = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
            msg = error_data.get('error', {}).get('message', response.text)
            raise NoRouteFoundError(f"No driving route found between start and finish: {msg}")

        if response.status_code != 200:
            raise RoutingAPIError(f"Routing API error (HTTP {response.status_code}): {response.text}")

        data = response.json()
        features = data.get('features', [])
        if not features:
            raise NoRouteFoundError("No route returned by the routing engine.")

        route_feature = features[0]
        summary = route_feature.get('properties', {}).get('summary', {})
        distance_meters = summary.get('distance', 0.0)
        duration_seconds = summary.get('duration', 0.0)

        distance_miles = round(distance_meters * self.METERS_TO_MILES, 2)
        duration_hours = round(duration_seconds / 3600.0, 2)
        geometry = route_feature.get('geometry', {})
        coordinates = geometry.get('coordinates', [])

        return {
            'distance_miles': distance_miles,
            'duration_hours': duration_hours,
            'geojson': geometry,
            'coordinates': coordinates,
        }
