"""App URLs"""

# Django
from django.urls import path

from . import views

app_name: str = "pilotstats"

urlpatterns = [
    path("", views.leaderboard, name="leaderboard"),
    path("upload/", views.upload, name="upload"),
    path("upload/characters.json", views.upload_characters, name="upload_characters"),
    path("sessions/<int:pk>/", views.session_detail, name="session"),
    path("my-uploads/", views.my_uploads, name="my_uploads"),
    path("my-uploads/<int:pk>/delete/", views.delete_upload, name="delete_upload"),
    path("events/", views.events, name="events"),
    path("wallet/connect/", views.connect_wallet, name="connect_wallet"),
    path("fleets/", views.fleet_list, name="fleets"),
    path("fleets/track/", views.track_fleet, name="track_fleet"),
    path("fleets/<int:pk>/", views.fleet_detail, name="fleet"),
    path("fleets/<int:pk>/stop/", views.stop_fleet, name="stop_fleet"),
]
