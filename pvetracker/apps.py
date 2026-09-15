"""App Configuration"""

# Django
from django.apps import AppConfig

# PvE Tracker
from pvetracker import __version__


class PvetrackerConfig(AppConfig):
    """App Config"""

    name = "pvetracker"
    label = "pvetracker"
    verbose_name = f"PvE Tracker v{__version__}"
