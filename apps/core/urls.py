"""URL configuration for Core application."""
from django.urls import path
from .views import HealthCheckView, LivenessCheckView, ReadinessCheckView

urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health-check"),
    path("health/live/", LivenessCheckView.as_view(), name="health-live"),
    path("health/ready/", ReadinessCheckView.as_view(), name="health-ready"),
]
