# Changelog

## Unreleased

- Events, session cards, and the section nav stay readable on Alliance Auth's dark themes. Darkly leaves `--bs-tertiary-bg` light and never sets `data-bs-theme="dark"`, so those surfaces were white under white text. Surfaces use `bg-body-tertiary` / `text-body` / `text-body-secondary`, the active tab is a `nav-pills` pill on `--bs-primary`, and Darkly remaps tertiary to `--bs-dark`.
- README now says v0.0.7, matching the package.
- Leaderboard roll-up lives in `aa_pvetracker.leaderboard` so the site, the dashboard rank, and a later Discord post share one grouping. Extra numeric columns can be summed later without a second grouping.
- Any logged-in member can view the leaderboard, a session, and the events list. `pvetracker.basic_access` is the upload and delete permission.
- Shared section nav (Leaderboard / Upload / My Uploads / Events), a session page that reuses the upload dashboard, and My Uploads plus the dashboard widget now show duration, last session, and rank.
- One checkbox hides your sessions from the events list. Leaderboard totals stay.
- Dashboard widget matches sessions by character row. On Alliance Auth 5 the EVE character id is not the primary key, so the old id comparison never matched an upload.
- `Payout` (character, time, type, amount, journal id) and `CombatLogUpload.ship_type_id` are in the schema for a later release. Nothing writes them yet.
- GitHub Actions runs the test suite on Alliance Auth 5.6.0.

## v0.0.7

Fix the leaderboard's per-character detail row rendering as a solid white bar — turned out to be a second, different bug from the v0.0.5 collapse fix. DevTools inspection confirmed the collapse mechanism itself was working correctly (Bootstrap marked the trigger `collapsed`/`aria-expanded="false"` as expected); the actual bug was Bootstrap's `bg-body-tertiary` utility not resolving to a dark background on a nested `<table class="table ...">` under Alliance Auth's Bootswatch-based theme (which pre-compiles a fixed theme rather than using Bootstrap 5.3's `data-bs-theme` variable-swap dark mode, so some table-specific background variables don't carry through the same way). Fixed by dropping Bootstrap's `.table` classes for this nested element and giving it fully explicit, self-contained CSS (`.pvet-detail-table`) instead — consistent with how the rest of this app's custom `.pvet-*` components are already styled, all of which were rendering correctly.

## v0.0.6

Closes the remaining visual gap with the standalone prototype's dashboard: a "Recent Activity" feed at the bottom of the upload result page, showing the last 60 events of the session (most recent first), same row layout and color-coding as the original. This required a small schema addition:

- New `CombatLogUpload.feed` JSONField (migration `0002_combatlogupload_feed`) — a capped, most-recent-first slice of the session's events, built in `parsing.summarize()` alongside the existing aggregates.
- Upload result page header now also shows session duration (`HH:MM:SS`) and the source filename, matching the prototype's compact header line.

## v0.0.5

- Fix the leaderboard's per-character expand row rendering as a solid white bar in dark mode. Bootstrap's `.collapse` doesn't animate `<tr>` elements correctly (table rows don't support the height transition it relies on), so the row was showing at an awkward in-between state with Bootstrap's default (light) table background instead of collapsing. Fixed by collapsing a `<div>` inside an always-present `<tr>`/`<td>` instead of collapsing the row itself — the standard workaround for this well-known Bootstrap limitation.

## v0.0.4

- Fix a stray absolute `from pvetracker.parsing import ...` in `test_parsing.py` left over from the v0.0.3 rename — missed because it's an absolute import rather than a relative one, so `git mv` didn't touch it and it wasn't caught until re-running the test suite post-rename. Caught immediately by that same test suite before this was reported by anyone; no functional code was affected, only the test module's own import.

## v0.0.3

Visual redesign — brought over the standalone prototype's dashboard look (big color-coded stat tiles, hit-quality bar charts) into the Alliance Auth app, built on Bootstrap 5 theme tokens so it still respects the site's light/dark toggle:

- Upload result page now shows full stat tiles (damage/DPS, reps received/given), top targets/attackers panels, and hit-quality bar charts — same data as before, much closer to the standalone dashboard's presentation.
- Leaderboard rows get numbered rank badges (gold/silver/bronze for top 3) and color-coded damage/rep columns (cyan/red/gold, matching the stat tiles).

## v0.0.2

- **Breaking (pre-install only):** renamed the importable package from `pvetracker` to `aa_pvetracker` so the `INSTALLED_APPS` entry matches the `aa-pvetracker` distribution name from `requirements.txt` (PEP 503 normalization aside — same name). If you already added `'pvetracker'` to `local.py`, change it to `'aa_pvetracker'`.
- Everything else (permission names, URL namespace, template tags, admin URLs) is unchanged — the Django app `label` stays `pvetracker` internally, so `pvetracker.basic_access` and `{% url 'pvetracker:...' %}` still work as documented.

## v0.0.1

Initial release — upload + leaderboard (Phase 1).

- Drag-and-drop (or file-picker) upload of an EVE Gamelog combat log.
- Server-side parsing ported from the standalone prototype, validated against real combat logs.
- Ownership-verified character attribution (log's `Listener:` must be a character the uploader owns).
- Fleet leaderboard with main/alt roll-up, date-range filter, and sortable columns.
- My Uploads page with delete.
- Alliance Auth dashboard widget.
