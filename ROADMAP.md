# Roadmap

What's planned for Pilot Stats, roughly in order. Released work is in the [changelog](CHANGELOG.md).

## For pilots

1. **ISK per hour** *(in review)*. Bounty and mission payouts earned during a logged session, divided by its length. Shown on the session page, and as a leaderboard column weighted by logged hours.
2. **Damage-over-time chart on the session page** *(in review)*. Per-minute DPS and reps for the session, to show ramp-up, downtime between waves, and when logi was busy. Needs per-minute totals stored with each session.
3. **Personal trends** *(in review)*. A "My stats" page with DPS, ISK/h and rats killed per week over time, and the pilot's personal records.

## For fleets and leadership

4. **Activity type per session.** Classify sessions as anomalies, incursions, abyssals, missions and so on, from the NPC types in bounty ticks and combat logs, so the leaderboard compares like with like.
5. **Corporation and alliance totals.** Weekly and monthly totals per corporation next to the per-pilot leaderboard, plus a CSV export.

## Quality and upkeep

6. **MariaDB in CI.** Real sites run MariaDB or MySQL, and the rename upgrade has a MySQL-specific path. CI only runs SQLite today.
7. **Leaderboard caching.** The leaderboard is computed on every page load. A short cache, cleared when new data arrives, keeps it fast as months of sessions, payouts and killmails build up.

## Automatic upload without a browser tab

8. **AA Desktop integration.** The AA Desktop app watches the Gamelogs folder and uploads new and grown logs through the AA Desktop bridge, signed in with the pilot's Alliance Auth login. Pilot Stats' side is a small optional hook with an upload endpoint. The in-browser folder scanner on the Upload page stays for everyone else.
