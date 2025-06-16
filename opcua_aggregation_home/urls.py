# opcua_aggregation_home/urls.py

from django.urls import path
from . import views

app_name = "opcua_aggregation_home"
urlpatterns = [
    path("", views.index, name="index"),
    path("schedule_machines/", views.schedule_machines, name="schedule_machines"),
    path("remove_connection/", views.remove_connection, name="remove_connection"),
    path(
        "schedule_machine_process_chain/",
        views.schedule_machine_process_chain,
        name="schedule_machine_process_chain",
    ),
    path(
        "schedule_machine_process_inputs/",
        views.schedule_machine_process_inputs,
        name="schedule_machine_process_inputs",
    ),
    path(
        "schedule_machine_process_start/",
        views.schedule_machine_process_start,
        name="schedule_machine_process_start",
    ),
    path(
        "get_process_progress/", views.get_process_progress, name="get_process_progress"
    ),
    path(
        "emergency_stop_process/",
        views.emergency_stop_process,
        name="emergency_stop_process",
    ),
]
