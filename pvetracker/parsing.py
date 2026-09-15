"""
Parser for EVE Online Gamelog combat lines.

Ported from the standalone `eve-damage-tracker` prototype (validated there
against 6,520/6,520 and 2,960/2,960 real combat lines from an actual pilot's
logs), with one addition: `remote_rep_out`, mirroring `remote_rep_in` with
"to" instead of "by" so a logi pilot's own log credits reps *given*. That
regex is UNVERIFIED — we only had DPS-pilot log samples to test against, no
real logi-pilot log showing the outgoing-rep line shape. It's an isolated
pattern, so fixing it later (once someone uploads a real logi log) is a
one-line change; results derived from it are flagged as estimated wherever
they're surfaced.

Gamelog files are plain UTF-8 text. Each line is either a header line, or:

    [ YYYY.MM.DD HH:MM:SS ] (channel) message

For (combat) lines, the message is wrapped in EVE's rich-text markup
(<color=...>, <b>, <font size=10>), e.g.:

    <color=0xff00ffff><b>1022</b> <color=0x77ffffff><font size=10>to</font>
    <b><color=0xffffffff>Bewildering Lathe</b><font size=10><color=0x77ffffff>
    - Mega Pulse Laser II - Hits

Stripping the known markup tags (but NOT other angle-bracket text, since EVE
also writes alliance tickers as literal "<TICKER>" with no closing tag, e.g.
"Tyler Lynch [C5-F]<FRT>") collapses that down to:

    1022 to Bewildering Lathe - Mega Pulse Laser II - Hits

which is what the regexes below match against.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

_MARKUP_RE = re.compile(r"</?(?:color(?:=0x[0-9a-fA-F]+)?|b|font(?:\s+size=\d+)?)>", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")

_LINE_RE = re.compile(
    r"^\[\s*(?P<ts>\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})\s*\]\s*\((?P<channel>[^)]*)\)\s*(?P<msg>.*)$"
)

_QUALITIES = "Hits|Grazes|Wrecks|Penetrates|Glances Off|Smashes|Misses"

_DAMAGE_OUT_RE = re.compile(
    rf"^(?P<amount>\d+)\s+to\s+(?P<target>.+?)\s+-\s+(?P<weapon>.+?)\s+-\s+(?P<quality>{_QUALITIES})$"
)
_DAMAGE_IN_RE = re.compile(
    rf"^(?P<amount>\d+)\s+from\s+(?P<attacker>.+?)\s+-\s+(?P<quality>{_QUALITIES})$"
)
_MISS_OUT_RE = re.compile(
    r"^Your (?:group of )?(?P<weapon>.+?) misses (?P<target>.+?) completely(?:\s+-\s+.+)?$"
)
_MISS_IN_RE = re.compile(r"^(?P<attacker>.+?) misses you completely$")
_JAMMED_RE = re.compile(r"^You're jammed by (?P<attacker>.+?)\s+-\s+.+$")
_REMOTE_REP_KINDS = "armor repaired|shield boosted|capacitor transmitted"
_REMOTE_REP_IN_RE = re.compile(
    rf"^(?P<amount>\d+)\s+remote (?P<kind>{_REMOTE_REP_KINDS})\s+by\s+(?P<healer>.+)\s+-\s+(?P<module>[^-]+)$"
)
# Unverified — see module docstring.
_REMOTE_REP_OUT_RE = re.compile(
    rf"^(?P<amount>\d+)\s+remote (?P<kind>{_REMOTE_REP_KINDS})\s+to\s+(?P<recipient>.+)\s+-\s+(?P<module>[^-]+)$"
)
_ENERGY_NEUT_RE = re.compile(r"^(?P<amount>\d+)\s*GJ\s+energy neutralized\s+(?P<target>.+?)\s+-\s+.+$")

QUALITY_ORDER = ["Wrecks", "Hits", "Penetrates", "Smashes", "Grazes", "Glances Off", "Misses"]


@dataclass
class CombatEvent:
    timestamp: datetime
    kind: str  # damage_out, damage_in, miss_out, miss_in, remote_rep_in, remote_rep_out, jammed, energy_neutralized, other
    amount: int = 0
    other_party: str = ""  # target/attacker/healer/recipient depending on kind
    weapon: str = ""
    quality: str = ""
    raw: str = ""


@dataclass
class ParseStats:
    combat_lines: int = 0
    matched: int = 0
    unmatched_samples: list = None

    def __post_init__(self):
        if self.unmatched_samples is None:
            self.unmatched_samples = []


def strip_markup(text: str) -> str:
    return _WS_RE.sub(" ", _MARKUP_RE.sub("", text)).strip()


def parse_line(line: str) -> Optional[tuple]:
    m = _LINE_RE.match(line.strip())
    if not m:
        return None
    ts = datetime.strptime(m.group("ts"), "%Y.%m.%d %H:%M:%S")
    return ts, m.group("channel"), m.group("msg")


def parse_combat_message(ts: datetime, msg: str, stats: Optional[ParseStats] = None) -> Optional[CombatEvent]:
    clean = strip_markup(msg)

    m = _DAMAGE_OUT_RE.match(clean)
    if m:
        return CombatEvent(ts, "damage_out", int(m.group("amount")), m.group("target"),
                            m.group("weapon"), m.group("quality"), clean)

    m = _DAMAGE_IN_RE.match(clean)
    if m:
        return CombatEvent(ts, "damage_in", int(m.group("amount")), m.group("attacker"),
                            "", m.group("quality"), clean)

    m = _MISS_OUT_RE.match(clean)
    if m:
        return CombatEvent(ts, "miss_out", 0, m.group("target"), m.group("weapon"), "Misses", clean)

    m = _MISS_IN_RE.match(clean)
    if m:
        return CombatEvent(ts, "miss_in", 0, m.group("attacker"), "", "Misses", clean)

    m = _REMOTE_REP_IN_RE.match(clean)
    if m:
        return CombatEvent(ts, "remote_rep_in", int(m.group("amount")), m.group("healer"),
                            m.group("module").strip(), m.group("kind"), clean)

    m = _REMOTE_REP_OUT_RE.match(clean)
    if m:
        return CombatEvent(ts, "remote_rep_out", int(m.group("amount")), m.group("recipient"),
                            m.group("module").strip(), m.group("kind"), clean)

    m = _JAMMED_RE.match(clean)
    if m:
        return CombatEvent(ts, "jammed", 0, m.group("attacker"), "", "", clean)

    m = _ENERGY_NEUT_RE.match(clean)
    if m:
        return CombatEvent(ts, "energy_neutralized", int(m.group("amount")), m.group("target"), "", "", clean)

    if stats is not None:
        stats.unmatched_samples.append(clean)
    return CombatEvent(ts, "other", 0, "", "", "", clean)


def iter_events_from_lines(lines: Iterable[str], stats: Optional[ParseStats] = None):
    for line in lines:
        parsed = parse_line(line)
        if not parsed:
            continue
        ts, channel, msg = parsed
        if channel != "combat":
            continue
        if stats is not None:
            stats.combat_lines += 1
        event = parse_combat_message(ts, msg, stats)
        if event and stats is not None and event.kind != "other":
            stats.matched += 1
        if event:
            yield event


def read_header_from_lines(lines: Iterable[str]) -> dict:
    """Read the 'Listener: <name>' / 'Session Started: ...' header of a gamelog file."""
    info: dict = {}
    for i, line in enumerate(lines):
        if i >= 6:
            break
        line = line.strip()
        if line.startswith("Listener:"):
            info["character"] = line.split(":", 1)[1].strip()
        elif line.startswith("Session Started:"):
            info["session_started"] = line.split(":", 1)[1].strip()
    return info


def summarize(events: Iterable[CombatEvent]) -> dict:
    """Reduce a stream of CombatEvents to a plain, JSON/model-field-ready dict.

    Mirrors the standalone tool's `Summary` class, minus the live-only fields
    (rolling DPS window, recent-event feed) that only matter while watching a
    log grow in real time — not for a finished-file upload.
    """
    damage_out = damage_in = 0
    hits_out = misses_out = hits_in = misses_in = 0
    quality_out: dict = {}
    quality_in: dict = {}
    damage_by_target: dict = {}
    damage_by_attacker: dict = {}
    reps_received_total = reps_given_total = 0
    reps_by_healer: dict = {}
    reps_by_recipient: dict = {}
    first_ts = last_ts = None

    for e in events:
        if first_ts is None or e.timestamp < first_ts:
            first_ts = e.timestamp
        if last_ts is None or e.timestamp > last_ts:
            last_ts = e.timestamp

        if e.kind == "damage_out":
            damage_out += e.amount
            hits_out += 1
            quality_out[e.quality] = quality_out.get(e.quality, 0) + 1
            damage_by_target[e.other_party] = damage_by_target.get(e.other_party, 0) + e.amount
        elif e.kind == "damage_in":
            damage_in += e.amount
            hits_in += 1
            quality_in[e.quality] = quality_in.get(e.quality, 0) + 1
            damage_by_attacker[e.other_party] = damage_by_attacker.get(e.other_party, 0) + e.amount
        elif e.kind == "miss_out":
            misses_out += 1
            quality_out["Misses"] = quality_out.get("Misses", 0) + 1
        elif e.kind == "miss_in":
            misses_in += 1
            quality_in["Misses"] = quality_in.get("Misses", 0) + 1
        elif e.kind == "remote_rep_in":
            reps_received_total += e.amount
            reps_by_healer[e.other_party] = reps_by_healer.get(e.other_party, 0) + e.amount
        elif e.kind == "remote_rep_out":
            reps_given_total += e.amount
            reps_by_recipient[e.other_party] = reps_by_recipient.get(e.other_party, 0) + e.amount

    def top(d: dict, n: int = 8) -> list:
        return [[k, v] for k, v in sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n]]

    return {
        "session_started": first_ts,
        "session_ended": last_ts,
        "damage_out": damage_out,
        "damage_in": damage_in,
        "shots_out": hits_out + misses_out,
        "shots_in": hits_in + misses_in,
        "quality_out": [[q, quality_out[q]] for q in QUALITY_ORDER if quality_out.get(q)],
        "quality_in": [[q, quality_in[q]] for q in QUALITY_ORDER if quality_in.get(q)],
        "top_targets": top(damage_by_target),
        "top_attackers": top(damage_by_attacker),
        "reps_received_total": reps_received_total,
        "reps_given_total": reps_given_total,
        "reps_by_healer": top(reps_by_healer),
        "reps_by_recipient": top(reps_by_recipient),
    }
