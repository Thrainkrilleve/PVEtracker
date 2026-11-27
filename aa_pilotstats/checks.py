"""System checks."""

# Django
from django.conf import settings
from django.core.checks import Error, register

LEGACY_APP = "aa_pvetracker"


@register()
def legacy_app_not_installed(app_configs, **kwargs):
    """
    Pilot Stats replaces PvE Tracker and takes over its tables on upgrade.
    Running both would leave the old app pointing at tables that no longer
    exist under its name, so refuse to start until it's removed.
    """
    if LEGACY_APP in settings.INSTALLED_APPS:
        return [
            Error(
                "Both Pilot Stats and its predecessor PvE Tracker are installed.",
                hint=(
                    f"Remove '{LEGACY_APP}' from INSTALLED_APPS and run "
                    "'pip uninstall aa-pvetracker'. Pilot Stats keeps PvE Tracker's "
                    "data: its first migration takes the old tables over."
                ),
                id="pilotstats.E001",
            )
        ]
    return []
