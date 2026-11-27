"""Hook into Alliance Auth"""

# Django
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

from . import urls
from .views import pvetracker_dashboard


class PvetrackerMenuItem(MenuItemHook):
    """Sidebar menu entry. Any logged-in member can open the leaderboard."""

    def __init__(self):
        MenuItemHook.__init__(
            self,
            _("PvE Tracker"),
            "fas fa-crosshairs fa-fw",
            "pvetracker:leaderboard",
            navactive=["pvetracker:"],
        )

    def render(self, request) -> str:
        if request.user.is_authenticated:
            return MenuItemHook.render(self, request)
        return ""


class PvetrackerDashboardHook(hooks.DashboardItemHook):
    def __init__(self):
        super().__init__(pvetracker_dashboard, 1)


@hooks.register("menu_item_hook")
def register_menu():
    return PvetrackerMenuItem()


@hooks.register("url_hook")
def register_urls():
    return UrlHook(urls, "pvetracker", r"^pvetracker/")


@hooks.register("dashboard_hook")
def register_dashboard():
    return PvetrackerDashboardHook()
