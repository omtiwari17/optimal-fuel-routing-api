from django.db import models


class FuelStation(models.Model):
    """
    Represents a US fuel station / truck stop with retail fuel price and geographic coordinates.
    Populated once offline via the geocode_stations management command.
    """
    opis_id = models.IntegerField(db_index=True, null=True, blank=True, help_text="OPIS Truckstop ID from CSV")
    name = models.CharField(max_length=255, help_text="Station or brand name")
    address = models.CharField(max_length=255, help_text="Street address")
    city = models.CharField(max_length=100, help_text="City name")
    state = models.CharField(max_length=10, db_index=True, help_text="Two-letter US state code")
    price_per_gallon = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        db_index=True,
        help_text="Retail diesel price per gallon in USD"
    )
    latitude = models.FloatField(db_index=True, help_text="Geographic latitude coordinate")
    longitude = models.FloatField(db_index=True, help_text="Geographic longitude coordinate")

    class Meta:
        verbose_name = "Fuel Station"
        verbose_name_plural = "Fuel Stations"
        indexes = [
            models.Index(fields=['latitude', 'longitude'], name='idx_station_coords'),
        ]

    def __str__(self):
        return f"{self.name} - {self.city}, {self.state} (${self.price_per_gallon}/gal)"
