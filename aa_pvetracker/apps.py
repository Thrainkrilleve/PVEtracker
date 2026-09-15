"""App Configuration"""

# Django
from django.apps import AppConfig

# PvE Tracker
from aa_pvetracker import __version__


class PvetrackerConfig(AppConfig):
    """App Config

    `name` is the importable package (what pip installs as "aa-pvetracker"
    and what goes in INSTALLED_APPS) — kept distinct from `label`, which
    stays the short, stable identifier used in permission strings, the URL
    namespace, and migration app_label, so renaming the distribution again
    later won't ripple through every `pvetracker.basic_access` /
    `{% url 'pvetracker:...' %}` reference.
    """

    name = "aa_pvetracker"
    label = "pvetracker"
    verbose_name = f"PvE Tracker v{__version__}"
