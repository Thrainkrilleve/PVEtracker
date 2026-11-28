"""
Routes for AA Desktop's automatic Gamelog upload.

Only imported when aa-desktop-bridge is in INSTALLED_APPS (see auth_hooks.py).
The bridge mounts the router at ``/api/desktop/v1/modules/pilotstats/``:

    POST upload      multipart field ``log_file``; the same JSON result as the
                     folder scanner (created, updated, duplicate, no_new_lines,
                     not_owned, ...), 400 ``invalid``, or 429 ``throttled``
    GET  characters  {"characters": [...]}: the user's own character names

Each route needs a bearer access token with the ``desktop.pilotstats.upload``
scope **and** the ``pilotstats.basic_access`` permission, the same permission
the web upload needs. Sessions and cookies are not accepted. Uploads go
through the same code, ownership check and per-user throttle as the folder
scanner.
"""

# Django
from django.http import HttpResponse

# Third Party
from aa_desktop_bridge.auth import DesktopBearer
from aa_desktop_bridge.modules import DesktopModule
from ninja import Router

from . import __title__, __version__
from .views import own_character_names, upload_json

SCOPE_UPLOAD = "desktop.pilotstats.upload"
SCOPE_DESCRIPTION = "Upload your own characters' EVE combat logs to Pilot Stats."
PERMISSIONS = ("pilotstats.basic_access",)

_auth = DesktopBearer([SCOPE_UPLOAD], permissions=PERMISSIONS)

router = Router()


def _no_store(response):
    response["Cache-Control"] = "no-store"
    return response


@router.post("upload", auth=_auth)
def upload(request):
    return _no_store(upload_json(request))


@router.get("characters", auth=_auth)
def characters(request, response: HttpResponse):
    _no_store(response)
    return {"characters": own_character_names(request.user)}


def desktop_module() -> DesktopModule:
    return DesktopModule(
        id="pilotstats",
        name=str(__title__),
        version=__version__,
        scopes={SCOPE_UPLOAD: SCOPE_DESCRIPTION},
        required_permissions=PERMISSIONS,
        router=router,
    )
