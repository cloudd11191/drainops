import datetime as dt, logging, os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from . import config
from .db import SessionLocal, Drain, HealthSnapshot, User
from .routers import admin, auth, field, ops, public, reports, twin
from .security import hash_password

app = FastAPI(title="DrainOps AI API", version="0.2")
app.add_middleware(CORSMiddleware, allow_origins=config.ORIGINS, allow_methods=["*"], allow_headers=["*"], expose_headers=["Content-Disposition"])
for r in (auth.router, twin.router, field.router, ops.router, reports.router, public.router, admin.router): app.include_router(r)
os.makedirs(config.UPLOAD_DIR, exist_ok=True)

@app.on_event("startup")
def bootstrap():
    s = SessionLocal()
    try:
        if config.AUTH_MODE == "open": logging.warning("AUTH_MODE=open: authentication is OFF and everyone is admin. Local development only.")
        elif not s.query(User).count():
            if config.ADMIN_EMAIL and config.ADMIN_PASSWORD and len(config.ADMIN_PASSWORD) >= 10:
                s.add(User(email=config.ADMIN_EMAIL.strip().lower(), name="Administrator", role="admin", pw_hash=hash_password(config.ADMIN_PASSWORD))); s.commit()
                logging.info("Created first admin %s", config.ADMIN_EMAIL)
            else: logging.warning("No users exist. Set ADMIN_EMAIL and ADMIN_PASSWORD (10+ chars) to create the first admin.")
        if s.query(Drain).count() == 0 and os.path.exists("data/registry.xlsx"):
            import load_registry; load_registry.run()
        have = {r[0] for r in s.query(HealthSnapshot.drain_id).distinct()}
        for d in s.query(Drain):
            if d.id not in have: s.add(HealthSnapshot(drain_id=d.id, taken=d.last_inspected or dt.datetime.utcnow(), health=d.health, source="registry"))
        s.commit()
    finally:
        s.close()

# Frontend (index.html, login.html). KEEP LAST so API routes win.
if os.path.isdir("web"): app.mount("/", StaticFiles(directory="web", html=True), name="web")
