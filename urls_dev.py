from django.contrib import admin
from django.urls import include, path

from allianceauth import views as auth_views
from allianceauth.authentication.decorators import decorate_url_patterns, main_character_required

from aa_pvetracker.auth_hooks import register_urls

# Auth's base template reverses a handful of authentication, notification,
# and menu URLs while rendering any page. Include those modules so the dev
# harness can render PvE Tracker pages.
#
# The app's own URLs are mounted from its url hook and wrapped in
# main_character_required, exactly as Alliance Auth's urls_from_apps() does,
# so tests see the same redirect a real Auth gives a user with no main.
_pvetracker_hook = register_urls()

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", include("allianceauth.authentication.urls")),
    path("account/", include("allianceauth.authentication.hmac_urls")),
    path("", include("allianceauth.notifications.urls")),
    path("", include("allianceauth.groupmanagement.urls")),
    path("", include("allianceauth.services.urls")),
    path("night/", auth_views.NightModeRedirectView.as_view(), name="nightmode"),
    path("theme/", auth_views.ThemeRedirectView.as_view(), name="theme"),
    path("minimize-sidebar/", auth_views.MinimizeSidebarRedirectView.as_view(), name="minimize_sidebar"),
    path(
        "",
        decorate_url_patterns([_pvetracker_hook.include_pattern], main_character_required),
    ),
]
