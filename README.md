# PVEtracker

> Fleet damage & logi tracking for [Alliance Auth](https://allianceauth.readthedocs.io/) — upload EVE combat logs, attribute stats to characters (and roll alts up to mains), and see a fleet leaderboard.

Each EVE client's combat log only ever records damage *involving that pilot* — there's no way to see a whole fleet's damage from one file. PVEtracker solves this the same way FAT solves fleet attendance: every pilot uploads their own combat log, the app verifies the log actually belongs to a character on their Alliance Auth account, and the results roll up into one fleet-wide leaderboard.

## Status

Alliance Auth 5.x only (`allianceauth>=5.0.0,<6`). Alliance Auth 4 is not supported. Combat stats still come from uploaded EVE Gamelog `.txt` files. The current version is in [`aa_pvetracker/__init__.py`](aa_pvetracker/__init__.py), and changes are listed in the [changelog](CHANGELOG.md).

Any logged-in member can view the leaderboard, a stored session, and the events list. Uploading and deleting logs needs `pvetracker.basic_access`.

A note on naming: the installable/importable package is `aa_pvetracker` (distributed as `aa-pvetracker`), but the Django app *label* — used in permission strings (`pvetracker.basic_access`), the URL namespace (`{% url 'pvetracker:...' %}`), and migration app labels — is the shorter `pvetracker`. This is a standard, supported Django distinction (`AppConfig.name` vs `AppConfig.label`). Test discovery follows the import package, so the suite is `manage.py test aa_pvetracker`, not `pvetracker`.

## Features

- **Drag-and-drop upload** of a Gamelog `.txt` file (from `Documents\EVE\logs\Gamelogs`) — falls back to a plain file picker with no JS. Upload and delete require `pvetracker.basic_access`.
- **Ownership-verified attribution** — a log is only accepted if its `Listener:` character is one the uploading user actually owns on Alliance Auth.
- **Main/alt roll-up** — leaderboard rows are grouped by each user's designated main character, with a per-character breakdown on expand. The grouping lives in `aa_pvetracker.leaderboard` so later Discord and ISK/kill columns can use the same buckets.
- **Re-uploading a growing log updates the same session.** The EVE client keeps writing to one file per client session. A session is identified by its character plus the log header's `Session Started:` time, so uploading a longer copy replaces that session's totals instead of counting the fight twice.
- **Fleet leaderboard** for the last 7 days, last 30 days, or all time, with rank badges and sortable damage and rep columns. The 7- and 30-day windows go by when the fight happened (the log's first combat line, in EVE time / UTC), not by when the log was uploaded.
- **Session page** — the upload-result dashboard (tiles, hit-quality bars, top targets and attackers, recent activity) has a stable URL, linked from My Uploads. Any logged-in member can open it.
- **My Uploads** shows duration, your last session, and your 30-day damage rank.
- **Events** lists recent lines from uploaded sessions. One checkbox hides your sessions from that list; leaderboard totals are unchanged. Live tailing is not in this version.
- **Dashboard widget** with your last-7-days damage and reps, last session, and 7-day damage rank.
- **Shared section nav** on every page: Leaderboard, Upload, My Uploads, Events. Upload and My Uploads are shown only when you can upload.

## Known limitations

- **No live tracking yet.** This version only reads finished (or in-progress, but manually re-uploaded) log files. Sessions uploaded before the "same session" matching was added have no header time stored, so a re-upload of one of those logs is stored as a new session; delete the old one from My Uploads. A later release is planned around the browser File System Access API.
- **No ESI wallet ingest, zKill feed, or Discord post yet.** A `Payout` table (character, time, type, amount, journal id) and a per-session `ship_type_id` are reserved and empty.
- **Damage, payout ISK, and kill value stay separate.** This version only has the damage and rep columns.
- **`reps_given_total` is an estimate.** The regex for a logi pilot's own *outgoing* remote-repair line was written by mirroring the incoming-rep line shape, since a real logi pilot's log was not available to test against. If the numbers look off, open an issue with a sample line (amounts and names redacted is fine).
- **Trust model**: a log's *character* has to be one you own via Alliance Auth, but the *numbers in it* are still whatever that client wrote to disk.

## Requirements

- Python >= 3.10
- Alliance Auth >= 5.0.0, < 6
- Redis (Alliance Auth already requires it)

## Installation

1. Install the package:

   ```bash
   pip install aa-pvetracker
   ```

2. Add `"aa_pvetracker"` to `INSTALLED_APPS` in your Alliance Auth settings file (e.g. `local.py`).

3. Rebuild the image, run migrations, and collect static files:

   ```bash
   docker compose build
   docker compose exec allianceauth_gunicorn bash -c "auth migrate && auth collectstatic --noinput"
   docker compose up -d
   ```

4. Grant `pvetracker.basic_access` to the group(s) that should upload and delete logs. Viewing does not need it: any logged-in member can open the leaderboard, sessions, and events.

## Development

The SQLite harness (`manage.py`, `settings_dev.py`, `urls_dev.py`) runs the parser tests, the leaderboard-helper tests, and the Django view tests. Alliance Auth's own system checks need a Redis server and the placeholder settings in `settings_dev.py` (every URL there is `https://example.com`; nothing in that file is a credential).

```bash
python -m pip install "allianceauth==5.6.0"
python -m pip install -e . --no-deps

# no database: parser + roll-up helper
python -m unittest aa_pvetracker.tests.test_parsing aa_pvetracker.tests.test_leaderboard

# full suite, including views (Redis on localhost:6379)
python manage.py test aa_pvetracker

# migration matches the models:
python manage.py makemigrations pvetracker --check --dry-run
```

`settings_dev.py` is not a production settings file. Confirm the pages against your real Alliance Auth theme in the Auth container when you care about Bootswatch colors; the nested leaderboard detail table is styled with the app's own classes for that reason.

GitHub Actions runs `python manage.py test aa_pvetracker` and the `makemigrations --check` step on Python 3.12 with Redis 7.4, against both the oldest supported Alliance Auth (5.0.0) and 5.6.0.
