"""App URLs"""

# Django
from django.urls import path

from . import views

app_name: str = "pilotstats"

urlpatterns = [
    path("", views.leaderboard, name="leaderboard"),
    path("upload/", views.upload, name="upload"),
    path("sessions/<int:pk>/", views.session_detail, name="session"),
    path("my-uploads/", views.my_uploads, name="my_uploads"),
    path("my-uploads/<int:pk>/delete/", views.delete_upload, name="delete_upload"),
    path("events/", views.events, name="events"),
]
