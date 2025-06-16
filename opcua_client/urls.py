# opcua_client/urls.py

from django.urls import path
from . import views
app_name = "opcua_client"
urlpatterns = [
    path("start_dashboard/", views.start_dashboard, name="start_dashboard"),
    path(
        "get_node_information/", views.get_node_information, name="get_node_information"
    ),
    path(
        "monitor_realtime_variables/",
        views.monitor_realtime_variables,
        name="monitor_realtime_variables",
    ),
    path("call-method/", views.process_method_request, name="process_method_request"),
    path("end-connection/", views.end_connection, name="end_connection"),
    path("generate_graph/", views.generate_graph, name="generate_graph"),
    path("delete_graph/", views.delete_graph, name="delete_graph"),
    path('download_data/', views.download_data, name='download_data')
]
