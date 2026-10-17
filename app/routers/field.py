import datetime as dt, json
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from .. import audit, config
from ..db import Drain, FieldReport, Inspection, TrainingExample, WorkOrder
from ..deps import db, optional_user, require
from ..security import RateLimiter
from ..services import read_limited, score_photo_with_groq, snapshot, store_image, store_voice
from ..verification import Evidence, evaluate_cleaning

router = APIRouter(tags=["field"])
scan_limiter = RateLimiter(30, 60)
FIELD = ("inspector", "supervisor", "admin")

def _ts(v):
    if not v: return None
    try:
        t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        return t.astimezone(dt.timezone.utc).replace(tzinfo=None) if t.tzinfo else t
    except ValueError: return None

async def _score(data):
    try: return await score_photo_with_groq(data)
    except HTTPException: raise
    except Exception: raise HTTPException(400, "Not a readable image")

@router.post("/inspect")
async def inspect(request: Request, photo: UploadFile = File(...), drain_code: str = Form(None), s=Depends(db), u=Depends(optional_user)):
    """Score one photo. Anonymous callers get a stateless score (rate-limited). With drain_code, an inspector/supervisor/admin
    records it as a routine inspection. Scores from the demo heuristic are stored but never overwrite a drain's health."""
    if not scan_limiter.allow(request.client.host if request.client else "?"): raise HTTPException(429, "Too many requests")
    data = await read_limited(photo); res = await _score(data)
    if not drain_code: return res
    if not u: raise HTTPException(401, "Sign in to save an inspection", headers={"WWW-Authenticate": "Bearer"})
    if u.role not in FIELD: raise HTTPException(403, "Requires role: " + ", ".join(FIELD))
    d = s.query(Drain).filter_by(code=drain_code).first()
    if not d: raise HTTPException(404, "Unknown drain code")
    i = Inspection(drain_id=d.id, kind="routine", tier=res["tier"], health=res["health"], confidence=res["confidence"], plastic=res["debris"]["plastic"],
                   silt=res["debris"]["silt"], organic=res["debris"]["organic"], model_mode=res["model_mode"], photo_path=store_image(data), inspector=u.name)
    s.add(i); d.last_inspected = dt.datetime.utcnow()
    applied = res["model_mode"] == "onnx"
    if applied: d.health = res["health"]; snapshot(s, d, "inspection")
    s.commit()
    return {**res, "inspection_id": i.id, "applied_to_twin": applied}

def _summary(r, d):
    return {"id": r.id, "client_id": r.client_id, "drain": d.code, "status": r.status, "model_mode": r.model_mode,
            "before_health": r.before_health, "after_health": r.after_health,
            "gain": None if r.before_health is None or r.after_health is None else round(r.after_health - r.before_health, 1),
            "checks": json.loads(r.checks_json or "{}").get("checks"), "reasons": json.loads(r.checks_json or "{}").get("reasons", [])}

def apply_verified(s, r, d, actor, role, health_override=None):
    """Shared by auto-verification and supervisor review: records the cleaning, closes the work order, updates the twin."""
    now = dt.datetime.utcnow(); r.status = "verified"; r.verified_at = now; d.last_cleaned = r.after_at or now
    new_h = health_override if health_override is not None else (r.after_health if r.model_mode == "onnx" else None)
    if new_h is not None: d.health = float(new_h); snapshot(s, d, "verified_cleaning")
    wo = s.get(WorkOrder, r.work_order_id) if r.work_order_id else None
    if not (wo and wo.drain_id == d.id):
        wo = s.query(WorkOrder).filter(WorkOrder.drain_id == d.id, WorkOrder.status.in_(("assigned", "in_progress", "completed"))).order_by(WorkOrder.id.desc()).first()
    if wo: wo.status = "verified"; wo.verified_at = now; wo.completed = wo.completed or r.after_at or now; r.work_order_id = wo.id
    return new_h

@router.post("/field-reports")
async def field_report(client_id: str = Form(..., max_length=80), drain_code: str = Form(...), before_at: str = Form(None), after_at: str = Form(None),
                       lat: float = Form(None, ge=-90, le=90), lon: float = Form(None, ge=-180, le=180), depth_cm: float = Form(None, ge=0, le=500),
                       cover_type: str = Form(None, max_length=60), notes: str = Form(None, max_length=2000), flood: int = Form(0, ge=0, le=1),
                       work_order_id: int = Form(None), before_photo: UploadFile = File(None), after_photo: UploadFile = File(None),
                       voice: UploadFile = File(None), s=Depends(db), u=Depends(require(*FIELD))):
    """One offline-queue item: checklist + optional before/after pair + voice note. Idempotent on client_id, so retries never duplicate."""
    d = s.query(Drain).filter_by(code=drain_code).first()
    if not d: raise HTTPException(404, "Unknown drain code")
    ex = s.query(FieldReport).filter_by(client_id=client_id).first()
    if ex: return {**_summary(ex, d), "duplicate": True}
    photos = {k: f for k, f in (("before", before_photo), ("after", after_photo)) if f is not None and f.filename}
    if not photos: raise HTTPException(422, "Attach a before and/or after photo")
    now = dt.datetime.utcnow()
    r = FieldReport(client_id=client_id, drain_id=d.id, actor=u.name, lat=lat, lon=lon, depth_cm=depth_cm, cover_type=cover_type, notes=notes,
                    before_at=_ts(before_at) or now if "before" in photos else None, after_at=_ts(after_at) or now if "after" in photos else None,
                    work_order_id=work_order_id, status="incomplete")
    if voice is not None and voice.filename: r.voice_path = store_voice(await read_limited(voice, 5 * 1048576), voice.filename)
    scored = {}
    for k, f in photos.items():
        data = await read_limited(f); res = await _score(data); scored[k] = res
        i = Inspection(drain_id=d.id, kind=k, tier=res["tier"], health=res["health"], confidence=res["confidence"], plastic=res["debris"]["plastic"], silt=res["debris"]["silt"],
                       organic=res["debris"]["organic"], model_mode=res["model_mode"], photo_path=store_image(data), inspector=u.name, client_id=f"{client_id}-{k}",
                       lat=lat, lon=lon, depth_cm=depth_cm, cover=cover_type, note=notes)
        s.add(i); s.flush(); setattr(r, k + "_id", i.id); setattr(r, k + "_health", res["health"])
    modes = {v["model_mode"] for v in scored.values()}
    mode = next(iter(modes)) if len(modes) == 1 else "demo_heuristic"
    r.model_mode = mode if mode in ("onnx", "groq_vision") else "demo_heuristic"
    s.add(r)
    try: s.flush()
    except IntegrityError:
        s.rollback(); ex = s.query(FieldReport).filter_by(client_id=client_id).first()
        if ex: return {**_summary(ex, d), "duplicate": True}
        raise
    for k in photos: s.query(Inspection).filter_by(id=getattr(r, k + "_id")).update({"report_id": r.id})
    ev = Evidence("before" in photos, "after" in photos, d.lat, d.lon, lat, lon,
                  r.before_at.replace(tzinfo=dt.timezone.utc).timestamp() if r.before_at else None, r.after_at.replace(tzinfo=dt.timezone.utc).timestamp() if r.after_at else None,
                  now.replace(tzinfo=dt.timezone.utc).timestamp(), r.before_health, r.after_health, r.model_mode)
    out = evaluate_cleaning(ev, config.MIN_GAIN, config.MAX_GPS_DIST_M, config.MAX_PAIR_GAP_H, config.AUTO_VERIFY)
    r.checks_json = json.dumps(out); r.status = out["status"]
    d.last_inspected = now
    if flood and "before" in photos: d.flood_history = (d.flood_history or 0) + 1
    if r.status == "verified":
        apply_verified(s, r, d, "system", "system")
        audit.write(s, "system", "system", "Cleaning auto-verified", d.code, f"report {r.id}: health gain {out['gain']}, GPS {out['distance_m']} m from drain, trained model")
    elif r.model_mode == "onnx":
        d.health = r.after_health if r.after_health is not None else r.before_health; snapshot(s, d, "field_report")
    s.commit()
    return {**_summary(r, d), "duplicate": False}

@router.get("/field-reports")
def list_reports(status: str = None, limit: int = 100, s=Depends(db), u=Depends(require("supervisor", "engineer", "admin"))):
    q = s.query(FieldReport, Drain).join(Drain, Drain.id == FieldReport.drain_id)
    if status: q = q.filter(FieldReport.status == status)
    return [{**_summary(r, d), "actor": r.actor, "created": r.created.isoformat(), "notes": r.notes, "depth_cm": r.depth_cm, "cover_type": r.cover_type}
            for r, d in q.order_by(FieldReport.id.desc()).limit(min(limit, 500))]

class ReviewIn(BaseModel):
    decision: str = Field(pattern="^(verify|reject)$"); note: str = Field("", max_length=500); health_after: int | None = Field(None, ge=0, le=100)

@router.post("/field-reports/{rid}/review")
def review(rid: int, body: ReviewIn, s=Depends(db), u=Depends(require("supervisor", "admin"))):
    r = s.get(FieldReport, rid)
    if not r: raise HTTPException(404)
    if r.status != "pending_review": raise HTTPException(409, f"Report is '{r.status}', not pending review")
    d = s.get(Drain, r.drain_id); info = json.loads(r.checks_json or "{}")
    failed = [k for k, v in (info.get("checks") or {}).items() if not v]
    if body.decision == "reject":
        r.status = "rejected"; r.reviewed_by = u.name; r.review_note = body.note
        audit.write(s, u.name, u.role, "Cleaning rejected", d.code, f"report {r.id}; failed checks: {', '.join(failed) or 'none'}; {body.note}")
    else:
        if failed and not body.note.strip(): raise HTTPException(422, "A note is required to verify a report that failed automatic checks")
        if r.model_mode != "onnx" and body.health_after is None: raise HTTPException(422, "Scores came from the demo heuristic: give health_after from your own judgement")
        r.reviewed_by = u.name; r.review_note = body.note
        before = d.health; new_h = apply_verified(s, r, d, u.name, u.role, body.health_after)
        audit.write(s, u.name, u.role, "Cleaning verified by supervisor", d.code,
                    f"report {r.id}; overridden checks: {', '.join(failed) or 'none'}; health {before:g}→{new_h if new_h is not None else before:g}; {body.note}")
    s.commit(); return _summary(r, d)

@router.post("/feedback/corrections")
async def correction(client_id: str = Form(..., max_length=80), photo: UploadFile = File(...), corrected_health: int = Form(..., ge=0, le=100),
                     plastic: int = Form(..., ge=0, le=100), silt: int = Form(..., ge=0, le=100), organic: int = Form(..., ge=0, le=100),
                     predicted_health: float = Form(None), predicted_plastic: float = Form(None), predicted_silt: float = Form(None), predicted_organic: float = Form(None),
                     confidence: float = Form(None, ge=0, le=1), model_mode: str = Form(None, max_length=30), drain_code: str = Form(None),
                     s=Depends(db), u=Depends(require(*FIELD))):
    """Training feedback from the 'Save correction' form. Debris percentages must total exactly 100. Does not change any drain's score."""
    if plastic + silt + organic != 100: raise HTTPException(422, "Debris percentages must total exactly 100")
    ex = s.query(TrainingExample).filter_by(client_id=client_id).first()
    if ex: return {"id": ex.id, "status": "queued_for_training", "duplicate": True}
    d = s.query(Drain).filter_by(code=drain_code).first() if drain_code else None
    t = TrainingExample(client_id=client_id, drain_id=d.id if d else None, photo_path=store_image(await read_limited(photo)), corrected_health=corrected_health,
                        plastic=plastic / 100, silt=silt / 100, organic=organic / 100, predicted_health=predicted_health, confidence=confidence, model_mode=model_mode, actor=u.name, role=u.role)
    s.add(t)
    pred = "" if predicted_health is None else f"Health {predicted_health:g}→{corrected_health}; "
    pd_ = "" if predicted_plastic is None else f"plastic {predicted_plastic:g}%→{plastic}%, silt {predicted_silt:g}%→{silt}%, organic {predicted_organic:g}%→{organic}%"
    audit.write(s, u.name, u.role, "Inspector score/debris correction", drain_code or "Photo inspection", (pred + (pd_ or f"plastic {plastic}%, silt {silt}%, organic {organic}%")).strip())
    try: s.commit()
    except IntegrityError:
        s.rollback(); ex = s.query(TrainingExample).filter_by(client_id=client_id).first(); return {"id": ex.id, "status": "queued_for_training", "duplicate": True}
    return {"id": t.id, "status": "queued_for_training", "duplicate": False}
