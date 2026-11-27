"""
Settings, each overridable from Alliance Auth's ``local.py``.

Read once at import; restart Auth after changing one.
"""

# Django
from django.conf import settings

# Gamelog folder scanner ------------------------------------------------------

# Most uploads one user's scanner may send per minute. A first scan of a
# large Gamelogs folder is paced to this; 0 turns the limit off.
SCANNER_UPLOADS_PER_MINUTE = int(getattr(settings, "PILOTSTATS_SCANNER_UPLOADS_PER_MINUTE", 30))

# By default the scanner only sends logs modified in the last N days; pilots
# can tick "include older logs" for a one-time backfill.
SCANNER_RECENT_DAYS = int(getattr(settings, "PILOTSTATS_SCANNER_RECENT_DAYS", 7))
