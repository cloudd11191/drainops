# DrainOps AI API v0.2: built around `web/index.html`

Auth: `Authorization: Bearer <token>` from `POST /auth/login`. Roles: inspector, supervisor, engineer, admin.
`AUTH_MODE=open` (local dev only) turns authentication off and makes everyone admin.

## What each frontend feature calls
| Frontend feature (index.html) | Endpoint | Who |
|---|---|---|
| Page load, map, filters, KPIs | `GET /drains?rain_mm=&model=` (props: risk, severity, debris, plastic_share, mosquito_risk, days_since_cleaned, downstream) | public |
| Time slider | `GET /twin/timeline?weeks=10` | supervisor, engineer, admin |
| Network lines | `GET /twin/network`, `PUT /drains/{code}/downstream`, `GET /drains/{code}/impact` | read: sup/eng/admin · write: engineer, admin |
| AI inspector (photo) | `POST /inspect` (anonymous = stateless score; with `drain_code` + login = saved) | public / field roles |
| Queue inspection + Sync now | `POST /field-reports` (multipart, idempotent on `client_id`) | inspector, supervisor, admin |
| Save correction to training queue | `POST /feedback/corrections` (debris % must total 100) | inspector, supervisor, admin |
| Supervisor confirms a cleaning | `GET /field-reports?status=pending_review`, `POST /field-reports/{id}/review` | supervisor, admin |
| Priority work orders / crews | `POST /workorders/generate`, `GET /workorders`, `PATCH /workorders/{id}`, `GET/POST /contractors` | supervisor, admin |
| Rain alerts (top 20) | `GET /alerts/rain` | supervisor, engineer, admin |
| Ward report table + Export Excel CSV | `GET /reports/ward`, `GET /reports/ward.csv?month=YYYY-MM&ward=` (byte-identical layout to your export) | supervisor, engineer, admin |
| Contractor report | `GET /reports/contractors?month=` | supervisor, admin |
| Audit log list | `GET /audit`, `GET /audit/verify` (hash-chain check) | admin |
| Public ward scorecard | `GET /public/scorecard` | public |
| Public alerts | `GET /public/alerts` | public |
| Citizen report form | `POST /citizen-reports` (5/hour per connection) · triage: `GET/PATCH /citizen-reports` | public · supervisor, admin |
| Staff login | `POST /auth/login`, `GET /auth/me`; admin: `GET/POST /users`, `PATCH /users/{id}/active` | |
| Photos / voice (supervisor review) | `GET /media/{name}` | supervisor, admin |

## Rules the server enforces (the frontend cannot)
* **A cleaning counts as verified only if:** before+after photos, GPS present and within `MAX_GPS_DIST_M` of the drain, after later than before (≤ `MAX_PAIR_GAP_H`), device clock sane, health gain ≥ `MIN_GAIN`.
  Scores from the **demo heuristic never self-verify**: they go to `pending_review` and a supervisor must confirm (with their own `health_after`).
  `last_cleaned`, "Verified cleanings", contractor stats and work-order closure all hang off verified reports only.
* Demo-heuristic scores are stored but **never overwrite a drain's health**.
* Overriding failed checks requires a note; every verify/reject/correction/export/login failure is written to a hash-chained audit log.
* Photos are decoded and re-encoded (EXIF/GPS removed); uploads are size-limited and served only to supervisors/admins.
* Contractor "repeat blockage rate" uses trained-model re-inspections only, otherwise `null`, never a false 0%.

## Environment
`SECRET_KEY` (required in prod) · `ADMIN_EMAIL` + `ADMIN_PASSWORD` (10+ chars; creates the first admin when no users exist) · `DATABASE_URL` · `ALLOWED_ORIGINS` ·
`AUTH_MODE` · `TOKEN_TTL_MIN` · `MIN_GAIN` · `MAX_GPS_DIST_M` · `MAX_PAIR_GAP_H` · `AUTO_VERIFY` · `RAIN_ALERT_MM` · `UPLOAD_DIR` · `MAX_UPLOAD_MB` · `MODEL_PATH`

Optional AI photo scoring: set `GROQ_API_KEY` as a server environment secret and optionally override `GROQ_MODEL` (default `meta-llama/llama-4-scout-17b-16e-instruct`). The key is only read by the API and is never sent to the browser. When configured, `/inspect` uses Groq vision; without it, the existing local model/demo path is used. Groq scores are estimates, are not treated as trained-model verification, and should be reviewed by a person.

## Known gaps
* Frontend's own risk formula differs from the server's (see `model=legacy` vs `calibrated`); prefer reading `risk` from `/drains`.
* No SMS/WhatsApp sending (alerts are shown, not delivered). Drain connectivity and ward polygons are not in the registry: supply them to light up the network layer.
* `/media` and rate limits are per-process; use object storage and a shared limiter behind several instances.
