"""Environment-driven settings."""
import os, secrets, logging

def _b(name, default): return os.getenv(name, str(default)).lower() in ("1", "true", "yes")

AUTH_MODE = os.getenv("AUTH_MODE", "required")          # "required" (default) | "open" (local dev ONLY: everyone is admin)
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_urlsafe(48)
if not os.getenv("SECRET_KEY"):
    logging.warning("SECRET_KEY not set: using an ephemeral key, so logins reset on every restart. Set SECRET_KEY in production.")
TOKEN_TTL_S = int(os.getenv("TOKEN_TTL_MIN", "480")) * 60
ADMIN_EMAIL, ADMIN_PASSWORD = os.getenv("ADMIN_EMAIL"), os.getenv("ADMIN_PASSWORD")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_MB", "8")) * 1024 * 1024
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")]
# Cleaning-verification policy (see app/verification.py)
MIN_GAIN = float(os.getenv("MIN_GAIN", "10"))
MAX_GPS_DIST_M = float(os.getenv("MAX_GPS_DIST_M", "250"))   # registry coordinates are rounded to ~100 m
MAX_PAIR_GAP_H = float(os.getenv("MAX_PAIR_GAP_H", "72"))
AUTO_VERIFY = _b("AUTO_VERIFY", True)                         # only ever applies to scores from a trained (onnx) model
RAIN_ALERT_MM = float(os.getenv("RAIN_ALERT_MM", "35"))
ROLES = ("inspector", "supervisor", "engineer", "admin")
