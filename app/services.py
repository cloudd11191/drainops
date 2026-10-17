"""Shared query helpers: drain serialisation, ranking, image/voice storage."""
import base64, io, json, logging, math, os, uuid, datetime as dt
import httpx
from fastapi import HTTPException, UploadFile
from PIL import Image
from sqlalchemy import func
from . import config
from .db import Drain, Inspection, HealthSnapshot
from .inference import predict
from .insights import debris_type, debris_shares, mosquito_risk, severity
from .risk import compute_risk, legacy_dashboard_risk
from .weather import forecasts, cell

DEFAULT_RAIN = 18.0   # mm; matches the dashboard slider default

def days_since(t):
    return (dt.datetime.utcnow() - t).days if t else None

def drain_risk(d, rain_mm, model="calibrated"):
    if model == "legacy": return float(legacy_dashboard_risk(d.health, d.width_m, d.depth_m, rain_mm))
    return compute_risk(d.health, rain_mm, days_since(d.last_cleaned), d.width_m * d.depth_m, d.flood_history).score

def latest_inspections(s) -> dict:
    ids = [r[0] for r in s.query(func.max(Inspection.id)).filter(Inspection.plastic.isnot(None)).group_by(Inspection.drain_id)]
    return {i.drain_id: i for i in s.query(Inspection).filter(Inspection.id.in_(ids))} if ids else {}

def drain_props(d, rain_mm, latest=None, model="calibrated"):
    i = (latest or {}).get(d.id)
    mix = {"plastic": i.plastic, "silt": i.silt, "organic": i.organic} if i else None
    sh = debris_shares(d.blockage_condition, mix)
    r = drain_risk(d, rain_mm, model)
    return {"id": d.id, "code": d.code, "name": d.name, "ward": d.municipality or d.ward, "ward_no": d.ward_no, "municipality": d.municipality,
            "state": d.state, "condition": d.blockage_condition, "health": d.health, "color": "green" if d.health >= 66 else "amber" if d.health >= 33 else "red",
            "width_m": d.width_m, "depth_m": d.depth_m, "flood_history": d.flood_history, "downstream": None,
            "days_since_cleaned": days_since(d.last_cleaned), "last_inspected": d.last_inspected.isoformat() if d.last_inspected else None,
            "debris": debris_type(d.blockage_condition), "debris_shares": sh, "plastic_share": sh["plastic"],
            "mosquito_risk": mosquito_risk(d.health, sh["organic"]), "risk": round(r), "severity": severity(r),
            "estimated_fields": ["mosquito_risk", "plastic_share"]}

async def ranked(s, rain_override=None, state=None, model="calibrated"):
    q = s.query(Drain)
    if state: q = q.filter_by(state=state)
    ds = q.all(); fc = {} if rain_override is not None else await forecasts([(d.lat, d.lon) for d in ds])
    out = []
    for d in ds:
        rain = rain_override if rain_override is not None else fc.get(cell(d.lat, d.lon))
        r = drain_risk(d, rain or 0.0, model)
        out.append({"drain_id": d.id, "code": d.code, "name": d.name, "ward": d.municipality, "state": d.state, "lat": d.lat, "lon": d.lon,
                    "rain_mm": None if rain is None else round(rain, 1), "forecast_available": rain is not None, "risk": round(r, 1), "severity": severity(r),
                    "registry_priority": d.registry_priority})
    return sorted(out, key=lambda x: -x["risk"])

def snapshot(s, drain, source):
    s.add(HealthSnapshot(drain_id=drain.id, health=drain.health, source=source))

async def read_limited(f: UploadFile, limit=config.MAX_UPLOAD) -> bytes:
    data = await f.read(limit + 1)
    if len(data) > limit: raise HTTPException(413, f"File too large (max {limit // 1048576} MB)")
    return data

def store_image(data: bytes) -> str:
    """Decode, drop EXIF/GPS metadata, downscale and re-encode as JPEG. Rejects anything that is not an image."""
    try:
        im = Image.open(io.BytesIO(data)); im.load(); im = im.convert("RGB")
    except Exception:
        raise HTTPException(400, "Not a readable image")
    im.thumbnail((1600, 1600)); os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    name = uuid.uuid4().hex + ".jpg"; im.save(os.path.join(config.UPLOAD_DIR, name), "JPEG", quality=85)
    return name

def store_voice(data: bytes, filename: str) -> str:
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    ext = ext if ext in ("webm", "ogg", "m4a", "wav", "mp4") else "bin"
    os.makedirs(config.UPLOAD_DIR, exist_ok=True); name = uuid.uuid4().hex + "." + ext
    open(os.path.join(config.UPLOAD_DIR, name), "wb").write(data); return name

def score_photo(data: bytes) -> dict: return predict(data)

async def score_photo_with_groq(data: bytes) -> dict:
    """Use Groq vision when configured; otherwise keep the local demo/model path."""
    if not config.GROQ_API_KEY:
        return score_photo(data)

    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
        image.thumbnail((1024, 1024))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=82, optimize=True)
    except Exception as exc:
        raise ValueError("Not a readable image") from exc

    prompt = (
        "Assess this drain photo for visible blockage and debris. Treat the result as an "
        "estimate, not a verified inspection. Do not infer details that are not visible. "
        "Return JSON only with keys: health (integer 0-100, where 100 is clear), "
        "confidence (number 0-1; your own uncertainty estimate, not calibrated), and "
        "debris (object with plastic, silt, organic numbers from 0 to 1 that sum to 1). "
        "Use health >= 66 for mostly clear, 33-65 for moderate, and below 33 for critical."
    )
    payload = {
        "model": config.GROQ_MODEL,
        "temperature": 0,
        "max_tokens": 180,
        "response_format": {"type": "json_object"},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
                }},
            ],
        }],
    }

    try:
        async with httpx.AsyncClient(timeout=35) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                json=payload,
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        result = json.loads(content)
        health = result["health"]
        confidence = result["confidence"]
        debris = result["debris"]
        if isinstance(health, bool) or not isinstance(health, (int, float)):
            raise ValueError("Invalid health score")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("Invalid confidence score")
        if not isinstance(debris, dict):
            raise ValueError("Invalid debris breakdown")
        shares = [debris[name] for name in ("plastic", "silt", "organic")]
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in shares):
            raise ValueError("Invalid debris share")
        if any(not math.isfinite(float(value)) for value in shares):
            raise ValueError("Non-finite debris share")
        health = float(health)
        confidence = float(confidence)
        if not math.isfinite(health) or not math.isfinite(confidence):
            raise ValueError("Non-finite model score")
        health = max(0, min(100, health))
        confidence = max(0, min(1, confidence))
        total = sum(max(0, float(value)) for value in shares)
        if total == 0 or not math.isfinite(total):
            raise ValueError("Empty debris breakdown")
        normalized = [max(0, float(value)) / total for value in shares]
    except httpx.HTTPStatusError as exc:
        logging.error("Groq image analysis returned HTTP %s", exc.response.status_code)
        raise HTTPException(502, "Image analysis provider rejected the request") from exc
    except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
        logging.error("Groq image analysis failed: %s", type(exc).__name__)
        raise HTTPException(502, "Image analysis provider is unavailable or returned an invalid result") from exc

    tier = "Clear" if health >= 66 else "Moderate" if health >= 33 else "Critical"
    return {
        "tier": tier,
        "health": round(health, 1),
        "confidence": round(confidence, 3),
        "debris": {name: round(value, 2) for name, value in zip(("plastic", "silt", "organic"), normalized)},
        "model_mode": "groq_vision",
        "quality_issues": [],
        "needs_review": confidence < 0.6,
    }
