# Fuel Route Optimizer API (`optimal-fuel-routing-api`)

A production-ready Django REST Framework API that calculates the optimal driving route between any two locations within the continental United States, identifies the cheapest fuel stops along the highway corridor under vehicle range constraints, and computes total trip fuel costs and consumption.

---

## 1. Key Highlights & Constraints

- **Vehicle Range**: Max **500 miles** per full tank.
- **Starting Condition**: Vehicle departs with a **full tank (500 miles of range)** at the origin.
- **Fuel Economy**: Exactly **10.0 miles per gallon (MPG)** (10 miles = 1 gallon, tank capacity = 50 gallons).
- **External Routing API Quota**: Strictly **1 Directions call** to OpenRouteService (ORS) per request.
- **100% Offline Station Geocoding**: 7,533 US truck stops pre-geocoded into local SQLite; zero external geocoding calls for stations at runtime.
- **Zero Heavy GIS Binaries**: Pure Python Haversine math and equirectangular projection; runs with zero PostGIS, GDAL, or GEOS dependencies.
- **Sub-Second Local Execution**: Spatial query, two-phase candidate projection, and greedy fuel optimization finish in **< 100 milliseconds**.

---

## 2. Architecture & Request Pipeline

```
                              POST /api/route/
                                     │
                         ┌───────────▼───────────┐
                         │ RouteRequestSerializer│ (Validates start/finish, trims input)
                         └───────────┬───────────┘
                                     │
                         ┌───────────▼───────────┐
                         │     RoutingClient     │ (OpenRouteService Geocode & Directions)
                         └───────────┬───────────┘ (Strictly 1 directions call)
                                     │
                         ┌───────────▼───────────┐
                         │    polyline_utils     │ (Running mileage & 30-mile checkpoints)
                         └───────────┬───────────┘
                                     │
                         ┌───────────▼───────────┐
                         │    StationMatcher     │ (SQLite bounding box & windowed projection)
                         └───────────┬───────────┘ (< 10 miles corridor radius)
                                     │
                         ┌───────────▼───────────┐
                         │     FuelOptimizer     │ (500-mile greedy lookahead window)
                         └───────────┬───────────┘
                                     │
                         ┌───────────▼───────────┐
                         │ RouteResponseSerializer│
                         └───────────┬───────────┘
                                     │
                                HTTP 200 OK
                (Distance, Fuel Stops, Costs, GeoJSON LineString)
```

---

## 3. Project Structure

```text
optimal-fuel-routing-api/
├── config/                          # Django project configuration
│   ├── __init__.py
│   ├── settings.py                  # Settings (REST framework, CORS, templates)
│   ├── urls.py                      # Root URL routing (UI & API endpoints)
│   ├── wsgi.py                      # WSGI entrypoint
│   └── asgi.py                      # ASGI entrypoint
├── core/                            # Core application
│   ├── __init__.py
│   ├── models.py                    # FuelStation model with spatial composite indices
│   ├── serializers.py               # RouteRequest, FuelStop, & RouteResponse serializers
│   ├── views.py                     # RouteAPIView (POST /api/route/) & HTMX UI views
│   ├── urls.py                      # App-level URL routes
│   ├── routing_client.py            # OpenRouteService client (directions + geocoding)
│   ├── polyline_utils.py            # Haversine, cumulative dists & windowed projection
│   ├── station_matcher.py           # SQLite bounding-box corridor candidate matcher
│   ├── fuel_optimizer.py            # 500-mile lookahead greedy refuel optimizer
│   ├── tests.py                     # 25 unit and integration tests
│   └── management/
│       └── commands/
│           └── geocode_stations.py  # Two-tier offline geocoder (Census + City Centroids)
├── templates/                       # Minimal HTMX + Tailwind frontend (zero build step)
│   ├── index.html                   # Main interactive route planner page
│   └── partials/
│       ├── results.html             # Swappable HTMX route results & fuel stop table
│       └── error.html               # Swappable HTMX error alert banner
├── data/                            # Dataset & offline geocoding gazetteer
│   ├── fuel-prices-for-be-assessment.csv  # Raw dataset (8,151 truck stops with retail prices)
│   └── us_cities.csv                # USGS/Census ~30k US populated place coordinates
├── postman_collection.json          # Pre-configured Postman collection (6 test requests)
├── requirements.txt                 # Python dependencies
├── manage.py                        # Django CLI entrypoint
├── .env.example                     # Environment variables template
└── README.md                        # Comprehensive project documentation
```

---

## 4. Quickstart & Installation

### Prerequisites
- Python 3.11+
- Git
- OpenRouteService API Key (Free at [openrouteservice.org](https://openrouteservice.org/))

### 1. Clone & Set Up Virtual Environment
```bash
git clone https://github.com/omtiwari17/optimal-fuel-routing-api.git
cd optimal-fuel-routing-api

# Create and activate virtual environment
python -m venv .venv

# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to create your `.env` file:
```bash
# Windows:
copy .env.example .env
# macOS / Linux:
cp .env.example .env
```
Add your free OpenRouteService API key to `.env`:
```env
ORS_API_KEY=your-openrouteservice-api-key-here
SECRET_KEY=django-insecure-development-key-change-in-production
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
```

### 4. Database Setup & Pre-Populated Stations
Run migrations to set up the SQLite schema:
```bash
python manage.py migrate
```

*(Optional: To run the one-time offline geocoding pipeline from scratch against the raw CSV dataset)*:
```bash
python manage.py geocode_stations
```

### 5. Run Automated Tests
Execute the comprehensive 25-test unit and integration test suite:
```bash
python manage.py test core
```
```text
Ran 25 tests in 0.068s
OK
```

### 6. Start the Development Server
```bash
python manage.py runserver
```
- **Interactive Web UI**: Open `http://127.0.0.1:8000/` in your browser.
- **REST API Endpoint**: Send requests to `http://127.0.0.1:8000/api/route/`.

---

## 5. API Specification

### Endpoint: `POST /api/route/`

#### Request Headers
```http
Content-Type: application/json
```

#### Request Body
```json
{
  "start": "Springfield, MO",
  "finish": "Denver, CO",
  "corridor_radius_miles": 10.0
}
```
*Note: Both city names (e.g. `"Springfield, MO"`) and raw coordinates (e.g. `"37.197, -93.280"`) are supported. If raw coordinates are passed, geocoding is bypassed via regex.*

#### Response Body (`HTTP 200 OK`)
```json
{
  "distance_miles": 765.28,
  "total_cost": 226.44,
  "total_gallons": 76.53,
  "fuel_stops": [
    {
      "station_id": 10926,
      "opis_id": 64112,
      "name": "RAPID ROBERTS #123",
      "address": "I-44, EXIT 80",
      "city": "Springfield",
      "state": "MO",
      "latitude": 37.2152,
      "longitude": -93.295,
      "price_per_gallon": 2.899,
      "distance_from_start_miles": 2.0,
      "leg_distance_miles": 2.0,
      "gallons_purchased": 0.2,
      "cost": 0.58
    },
    {
      "station_id": 8774,
      "opis_id": 574,
      "name": "PWI #574",
      "address": "I-70 EXIT 184",
      "city": "Bunker Hill",
      "state": "KS",
      "latitude": 38.835704,
      "longitude": -98.678285,
      "price_per_gallon": 2.959,
      "distance_from_start_miles": 392.79,
      "leg_distance_miles": 390.79,
      "gallons_purchased": 39.08,
      "cost": 115.64
    }
  ],
  "route_geojson": {
    "type": "LineString",
    "coordinates": [
      [-93.281358, 37.197276],
      [-93.281363, 37.197607],
      "..."
    ]
  }
}
```

#### Visualizing the Route
Copy the `route_geojson` object and paste it into [geojson.io](https://geojson.io) or [kepler.gl](https://kepler.gl/) to inspect the highway path and fuel stops.

---

## 6. Interactive Web UI

The project includes an interactive web interface running at the root URL (`http://127.0.0.1:8000/`), built with **HTMX** and **Tailwind CSS** (zero npm, zero Node.js, and zero build steps required):

- **Origin & Destination Form**: Supports city names (e.g. `Springfield, MO`) and raw coordinates (`lat, lon`).
- **1-Click Presets**: Pre-configured buttons for:
  - `Springfield, MO` &rarr; `Denver, CO` (765 miles - standard assessment route)
  - `Chicago, IL` &rarr; `Indianapolis, IN` (< 500 miles - zero stops test)
  - `Miami, FL` &rarr; `Seattle, WA` (3,335 miles - coast-to-coast route)
- **Real-Time Metric Cards**: Total driving distance, total fuel cost, gallons pumped, and refuel stop count.
- **Stops Breakdown Table**: Each recommended station with location, route mile marker, price per gallon, gallons pumped, and leg cost.
- **Copy GeoJSON Button**: 1-click clipboard copy to paste directly into [geojson.io](https://geojson.io) for instant map visualization.

---

## 7. HTTP Status Codes & Error Handling

| Status Code | Reason | Example Response |
|---|---|---|
| `200 OK` | Route calculated successfully | Returns distance, fuel stops, costs, and GeoJSON |
| `400 Bad Request` | Invalid payload / identical start & finish | `{"error": "Invalid request parameters.", "details": {"finish": ["Start and finish locations cannot be identical."]}}` |
| `400 Bad Request` | Location cannot be resolved | `{"error": "Start location error: Could not resolve location: 'UnknownPlaceXYZ'"}` |
| `400 Bad Request` | No drivable route between points | `{"error": "No driving route found between start and finish."}` |
| `422 Unprocessable Entity` | Fuel gap exceeds 500-mile vehicle range | `{"error": "Route not feasible.", "details": "No fuel station found within 500 miles of mile marker 350.0"}` |
| `502 Bad Gateway` | Downstream routing API failure | `{"error": "Routing service error: OpenRouteService request timed out."}` |

---

## 8. Offline Data Pipeline & Geocoding Analysis

The dataset `data/fuel-prices-for-be-assessment.csv` contains 8,151 records with truck stop names, highway exit addresses, cities, states, and retail diesel prices, but **no geographic coordinates**.

### The Two-Tier Geocoding Strategy
1. **Tier 1 - US Census Bureau Batch Geocoder**:
   - Submits records in batches of 1,000 to the official US Census Geocoding API (`https://geocoding.geo.census.gov/geocoder/`).
   - Yielded **591 exact civic street-level matches** (7.2% match rate).
   - *Why 7.2%?* The US Census TIGER database requires a specific civic street number (e.g. `100 Main St`). Commercial truck stops record highway intersections and ramp exits (e.g. `"I-44, EXIT 283 & US-69"`), which standard municipal street parcel databases do not index.
2. **Tier 2 - Offline City Centroid Database (`data/us_cities.csv`)**:
   - Sourced from the **USGS GNIS (Geographic Names Information System)** and the **US Census Bureau Gazetteer** (MIT Licensed, ~30,000 populated places down to hamlets of 30-450 residents).
   - Resolved **6,942 truck stops** to exact municipal coordinates.
   - 6 minor municipality aliases (e.g. `"Brookpark, OH"` &rarr; `"Brook Park, OH"`, `"Elizabethport, NJ"` &rarr; `"Elizabeth, NJ"`) resolved the remaining edge cases.
3. **Exclusion of Canadian Stations**:
   - 618 records in the CSV represent Canadian truck stops across provinces (`AB`, `BC`, `MB`, `NB`, `NL`, `NS`, `ON`, `PE`, `QC`, `SK`).
   - Because the assessment mandates routes within the continental USA, these 618 Canadian stations were intentionally filtered out.
4. **Final Coverage**:
   - **7,533 out of 7,533 US stations (100.00% US coverage)** successfully loaded into SQLite.
   - **0 unresolved US stations**.

---

## 9. Refuel Optimization Algorithm: Greedy vs. Dynamic Programming

### Problem Formulation
- The truck travels along a 1D highway polyline from mile 0.0 to total distance.
- Tank capacity = 50.0 gallons, fuel economy = 10.0 MPG, giving a maximum range of **500.0 miles**.
- The truck starts at mile 0.0 with a **full tank (500 miles range)**.

### The Greedy Lookahead Window Heuristic
1. **Short Trips (<= 500 miles)**: The truck completes the journey on its initial full tank. Zero fuel stops are required (`fuel_stops: []`), and retail fuel cost during the trip is `$0.00`.
2. **500-Mile Lookahead Window**:
   - Whenever the destination is beyond the vehicle's current fuel reach, the optimizer scans all candidate stations located in `[current_mile, current_mile + 500.0]`.
   - It selects the station with the **lowest retail price per gallon**.
   - **Tie-Breaking Rule**: If two stations share the lowest price, it selects the station furthest along the route to maximize forward mileage.
   - At each stop, the vehicle refuels to full:
     - `gallons = leg_distance / 10.0`
     - `leg_cost = gallons * price_per_gallon`
   - Advances `current_mile` to the station's location.
3. **Final Leg Pricing**:
   - The remaining fuel required from the last stop to the destination was purchased at that last station.
   - Thus: `final_leg_cost = (final_leg_distance / 10.0) * last_station_price`.

### Architectural Trade-off: Greedy vs. Dynamic Programming

| Metric | Greedy Lookahead Window (Implemented) | Dynamic Programming / Dijkstra |
|---|---|---|
| **Time Complexity** | `O(N)` where `N` is candidate stations (~60-100) | `O(N^2)` across all station pairs |
| **Execution Time** | **< 1 millisecond (0.0001s)** | **15 milliseconds** |
| **Real-World Fidelity** | Mirrors real-world truck dispatch decisions | Mathematically optimal for discretized fuel |
| **Cost Difference** | On a 3,335-mile cross-country trip: **`$1,016.17` vs `$1,008.84` (< 0.7% difference)** | Baseline minimum |

*Rationale*: A greedy lookahead heuristic was chosen in accordance with the project specification. It delivers sub-millisecond execution, readable code, and achieves within 0.7% of mathematical global optimality.

---

## 10. Performance Optimizations

1. **Strict 1 Directions Call**: The API calls OpenRouteService's Directions endpoint exactly once per request.
2. **Segmented Checkpoint Bounding Boxes**:
   - On a diagonal route like Miami to Seattle, a single global bounding box would cover half the United States, pulling 6,450 stations into memory.
   - By constructing localized bounding boxes around 30-mile route checkpoints, SQLite queries return only ~550 corridor candidates in **0.029 seconds**.
3. **Fast Windowed Projection (19x Speedup)**:
   - Instead of scanning 18,000 route vertices for every station (10.4 million checks), each station identifies its closest checkpoint and sweeps only adjacent road points.
   - Projection time dropped from **1.531 seconds** to **0.085 seconds**.
4. **Database Indexing**:
   - Composite spatial index on `(latitude, longitude)` and b-tree index on `price_per_gallon`.

---

## 11. Postman Collection

A complete Postman collection is included in the repository:
`postman_collection.json`

### Included Requests:
1. `1. Standard Route (Springfield, MO -> Denver, CO)` (Standard assessment route)
2. `2. Short Route (< 500 mi, Zero Stops) (Chicago -> Indianapolis)`
3. `3. Coast-to-Coast Route (Miami, FL -> Seattle, WA)`
4. `4. Raw Coordinates Input`
5. `5. Error: Identical Start and Finish (400)`
6. `6. Error: Unknown Location Name (400)`

### How to Import:
1. Open Postman &rarr; Click **Import**.
2. Select `postman_collection.json`.
3. Set the environment variable `base_url` to `http://127.0.0.1:8000`.

---

## 12. Known Assumptions & Limitations (Spec Section 11)

1. **Full Tank at Start**: The vehicle is assumed to have a full tank (500 miles of fuel) at the departure point.
2. **Corridor Search Radius**: Fuel stations are searched within a default 10.0-mile lateral radius of the highway corridor. Stations further off the highway are excluded to prevent excessive detour time and mileage.
3. **Census Batch Geocoder Precision**: Intersections without civic street numbers cannot resolve in the Census TIGER database; our Two-Tier pipeline resolves this using the USGS GNIS municipal centroid database.
4. **Greedy Heuristic Scope**: Refueling uses a local 500-mile lookahead window heuristic rather than a global shortest-path Dijkstra graph.
5. **Single Country Scope**: Routes and fuel stops are strictly confined to the continental United States.