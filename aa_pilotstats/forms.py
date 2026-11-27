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


class PrivacyForm(forms.Form):
    """Two checkboxes. Both unchecked (the default) is: visible on the feed, wallet read if allowed."""

    hide_from_live_feed = forms.BooleanField(
        required=False,
        label=_("Hide me from the live feed"),
        help_text=_(
            "Your leaderboard totals stay. This only hides your sessions from the events list."
        ),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    wallet_opt_out = forms.BooleanField(
        required=False,
        label=_("Don't read my wallet"),
        help_text=_(
            "Stops reading payouts even if you granted the wallet scope, and deletes the "
            "payouts and rat counts already stored for your characters."
        ),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )


# Kept for imports from before the wallet checkbox existed.
LiveFeedOptOutForm = PrivacyForm
