from django.urls import path
from core.views import RouteAPIView, home_view, ui_route_view

urlpatterns = [
    path('', home_view, name='home'),
    path('api/route/', RouteAPIView.as_view(), name='api-route'),
    path('ui/route/', ui_route_view, name='ui-route'),
]
