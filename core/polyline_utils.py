import math
from typing import List, Tuple, Dict, Any, Optional

# Mean radius of the Earth in miles (WGS-84 standard approximation)
EARTH_RADIUS_MILES = 3958.8


def haversine_distance(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float
) -> float:
    """
    Calculate the great-circle distance between two geographic points
    on the Earth using the Haversine formula.

    Returns the distance in miles.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    sin_half_dphi = math.sin(delta_phi / 2.0)
    sin_half_dlambda = math.sin(delta_lambda / 2.0)

    a = (
        sin_half_dphi * sin_half_dphi
        + math.cos(phi1) * math.cos(phi2) * sin_half_dlambda * sin_half_dlambda
    )
    # Clip 'a' between 0.0 and 1.0 to guard against any float precision anomalies
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return EARTH_RADIUS_MILES * c


def compute_cumulative_distances(
    coordinates: List[List[float]]
) -> List[float]:
    """
    Given an ordered list of GeoJSON coordinates [[lon_0, lat_0], [lon_1, lat_1], ...],
    compute the running cumulative distance from the start in miles for every vertex.

    Returns a list of floats [0.0, d_1, d_2, ..., d_n] where d_i is the
    distance in miles from the origin along the highway polyline.
    """
    if not coordinates:
        return []

    cumulative = [0.0]
    total = 0.0

    for i in range(1, len(coordinates)):
        prev_lon, prev_lat = coordinates[i - 1][0], coordinates[i - 1][1]
        curr_lon, curr_lat = coordinates[i][0], coordinates[i][1]

        leg_dist = haversine_distance(prev_lat, prev_lon, curr_lat, curr_lon)
        total += leg_dist
        cumulative.append(round(total, 4))

    return cumulative


def sample_checkpoints(
    coordinates: List[List[float]],
    cumulative_distances: Optional[List[float]] = None,
    interval_miles: float = 30.0
) -> List[Dict[str, Any]]:
    """
    Sample points along the route polyline at approximately regular intervals (e.g. every 30 miles).
    Always includes the start point and the destination endpoint.

    Each checkpoint is a dictionary:
    {
        'lat': float,
        'lon': float,
        'mile': float,
        'index': int
    }

    These checkpoints allow spatial querying of candidate fuel stations within an
    overlapping corridor radius (e.g. 15-20 miles) along the driving route.
    """
    if not coordinates:
        return []

    if cumulative_distances is None or len(cumulative_distances) != len(coordinates):
        cumulative_distances = compute_cumulative_distances(coordinates)

    total_miles = cumulative_distances[-1]
    checkpoints = []

    # First point: Route start (mile 0.0)
    checkpoints.append({
        'lat': coordinates[0][1],
        'lon': coordinates[0][0],
        'mile': 0.0,
        'index': 0
    })

    next_target = interval_miles
    for i in range(1, len(coordinates)):
        m = cumulative_distances[i]
        if m >= next_target and m < (total_miles - 5.0):
            checkpoints.append({
                'lat': coordinates[i][1],
                'lon': coordinates[i][0],
                'mile': round(m, 2),
                'index': i
            })
            next_target = m + interval_miles

    # Destination point: Route end
    last_idx = len(coordinates) - 1
    if checkpoints[-1]['index'] != last_idx:
        checkpoints.append({
            'lat': coordinates[last_idx][1],
            'lon': coordinates[last_idx][0],
            'mile': round(total_miles, 2),
            'index': last_idx
        })

    return checkpoints


def project_station_onto_route(
    station_lat: float,
    station_lon: float,
    coordinates: List[List[float]],
    cumulative_distances: List[float]
) -> Tuple[float, float, int]:
    """
    Project a fuel station's geographic coordinates onto the driving route polyline.

    To achieve sub-millisecond execution across thousands of route vertices without
    requiring PostGIS or external C libraries, this function uses a two-phase search:
    1. Phase 1 (Fast Pruning): Computes squared Euclidean distance in equirectangular
       projection space (scaling longitude by cos(latitude)). This is a pure algebraic
       operation with zero trigonometric calls inside the loop.
    2. Phase 2 (Exact Haversine): Evaluates the exact spherical Haversine distance for
       the closest matching route vertex.

    Returns:
        (detour_distance_miles, route_mile_marker, nearest_vertex_index)
        - detour_distance_miles: Shortest distance in miles from the station to the route highway.
        - route_mile_marker: Distance from trip origin along the highway where the station is accessed.
        - nearest_vertex_index: Index of the nearest route vertex in the coordinates array.
    """
    if not coordinates:
        return (0.0, 0.0, 0)

    # Precompute cos(latitude) for equirectangular projection at station latitude
    cos_lat = math.cos(math.radians(station_lat))
    # 1 degree latitude is approximately 69.0 miles
    # 1 degree longitude is approximately 69.0 * cos(lat) miles
    lat_scale = 69.0
    lon_scale = 69.0 * cos_lat

    min_dist_sq = float('inf')
    best_idx = 0

    # Phase 1: Fast linear sweep across vertices
    for i, pt in enumerate(coordinates):
        pt_lon, pt_lat = pt[0], pt[1]
        dy = (pt_lat - station_lat) * lat_scale
        dx = (pt_lon - station_lon) * lon_scale
        dist_sq = dx * dx + dy * dy

        if dist_sq < min_dist_sq:
            min_dist_sq = dist_sq
            best_idx = i

    # Phase 2: Exact Haversine calculation for the nearest vertex
    best_pt = coordinates[best_idx]
    exact_detour_dist = haversine_distance(station_lat, station_lon, best_pt[1], best_pt[0])
    route_mile = cumulative_distances[best_idx]

    return (round(exact_detour_dist, 2), round(route_mile, 2), best_idx)
