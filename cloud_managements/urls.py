# cloud_managements/urls.py

from django.urls import path
from . import views

app_name = "cloud_managements"

urlpatterns = [
    path("", views.index, name="index"),
    path("user_accounts/", views.user_accounts, name="user_accounts"),
    path(
        "user_accounts/<str:id>/", views.user_account_detail, name="user_account_detail"
    ),
    path("quote_requests/", views.quote_requests, name="quote_requests"),
    path(
        "quote_requests/<str:id>/",
        views.quote_request_detail,
        name="quote_request_detail",
    ),
]
