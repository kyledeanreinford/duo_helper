# duo_tracker

Daily Duolingo course-progress snapshots (section/unit position, units
completed, XP, streak) for the family's Portuguese course, one Postgres row
per person per day. Built because Duolingo's course-wide averages proved
useless for pacing (site said ~8.3 lessons/unit; actual unit 9 ran ~24) —
this logs real data so pace questions have answers.

Spec: `~/Obsidian/Home/DEV/Specs/Duolingo Helper.md`. Uses reverse-engineered
endpoints (no official API exists); shapes were confirmed live on 2026-07-11.
Read-only against Duolingo, personal use.

## Setup

```bash
uv sync
cp .env.example .env   # then fill in DB_URL + JWTs, see below
```

**Harvest a JWT** (per person; no passwords are stored anywhere):

1. Log into duolingo.com in a browser as that person
2. DevTools → Application → Cookies → `https://www.duolingo.com`
3. Copy the `jwt_token` cookie value into `DUO_JWT_<NAME>=` in `.env`

Tokens are long-lived (Kyle's expires in 2169). When one is rejected,
`snapshot` exits non-zero with a "re-harvest JWT for <person>" message.

**Dev database** — no local postgres on this machine; use the cluster one:

```bash
kubectl port-forward svc/postgres 5432:5432 -n monarch   # keep running
# one-time: kubectl exec -n monarch postgres-0 -- psql -U postgres -c 'CREATE DATABASE duo_tracker'
uv run duo-tracker migrate
```

## Commands

```bash
uv run duo-tracker probe [--person kyle]      # hit endpoints, dump raw JSON to data/, print shapes
uv run duo-tracker snapshot [--person kyle] [--date YYYY-MM-DD]   # the daily job (upserts)
uv run duo-tracker show [--person kyle] [--days 14]               # recent rows, sanity check
uv run duo-tracker backfill --since YYYY-MM-DD [--person kyle]    # past days from xp_summaries
uv run duo-tracker migrate                    # idempotent schema
```

`backfill` fills xp/lessons (and streak, reconstructed from streakExtended
flags inside the current streak only) for past days — path position is not
recoverable historically, those columns stay NULL. It never overwrites rows
that already exist; real snapshots win. Note xp_summaries is account-wide,
not per-course: only backfill windows where the person was doing this course
(Kyle's PT window starts 2026-06-15; already backfilled).

`snapshot` isolates per-person failures (one bad JWT doesn't block the
others) but exits non-zero if anyone failed, so a CronJob shows red.

## Data notes (hard-won, don't re-learn these)

- `raw_response` (full user + xp_summaries payloads) is the source of truth;
  typed columns are a convenience projection. The course structure has
  changed shape repeatedly (81 → 91 → 1031 units) — re-derive from raw.
- `xp_summaries[].date` is **midnight UTC** of the summary day. Convert with
  UTC or every entry lands a day early in America/Chicago.
- `unitIndex` on path units is **global across sections**; "Unit 9" as shown
  in the UI is the 1-based position within the section.
- Sections carry `completedUnits`/`totalUnits` directly; level-state
  derivation (`all stated levels == "passed"`) is the fallback. The
  `daily_refresh` section type is excluded from unit counts.
- The `fields=` query param on `/2017-06-30/users/{id}` is the flakiest part
  of the API surface; `probe` retries without it if `currentCourse` is missing.

## Adding Lindsey / Walker

Set `DUO_PEOPLE=kyle,lindsey,walker` and add `DUO_JWT_LINDSEY` /
`DUO_JWT_WALKER` to `.env`. No code changes.

## Phase 2 (not built yet)

Dockerfile + GitHub Actions → Harbor (clone monarch_helper's), manifests in
`~/Dev/k3s/k3s/duo/` mirroring `k3s/monarch/` (own namespace + postgres
StatefulSet, app secret with `DB_URL` + `DUO_JWT_*`, CronJob late evening
America/Chicago so `snapshot_date` captures the full day). The CronJob
failing loudly on an expired JWT *is* the alerting.
