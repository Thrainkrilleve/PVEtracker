# PVEtracker

> Fleet damage & logi tracking for [Alliance Auth](https://allianceauth.readthedocs.io/) — upload EVE combat logs, attribute stats to characters (and roll alts up to mains), and see a fleet leaderboard.

Each EVE client's combat log only ever records damage *involving that pilot* — there's no way to see a whole fleet's damage from one file. PVEtracker solves this the same way FAT solves fleet attendance: every pilot uploads their own combat log, the app verifies the log actually belongs to a character on their Alliance Auth account, and the results roll up into one fleet-wide leaderboard.

## Status

**v0.0.2 — Phase 1 (upload + leaderboard).** This is the upload-and-leaderboard half of the project; live in-browser log tailing (via the File System Access API, so a pilot's dashboard updates in real time while they fly) is planned for a later release and isn't in this version.

A note on naming: the installable/importable package is `aa_pvetracker` (distributed as `aa-pvetracker`), but the Django app *label* — used in permission strings (`pvetracker.basic_access`), the URL namespace (`{% url 'pvetracker:...' %}`), and `manage.py` app-label arguments (`manage.py test pvetracker`) — is the shorter `pvetracker`. This is a standard, supported Django distinction (`AppConfig.name` vs `AppConfig.label`), used here so the label doesn't need to change again if the distribution is ever renamed.

## Features

- **Drag-and-drop upload** of a Gamelog `.txt` file (from `Documents\EVE\logs\Gamelogs`) — falls back to a plain file picker with no JS.
- **Ownership-verified attribution** — a log is only accepted if its `Listener:` character is one the uploading user actually owns on Alliance Auth, so nobody can pad another pilot's numbers.
- **Main/alt roll-up** — leaderboard rows are grouped by each user's designated main character, exactly like FAT groups fleet participation, with a per-character breakdown on expand.
- **Per-session stats**: damage dealt/received, hit-quality breakdown, top targets/attackers, reps received (and an estimated reps-given figure — see Known Limitations).
- **My Uploads** page to review or delete your own uploads.
- **Dashboard widget** showing your own last-7-days damage and reps given.

## Known limitations (v0.0.2)

- **No live tracking yet.** This version only reads finished (or in-progress, but manually re-uploaded) log files — it doesn't watch a growing log in real time. That's planned for a later release using the browser's File System Access API.
- **`reps_given_total` is an estimate.** The regex for a logi pilot's own *outgoing* remote-repair line was written by mirroring the (fully verified) incoming-rep line shape, since we didn't have a real logi pilot's log to test against at the time. If your numbers look off, please open an issue with a sample line (amounts/names redacted is fine).
- **Trust model**: a log's *character* is cryptographically-adjacent verified (it must be one you own via Alliance Auth's SSO-backed character ownership), but the *numbers in it* are still just whatever your own client wrote to disk — there's no independent server-side corroboration.

## Requirements

- Python >= 3.10
- Alliance Auth >= 4.3.1, < 6

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

4. Grant the `pvetracker.basic_access` permission to whichever group(s) should be able to upload logs and see the leaderboard.

## Development

A lightweight SQLite dev harness (`manage.py` / `settings_dev.py` / `urls_dev.py`) is included for local iteration. It's enough to run the standalone parser tests and to confirm the migration matches model state, without a full Alliance Auth stack:

```bash
python -m venv .venv
.venv/Scripts/pip install allianceauth
.venv/Scripts/python -m unittest aa_pvetracker.tests.test_parsing

# confirm 0001_initial.py matches the current models (no changes detected):
DJANGO_SETTINGS_MODULE=settings_dev .venv/Scripts/python -c "
import django; django.setup()
from django.core.management import call_command
call_command('makemigrations', 'pvetracker', dry_run=True, check=True, skip_checks=True)"
```

It's *not* enough to run the full Django `TestCase` suite (`pvetracker/tests/test_views.py`) — Alliance Auth's own app registry expects the complete official app roster (it errors on `InvalidBasesError` with only the minimal set of apps this harness installs), and separately, several of its startup checks require a reachable Redis. Run the full suite inside your real Docker instance instead, where both are already correctly configured:

```bash
docker compose exec allianceauth_gunicorn python manage.py test pvetracker
```
