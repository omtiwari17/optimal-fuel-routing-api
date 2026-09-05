from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    """
    Validates input parameters for route and fuel optimization request.
    """
    start = serializers.CharField(
        required=True,
        max_length=255,
        trim_whitespace=True,
        help_text="Starting location name (e.g. 'Springfield, MO') or 'lat, lon' coordinates."
    )
    finish = serializers.CharField(
        required=True,
        max_length=255,
        trim_whitespace=True,
        help_text="Destination location name (e.g. 'Denver, CO') or 'lat, lon' coordinates."
    )
    corridor_radius_miles = serializers.FloatField(
        required=False,
        default=10.0,
        min_value=1.0,
        max_value=50.0,
        help_text="Maximum lateral search distance from the highway corridor in miles (default: 10.0)."
    )

    def validate(self, data):
        start = data.get('start', '').strip()
        finish = data.get('finish', '').strip()

        if not start:
            raise serializers.ValidationError({"start": "Starting location cannot be empty."})
        if not finish:
            raise serializers.ValidationError({"finish": "Destination location cannot be empty."})
        if start.lower() == finish.lower():
            raise serializers.ValidationError(
                {"finish": "Start and finish locations cannot be identical."}
            )

        return data


class FuelStopSerializer(serializers.Serializer):
    """
    Represents an individual recommended fuel stop along the route.
    """
    station_id = serializers.IntegerField(required=False, allow_null=True)
    opis_id = serializers.IntegerField(required=False, allow_null=True)
    name = serializers.CharField(max_length=255)
    address = serializers.CharField(max_length=255, allow_blank=True)
    city = serializers.CharField(max_length=100)
    state = serializers.CharField(max_length=10)
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    price_per_gallon = serializers.FloatField()
    distance_from_start_miles = serializers.FloatField()
    leg_distance_miles = serializers.FloatField()
    gallons_purchased = serializers.FloatField()
    cost = serializers.FloatField()


class RouteResponseSerializer(serializers.Serializer):
    """
    Response schema containing total distance, route geometry, fuel stops, and costs.
    """
    distance_miles = serializers.FloatField()
    total_cost = serializers.FloatField()
    total_gallons = serializers.FloatField()
    fuel_stops = FuelStopSerializer(many=True)
    route_geojson = serializers.DictField()
