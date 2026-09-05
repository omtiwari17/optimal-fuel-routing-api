from typing import List, Dict, Any, Optional


class RouteNotFeasibleError(Exception):
    """Raised when a route cannot be completed within vehicle fuel range constraints."""
    pass


class FuelOptimizer:
    """
    Optimizes fuel stop selection along a 1D highway route using a Greedy Lookahead Window.

    Rules & Assumptions:
    - The vehicle starts at mile 0.0 with a FULL tank (default 500.0 miles range).
    - Fuel economy is exactly 10.0 miles per gallon (MPG).
    - Tank capacity is 50.0 gallons (500 miles / 10 MPG).
    - When vehicle range cannot reach the destination directly, it searches all reachable
      stations in the [current_mile, current_mile + max_range] window and chooses the
      cheapest station.
    - If multiple stations share the lowest price, it breaks ties by choosing the station
      furthest along the route to maximize forward progress.
    - At each stop, the vehicle refuels to full, purchasing the gallons consumed since the
      last stop (leg_distance / 10.0).
    - For the final leg from the last stop to the destination, fuel cost is calculated using
      the price per gallon at the last station fueled at.
    """

    DEFAULT_MAX_RANGE_MILES = 500.0
    DEFAULT_MPG = 10.0

    @classmethod
    def optimize(
        cls,
        candidate_stations: List[Dict[str, Any]],
        total_distance_miles: float,
        max_range_miles: float = DEFAULT_MAX_RANGE_MILES,
        mpg: float = DEFAULT_MPG,
    ) -> Dict[str, Any]:
        """
        Calculates optimal fuel stops, fuel quantities, and total trip cost.

        Args:
            candidate_stations: Ordered list of stations along the route corridor
                                (must have 'route_mile' and 'price_per_gallon').
            total_distance_miles: Total driving distance of the route in miles.
            max_range_miles: Maximum range on a full tank (default: 500.0 miles).
            mpg: Vehicle fuel economy in miles per gallon (default: 10.0).

        Returns:
            Dict containing:
            - distance_miles: float
            - total_cost: float
            - total_gallons: float
            - fuel_stops: List of fuel stop dicts

        Raises:
            RouteNotFeasibleError: If a gap between reachable stations exceeds max_range_miles.
        """
        total_distance = round(total_distance_miles, 2)
        total_gallons = round(total_distance / mpg, 2)

        # Edge Case 1: Route is within starting tank range (no stops needed)
        if total_distance <= max_range_miles:
            return {
                'distance_miles': total_distance,
                'total_cost': 0.0,
                'total_gallons': total_gallons,
                'fuel_stops': [],
            }

        if not candidate_stations:
            raise RouteNotFeasibleError(
                f"Trip distance ({total_distance} miles) exceeds fuel range ({max_range_miles} miles), "
                "but no candidate fuel stations were found along the corridor."
            )

        current_mile = 0.0
        last_stop_mile = 0.0
        fuel_stops: List[Dict[str, Any]] = []

        # Iterate forward while destination is outside current reachable range
        while (current_mile + max_range_miles) < total_distance:
            max_reachable_mile = current_mile + max_range_miles

            # Find all candidate stations ahead within the reachable window
            reachable_stations = [
                s for s in candidate_stations
                if s['route_mile'] > current_mile and s['route_mile'] <= max_reachable_mile
            ]

            if not reachable_stations:
                raise RouteNotFeasibleError(
                    f"Route not feasible: No fuel station found between mile {current_mile:.1f} "
                    f"and {max_reachable_mile:.1f}. Gap exceeds vehicle range of {max_range_miles} miles."
                )

            # Greedy Choice: Select station with lowest price per gallon.
            # Tie-break: Select the one furthest along the route to maximize forward mileage.
            best_station = min(
                reachable_stations,
                key=lambda s: (s['price_per_gallon'], -s['route_mile'])
            )

            leg_distance = round(best_station['route_mile'] - last_stop_mile, 2)
            gallons_purchased = round(leg_distance / mpg, 2)
            price = float(best_station['price_per_gallon'])
            leg_cost = round(gallons_purchased * price, 2)

            fuel_stops.append({
                'station_id': best_station.get('id'),
                'opis_id': best_station.get('opis_id'),
                'name': best_station['name'],
                'address': best_station.get('address', ''),
                'city': best_station['city'],
                'state': best_station['state'],
                'latitude': best_station['latitude'],
                'longitude': best_station['longitude'],
                'price_per_gallon': price,
                'distance_from_start_miles': best_station['route_mile'],
                'leg_distance_miles': leg_distance,
                'gallons_purchased': gallons_purchased,
                'cost': leg_cost,
            })

            # Advance truck position: tank is refueled to full at this station
            current_mile = best_station['route_mile']
            last_stop_mile = best_station['route_mile']

        # Final Leg: From the last refuel stop to the destination
        if fuel_stops:
            final_leg_distance = round(total_distance - last_stop_mile, 2)
            final_leg_gallons = round(final_leg_distance / mpg, 2)
            last_station_price = fuel_stops[-1]['price_per_gallon']
            final_leg_cost = round(final_leg_gallons * last_station_price, 2)

            total_cost = round(sum(s['cost'] for s in fuel_stops) + final_leg_cost, 2)
        else:
            total_cost = 0.0

        return {
            'distance_miles': total_distance,
            'total_cost': total_cost,
            'total_gallons': total_gallons,
            'fuel_stops': fuel_stops,
        }
