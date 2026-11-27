"""
ESI client for Pilot Stats.

Same pattern as Alliance Auth's own providers (e.g. ``allianceauth/srp``):
one ``ESIClientProvider`` per app, limited to the operations used. It sends
``X-Compatibility-Date`` on unversioned paths and handles ETags, paging, the
response cache, and ESI's rate-limit buckets. Works with django-esi 9.x and
10.x; only the API common to both is used (no ``last_modified``).

Only these routes are called. In particular no wallet balance or wallet
transactions, which the same wallet scope would allow.
"""

from esi.openapi_clients import ESIClientProvider

from . import __version__
from .app_settings import ESI_COMPATIBILITY_DATE

esi = ESIClientProvider(
    compatibility_date=ESI_COMPATIBILITY_DATE,
    ua_appname="aa-pilotstats",
    ua_version=__version__,
    ua_url="https://github.com/Thrainkrilleve/PilotStats",
    operations=[
        "GetCharactersCharacterIdWalletJournal",
        "GetCharactersCharacterIdFleet",
        "GetFleetsFleetIdMembers",
    ],
)


def wallet_journal(character_id: int, token):
    """All pages of the journal (30 days). Raises HTTPNotModified when unchanged."""
    return esi.client.Wallet.GetCharactersCharacterIdWalletJournal(
        character_id=character_id, token=token,
    ).results(use_etag=True)


def character_fleet(character_id: int, token):
    """The fleet the character is in. 404 (HTTPClientError) when in none."""
    return esi.client.Fleets.GetCharactersCharacterIdFleet(
        character_id=character_id, token=token,
    ).result(use_etag=False)


def fleet_members(fleet_id: int, token):
    """Fleet roster. Needs the fleet boss's token (403 otherwise)."""
    return esi.client.Fleets.GetFleetsFleetIdMembers(
        fleet_id=fleet_id, token=token,
    ).result(use_etag=False)
