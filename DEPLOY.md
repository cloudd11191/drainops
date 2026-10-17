# Deploy DrainOps AI (one service: API + frontend)

## Run locally
    pip install -r requirements.txt
    uvicorn app.main:app --reload      # http://localhost:8000
First start on an empty database loads the 101-drain registry automatically (it never overwrites existing data).
Check `/health` (model_mode) and `/docs` (API).

## Docker
    docker build -t drainops .
    docker run -p 8000:8000 drainops

## Host it (Render / Railway / Fly.io, any Docker-capable host)
1. Push this folder to GitHub.
2. Create a web service from the repo (Docker). It reads $PORT automatically.
3. Create a managed PostgreSQL database and set `DATABASE_URL` on the service
   (`postgres://` URLs are converted automatically). Without it the app uses SQLite on a disk that
   is usually wiped on redeploy.
4. Optional env vars: `ALLOWED_ORIGINS` (comma-separated frontend URLs if hosted separately),
   `MODEL_PATH` (default models/drain_model.onnx), and `GROQ_API_KEY` (set as a server-side secret
   to enable Groq vision scoring; optionally set `GROQ_MODEL` to choose a supported vision model).
   Never put provider keys in `web/` or any browser-side JavaScript.
5. Open the service URL. The dashboard should load drains from the API.
   If the API is unreachable the page falls back to its embedded copy of the registry.

## Separate frontend host (Netlify / Cloudflare Pages / GitHub Pages)
Upload `web/index.html` and add before its main script:
    <script>window.DRAINOPS_API = "https://your-api.example.com";</script>
and set `ALLOWED_ORIGINS` on the API to the frontend's https URL.

## Add the trained model
Put `drain_model.onnx` in `models/` (commit it, or use a persistent disk) and redeploy.
`/health` then reports `"model_mode": "onnx"` and the inspector shows "Trained model".

## Before real municipal use
Add login with inspector/supervisor roles, rate limiting, and object storage for photos
(files in `uploads/` vanish on most hosts at redeploy).

## Upgrading an existing database
New columns (inspection GPS/checklist/voice fields, drain flood_history) are added automatically on start.

## v0.2 environment (see API.md)
Set `SECRET_KEY` (long random string), `ADMIN_EMAIL`, `ADMIN_PASSWORD` (10+ chars) and `DATABASE_URL`.
Without ADMIN_* no one can sign in. For local dev only, `AUTH_MODE=open` disables authentication.
