"""Hook into Alliance Auth"""

# Django
from django.apps import apps as django_apps
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

from . import legacy_urls, urls
from .views import pilotstats_dashboard


class PilotstatsMenuItem(MenuItemHook):
    """Sidebar menu entry. Any logged-in member can open the leaderboard."""

    def __init__(self):
        MenuItemHook.__init__(
            self,
            _("Pilot Stats"),
            "fas fa-crosshairs fa-fw",
            "pilotstats:leaderboard",
            navactive=["pilotstats:"],
        )

    def render(self, request) -> str:
        if request.user.is_authenticated:
            return MenuItemHook.render(self, request)
        return ""


class PilotstatsDashboardHook(hooks.DashboardItemHook):
    def __init__(self):
        super().__init__(pilotstats_dashboard, 1)


@hooks.register("menu_item_hook")
def register_menu():
    return PilotstatsMenuItem()


@hooks.register("url_hook")
def register_urls():
    return UrlHook(urls, "pilotstats", r"^pilotstats/")


@hooks.register("url_hook")
def register_legacy_urls():
    """Redirect PvE Tracker-era /pvetracker/... links to /pilotstats/..."""
    return UrlHook(legacy_urls, "pvetracker_legacy", r"^pvetracker/")


@hooks.register("dashboard_hook")
def register_dashboard():
    return PilotstatsDashboardHook()


# AA Desktop's bridge is optional. Without it nothing below is registered and
# Pilot Stats works as before. The import of desktop.py happens inside the
# hook, so a problem there makes the bridge skip this module (and log why)
# instead of breaking the rest of Pilot Stats.
if django_apps.is_installed("aa_desktop_bridge"):

    @hooks.register("desktop_module_hook")
    def register_desktop_module():
        from .desktop import desktop_module

        return desktop_module()
