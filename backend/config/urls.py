"""Root URL configuration for the versioned API."""
from django.urls import include, path

urlpatterns = [
    path("api/", include("api.urls")),
]