"""Forms"""

# Django
from django import forms
from django.utils.translation import gettext_lazy as _


class LogUploadForm(forms.Form):
    """A single EVE Gamelog .txt file, dropped or picked by the user."""

    log_file = forms.FileField(
        label=_("Combat log"),
        help_text=_("A .txt file from Documents\\EVE\\logs\\Gamelogs"),
    )


class LiveFeedOptOutForm(forms.Form):
    """Single checkbox. Unchecked (the default) leaves the pilot on the events feed."""

    hide_from_live_feed = forms.BooleanField(
        required=False,
        label=_("Hide me from the live feed"),
        help_text=_(
            "Your leaderboard totals stay. This only hides your sessions from the events list."
        ),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
