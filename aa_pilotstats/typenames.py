"""
Names for EVE type ids (hulls, NPCs).

Pages only read the local ``TypeName`` cache and never wait on the network.
A Celery task fills missing names: from the ``eve_sde`` app's tables when
that app is installed, otherwise from CCP's SDE over HTTP
(``sde.evetech.net``). Only ids this app has seen are looked up.
"""

# Standard Library
import logging

# Third Party
import requests

# Django
from django.apps import apps

from .app_settings import SDE_URL, USER_AGENT
from .models import TypeName

logger = logging.getLogger(__name__)

MAX_HTTP_LOOKUPS = 50


def names(type_ids) -> dict:
    """{type_id: name} for the ids already cached. Never blocks on the network."""
    ids = {int(t) for t in type_ids if t}
    if not ids:
        return {}
    return dict(TypeName.objects.filter(type_id__in=ids).values_list("type_id", "name"))


def label(type_id, cache: dict) -> str:
    if not type_id:
        return ""
    return cache.get(int(type_id)) or f"Type {type_id}"


def missing(type_ids) -> list:
    ids = {int(t) for t in type_ids if t}
    have = set(TypeName.objects.filter(type_id__in=ids).values_list("type_id", flat=True))
    return sorted(ids - have)


def resolve(type_ids) -> int:
    """Fill the cache for any of *type_ids* not cached yet. Returns how many were added."""
    todo = missing(type_ids)
    if not todo:
        return 0
    found = _from_eve_sde(todo) if apps.is_installed("eve_sde") else {}
    rest = [t for t in todo if t not in found][:MAX_HTTP_LOOKUPS]
    if rest:
        found.update(_from_sde_http(rest))
    TypeName.objects.bulk_create(
        [TypeName(type_id=t, name=n, group_name=g) for t, (n, g) in found.items()],
        ignore_conflicts=True,
    )
    return len(found)


def _from_eve_sde(type_ids) -> dict:
    try:
        ItemType = apps.get_model("eve_sde", "ItemType")
        rows = ItemType.objects.filter(id__in=type_ids).select_related("group")
        return {
            row.id: (row.name, getattr(getattr(row, "group", None), "name", "") or "")
            for row in rows
        }
    except Exception:  # an eve_sde version with a different schema: fall back to HTTP
        logger.warning("eve_sde lookup failed; using the SDE HTTP API", exc_info=True)
        return {}


def _from_sde_http(type_ids) -> dict:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    groups: dict = {}
    found = {}
    for type_id in type_ids:
        try:
            response = session.get(f"{SDE_URL}/types/{type_id}.json", timeout=10)
            if response.status_code != 200:
                continue
            data = response.json()
            name = _english(data.get("name")) or f"Type {type_id}"
            group_id = data.get("groupID")
            if group_id and group_id not in groups:
                group = session.get(f"{SDE_URL}/groups/{group_id}.json", timeout=10)
                groups[group_id] = _english(group.json().get("name")) if group.status_code == 200 else ""
            found[type_id] = (name, groups.get(group_id, ""))
        except (requests.RequestException, ValueError):
            logger.warning("SDE lookup failed for type %s", type_id, exc_info=True)
    return found


def _english(value) -> str:
    if isinstance(value, dict):
        return value.get("en") or next(iter(value.values()), "")
    return value or ""
