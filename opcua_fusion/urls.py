# opcua_fusion/urls.py

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("", include("opcua_client.urls")),
    path("", include("opcua_aggregation_home.urls")),
    path("cloud_managements/", include("cloud_managements.urls")),
]
