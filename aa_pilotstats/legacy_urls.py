"""
Old PvE Tracker URLs (``/pvetracker/...``) redirect to Pilot Stats.

Bookmarks, Discord messages, and wiki links from before the rename keep
working: ``/pvetracker/sessions/12/`` goes to ``/pilotstats/sessions/12/``
with the query string kept.
"""

# Django
from django.urls import re_path, reverse
from django.views.generic import RedirectView

app_name = "pvetracker_legacy"


class LegacyRedirectView(RedirectView):
    permanent = True

    def get_redirect_url(self, *args, **kwargs):
        url = f"{reverse('pilotstats:leaderboard')}{kwargs.get('rest', '')}"
        query = self.request.META.get("QUERY_STRING", "")
        return f"{url}?{query}" if query else url


urlpatterns = [
    re_path(r"^(?P<rest>.*)$", LegacyRedirectView.as_view(), name="redirect"),
]
