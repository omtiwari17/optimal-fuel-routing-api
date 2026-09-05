from django.urls import path
from core.views import RouteAPIView

urlpatterns = [
    path('route/', RouteAPIView.as_view(), name='api-route'),
]
