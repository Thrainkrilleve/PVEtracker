"""
Celery tasks. Schedule them in local.py (see the README):

    pull_all_wallet_journals   hourly    ESI wallet journal → payouts, rat counts
    drain_r2z2                 1 min     zKill R2Z2 → killmails
    poll_tracked_fleets        1 min     ESI fleet roster for tracked fleets
    purge_old_payouts          daily     drop itemized payouts past retention
    post_discord_daily         5 min     daily top 10 to Discord, once at the set time

Every task is safe to run late, twice, or not at all for a while.
"""

# Standard Library
import logging
import time
from datetime import timedelta

# Third Party
import requests
from celery import shared_task

# Django
from django.core.cache import cache
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter

from esi.exceptions import (
    ESIBucketLimitException,
    ESIErrorLimitException,
    HTTPClientError,
    HTTPNotModified,
    HTTPServerError,
)
from esi.models import Token

from . import discord, fleets, payouts, providers, typenames, zkill
from .app_settings import (
    PAYOUT_RETENTION_DAYS,
    R2Z2_MAX_PER_RUN,
    R2Z2_REQUEST_INTERVAL,
    R2Z2_URL,
    USER_AGENT,
    WALLET_SCOPE,
)
from .models import (
    CombatLogUpload,
    DiscordPost,
    Killmail,
    KillmailParticipant,
    NpcKillDaily,
    Payout,
    R2Z2State,
    TrackedFleet,
)

logger = logging.getLogger(__name__)

R2Z2_LOCK = "pilotstats:lock:drain_r2z2"
FLEET_LOCK = "pilotstats:lock:poll_tracked_fleets"
# Past this many missing sequence numbers (e.g. after a long outage, older
# R2Z2 files expire after ~24 h) jump straight to the newest sequence.
R2Z2_MAX_GAP = 2000
# Spread wallet pulls over the hour instead of firing them all at once.
WALLET_STAGGER_SECONDS = 5


# ---- Wallet journal ----------------------------------------------------------


def wallet_character_ids() -> list:
    """EVE ids of characters whose owner granted the wallet scope and didn't opt out."""
    opted_out = payouts.wallet_opted_out_user_ids()
    tokens = (
        Token.objects.filter(scopes__name=WALLET_SCOPE)
        .exclude(user_id__in=opted_out)
        .values_list("character_id", "user_id")
        .distinct()
    )
    owned = set(CharacterOwnership.objects.values_list("character__character_id", "user_id"))
    return sorted({cid for cid, uid in tokens if (cid, uid) in owned})


@shared_task
def pull_all_wallet_journals():
    for index, character_id in enumerate(wallet_character_ids()):
        pull_wallet_journal.apply_async(
            args=[character_id], countdown=index * WALLET_STAGGER_SECONDS,
        )


@shared_task(bind=True, max_retries=3)
def pull_wallet_journal(self, character_id: int):
    character = EveCharacter.objects.filter(character_id=character_id).first()
    if character is None:
        return
    ownership = CharacterOwnership.objects.filter(character=character).select_related("user").first()
    if ownership is None or ownership.user_id in payouts.wallet_opted_out_user_ids():
        return
    token = Token.get_token(character_id, [WALLET_SCOPE])
    if not token or token.user_id != ownership.user_id:
        return
    try:
        entries = providers.wallet_journal(character_id, token)
    except HTTPNotModified:
        return
    except (ESIBucketLimitException, ESIErrorLimitException) as exc:
        wait = int(getattr(exc, "reset", 0) or 60)
        raise self.retry(exc=exc, countdown=max(wait, 60))
    except HTTPServerError as exc:
        raise self.retry(exc=exc, countdown=300)
    except HTTPClientError as exc:
        logger.warning("Wallet journal for %s refused: %s", character_id, exc)
        return
    except ValueError:
        # A journal type newer than the pinned spec, failing validation.
        logger.warning("Wallet journal for %s didn't validate; skipped", character_id, exc_info=True)
        return
    if payouts.store_journal(character, entries):
        discord.check_payouts(character)
        unnamed = typenames.missing(
            NpcKillDaily.objects.filter(character=character)
            .values_list("npc_type_id", flat=True).distinct()
        )
        if unnamed:
            resolve_type_names.delay(unnamed)
    # Every successful pull, even with nothing new: a session uploaded since
    # the last pull, or a wallet connected for the first time, gets its ISK.
    payouts.match_sessions(character)


@shared_task
def purge_old_payouts():
    cutoff = timezone.now() - timedelta(days=PAYOUT_RETENTION_DAYS)
    Payout.objects.filter(occurred_at__lt=cutoff).delete()


# ---- zKill R2Z2 ------------------------------------------------------------------


def _r2z2_session():
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.headers["Accept"] = "application/json"
    return session


def _latest_sequence(session) -> int:
    response = session.get(f"{R2Z2_URL}/sequence.json", timeout=10)
    response.raise_for_status()
    return int(response.json()["sequence"])


@shared_task
def drain_r2z2(max_files: int = R2Z2_MAX_PER_RUN, interval: float = R2Z2_REQUEST_INTERVAL):
    """Read new R2Z2 sequence files until caught up (404) or *max_files*, keeping tracked mails."""
    if not cache.add(R2Z2_LOCK, 1, timeout=600):
        return
    try:
        zkill.refresh_auto_entities()
        corps, alliances = zkill.tracked_ids()
        state = R2Z2State.get()
        session = _r2z2_session()
        if state.last_sequence is None:
            state.last_sequence = _latest_sequence(session) - 1
            state.save(update_fields=["last_sequence", "updated_at"])
        next_sequence = state.last_sequence + 1
        ship_types = set()

        for _index in range(max_files):
            response = session.get(f"{R2Z2_URL}/{next_sequence}.json", timeout=10)
            if response.status_code == 404:
                latest = _latest_sequence(session)
                if latest < next_sequence:
                    break  # caught up; the next run waits the required ≥6 s
                if latest - next_sequence > R2Z2_MAX_GAP:
                    logger.warning("R2Z2 fell %s files behind; skipping to the newest", latest - next_sequence)
                    next_sequence = latest
                else:
                    next_sequence += 1  # a missing file inside the window
                continue
            if response.status_code == 429:
                logger.warning("R2Z2 rate limited us; stopping this run")
                break
            response.raise_for_status()
            package = response.json()
            if zkill.process_killmail(package, corps, alliances):
                kept = Killmail.objects.filter(killmail_id=package.get("killmail_id")).first()
                if kept is not None:
                    discord.check_killmail(kept)
                esi = package.get("esi") or {}
                ship_types.add((esi.get("victim") or {}).get("ship_type_id"))
                ship_types.update(a.get("ship_type_id") for a in esi.get("attackers") or [])
            state.last_sequence = next_sequence
            state.save(update_fields=["last_sequence", "updated_at"])
            next_sequence += 1
            if interval:
                time.sleep(interval)

        ship_types.discard(None)
        if ship_types:
            resolve_type_names.delay(sorted(ship_types))
    except requests.RequestException:
        logger.warning("R2Z2 request failed; will resume from the saved sequence", exc_info=True)
    finally:
        cache.delete(R2Z2_LOCK)


# ---- Fleets ------------------------------------------------------------------------


@shared_task
def poll_tracked_fleets():
    if not cache.add(FLEET_LOCK, 1, timeout=300):
        return
    try:
        ship_types = set()
        for fleet in TrackedFleet.objects.filter(ended_at__isnull=True).select_related("boss"):
            try:
                fleets.poll_fleet(fleet)
            except (ESIBucketLimitException, ESIErrorLimitException, HTTPServerError):
                logger.warning("ESI busy while polling fleet %s; next run retries", fleet.fleet_id)
                continue
            ship_types.update(fleet.members.values_list("ship_type_id", flat=True))
        if ship_types:
            resolve_type_names.delay(sorted(ship_types))
    finally:
        cache.delete(FLEET_LOCK)


@shared_task
def poll_fleet_now(fleet_pk: int):
    """First poll right after someone starts tracking, so the page isn't empty for a minute."""
    fleet = TrackedFleet.objects.filter(pk=fleet_pk, ended_at__isnull=True).select_related("boss").first()
    if fleet:
        fleets.poll_fleet(fleet)
        resolve_type_names.delay(sorted(set(fleet.members.values_list("ship_type_id", flat=True))))


# ---- Type names --------------------------------------------------------------------


@shared_task
def resolve_type_names(type_ids):
    typenames.resolve(type_ids)


@shared_task
def resolve_known_type_names():
    """Backfill names for every hull seen so far (cheap; only missing ids are fetched)."""
    ids = set(Killmail.objects.values_list("victim_ship_type_id", flat=True))
    ids.update(KillmailParticipant.objects.values_list("ship_type_id", flat=True))
    typenames.resolve(ids)


# ---- Discord ------------------------------------------------------------------------


@shared_task
def post_discord_daily():
    """Runs every few minutes; posts the daily top 10 once, at the configured local time."""
    discord.post_daily()


@shared_task
def check_session_records(session_pk: int):
    session = CombatLogUpload.objects.filter(pk=session_pk).first()
    if session is not None:
        discord.check_session_records(session)


@shared_task(bind=True, max_retries=5)
def send_discord_post(self, post_pk: int, body: dict):
    """POST one message to the webhook. Honors 429 retry_after; retries server errors."""
    post = DiscordPost.objects.filter(pk=post_pk).first()
    config = discord.DiscordSettings.get()
    if post is None or not config.is_active:
        return
    try:
        response = requests.post(
            config.webhook_url, json=body, timeout=10, headers={"User-Agent": USER_AGENT},
        )
    except requests.RequestException as exc:
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))
    if response.status_code == 429:
        try:
            wait = float(response.json().get("retry_after", 5))
        except ValueError:
            wait = float(response.headers.get("Retry-After", 5))
        raise self.retry(countdown=max(1, int(wait) + 1))
    if response.status_code >= 500:
        raise self.retry(countdown=60 * (self.request.retries + 1))
    if response.status_code >= 400:
        # Bad or deleted webhook: retrying won't help.
        logger.warning("Discord webhook refused a post (%s)", response.status_code)
        DiscordPost.objects.filter(pk=post_pk).update(
            status=DiscordPost.Status.FAILED, detail=f"HTTP {response.status_code}",
        )
        return
    DiscordPost.objects.filter(pk=post_pk).update(status=DiscordPost.Status.SENT)
