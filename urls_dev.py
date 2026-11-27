from django.contrib import admin
from django.urls import include, path

from allianceauth import views as auth_views

# Auth's base template reverses a handful of authentication, notification,
# and menu URLs while rendering any page. Include those modules so the dev
# harness can render PvE Tracker pages. The app's own URLs stay explicit
# here; production mounts them through the url hook instead.
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
    path("pvetracker/", include("aa_pvetracker.urls", namespace="pvetracker")),
]
