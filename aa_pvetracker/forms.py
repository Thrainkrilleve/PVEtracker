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
