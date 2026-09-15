# Changelog

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
