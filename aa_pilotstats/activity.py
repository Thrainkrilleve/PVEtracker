"""
What kind of PvE a session was: ratting, an incursion, an abyssal run, or missions.

Two kinds of evidence, strongest first:

1. **The wallet** (when connected): a mission reward in the session's payout
   window means missions; a bounty tick means ratting. Abyssal sites pay no
   bounties, which also tells an abyssal run apart from Triglavian ratting.
2. **What was shot**, from the combat log's top targets: Sansha incursion NPCs
   and abyssal NPCs have distinctive names (lists below).

The name lists cover EVE's naming schemes and are deliberately in one place.
An NPC missing from them only means that session falls back to the wallet
rule; it never turns ratting into something else. Extend them as needed.
"""

# Standard Library
import re

# Words in Sansha incursion NPC names. They're named after parts of the brain
# ("Romi Thalamus", "Mara Paleo", "Schmaeel Medulla"), plus the mothership.
INCURSION_WORDS = frozenset({
    "cerebellum", "cerebi", "colliculus", "dien", "epithalamus", "hypophysis",
    "medulla", "mesen", "meten", "myelen", "paleo", "rhomben", "tectum",
    "tegmentum", "telen", "thalamus", "kundalini",
})

# Words in abyssal deadspace NPC names: Triglavian hulls ("Starving Damavik"),
# Drifters ("Lucid Deepwatcher"), the abyssal Sleeper-type Ephialtes, and the
# Karybdis Tyrannos.
ABYSSAL_WORDS = frozenset({
    "damavik", "drekavac", "kikimora", "leshak", "rodiva", "vedmak",
    "lucid", "ephialtes", "karybdis",
})

# How much of the damage dealt must go to such NPCs to call the session that.
NAME_SHARE = 0.25

MISSION_TYPES = frozenset({"agent_mission_reward", "agent_mission_time_bonus_reward", "mission_reward"})
BOUNTY_TYPES = frozenset({"bounty", "bounty_prize", "bounty_prizes"})

_WORD = re.compile(r"[a-z]+")


def _share(targets, words) -> float:
    """Fraction of the damage in *targets* (``[[name, damage], ...]``) dealt to names containing *words*."""
    total = matched = 0
    for name, damage in targets or []:
        damage = damage or 0
        total += damage
        if words.intersection(_WORD.findall(str(name).lower())):
            matched += damage
    return matched / total if total else 0.0


def classify(top_targets, payout_types=None) -> str:
    """The session's activity (an ``Activity`` value), or "" when there's no telling.

    *payout_types* is the set of payout types that landed in the session's
    payout window, or None when the character's wallet isn't connected.
    """
    # Imported here so this module has no Django model import at load time.
    from .models import CombatLogUpload

    Activity = CombatLogUpload.Activity
    kinds = set(payout_types or ())
    if kinds & MISSION_TYPES:
        return Activity.MISSION
    if _share(top_targets, INCURSION_WORDS) >= NAME_SHARE:
        return Activity.INCURSION
    if _share(top_targets, ABYSSAL_WORDS) >= NAME_SHARE and not kinds & BOUNTY_TYPES:
        return Activity.ABYSSAL
    if kinds & BOUNTY_TYPES:
        return Activity.RATTING
    if payout_types is None:
        return ""  # no wallet and no telling names: unclassified, not "other"
    return Activity.OTHER
