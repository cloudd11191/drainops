import datetime as dt, secrets
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func
from .. import audit, config
from ..db import CitizenReport, Drain, FieldReport
from ..deps import db, require
from ..insights import ISSUE_BASE, URGENCY_BOOST, citizen_score
from ..security import RateLimiter
from ..services import ranked, read_limited, score_photo, store_image
from ..verification import haversine_m

router = APIRouter(tags=["public"])
citizen_limiter = RateLimiter(5, 3600)
MATCH_M = 300

@router.get("/public/scorecard")
def scorecard(s=Depends(db)):
    """Per municipality: mean health, verified cleanings in the last 30 days, and a simple risk label."""
    since = dt.datetime.utcnow() - dt.timedelta(days=30)
    clean = dict(s.query(Drain.municipality, func.count(FieldReport.id)).join(FieldReport, FieldReport.drain_id == Drain.id)
                 .filter(FieldReport.status == "verified", FieldReport.verified_at >= since).group_by(Drain.municipality).all())
    g = {}
    for d in s.query(Drain): g.setdefault(d.municipality or d.ward, []).append(d.health)
    out = []
    for w, hs in g.items():
        h = round(sum(hs) / len(hs))
        out.append({"ward": w, "health": h, "frequency": f"{clean.get(w, 0)} / month", "risk": "Low" if h >= 70 else "Moderate" if h >= 50 else "High", "drains": len(hs)})
    return sorted(out, key=lambda x: x["health"])

@router.get("/public/alerts")
async def alerts(s=Depends(db)):
    """Forecast alerts from the live rainfall feed and recently dispatched resident reports. No contact details, no promise of SMS delivery."""
    r = await ranked(s, None); by = {}
    for x in r: by.setdefault(x["ward"], []).append(x)
    out = []
    for w, xs in by.items():
        mm = max(x["rain_mm"] or 0 for x in xs)
        if mm >= config.RAIN_ALERT_MM:
            n = sum(x["severity"] == "Critical" for x in xs)
            out.append({"tag": "Forecast", "ward": w, "title": f"{w}: heavy rain forecast", "detail": f"About {mm:.0f} mm expected in 48 h. {n} drain(s) here are at high overflow risk."})
    for c in s.query(CitizenReport).filter(CitizenReport.status == "dispatched", CitizenReport.created >= dt.datetime.utcnow() - dt.timedelta(days=7)).order_by(CitizenReport.id.desc()).limit(5):
        out.append({"tag": "Citizen", "ward": c.ward, "title": "Resident report sent to a crew", "detail": f"{c.issue} reported in {c.ward}."})
    return {"alerts": out[:10], "forecast_available": any(x["forecast_available"] for x in r)}

@router.post("/citizen-reports", status_code=201)
async def citizen(request: Request, ward: str = Form(..., max_length=60), location: str = Form(..., max_length=200), issue: str = Form(..., max_length=40),
                  urgency: str = Form("Medium", max_length=10), name: str = Form(None, max_length=60), lat: float = Form(None, ge=-90, le=90),
                  lon: float = Form(None, ge=-180, le=180), photo: UploadFile = File(None), s=Depends(db)):
    if not citizen_limiter.allow(request.client.host if request.client else "?"): raise HTTPException(429, "Too many reports from this connection. Please try again later.")
    if issue not in ISSUE_BASE: raise HTTPException(422, f"issue must be one of {list(ISSUE_BASE)}")
    if urgency not in URGENCY_BOOST: raise HTTPException(422, f"urgency must be one of {list(URGENCY_BOOST)}")
    ph, mode, path = None, None, None
    if photo is not None and photo.filename:
        data = await read_limited(photo)
        try: res = score_photo(data)
        except Exception: raise HTTPException(400, "Not a readable image")
        path = store_image(data)                                    # strips EXIF/GPS before storage
        mode = res["model_mode"]; ph = res["health"] if mode == "onnx" else None   # demo-heuristic photo scores are not used for triage
    match = None
    if lat is not None and lon is not None:
        best = min(((haversine_m(lat, lon, d.lat, d.lon), d) for d in s.query(Drain)), key=lambda t: t[0], default=None)
        if best and best[0] <= MATCH_M: match = best[1]
    c = CitizenReport(ref="CIT-" + secrets.token_hex(3).upper(), name=(name or "").strip() or None, ward=ward, location=location, issue=issue, urgency=urgency, lat=lat, lon=lon,
                      photo_path=path, photo_health=ph, model_mode=mode, auto_score=citizen_score(issue, urgency, ph), matched_drain_id=match.id if match else None)
    s.add(c); s.commit()
    return {"ref": c.ref, "auto_score": c.auto_score, "status": c.status, "matched_drain": match.code if match else None, "scored_from_photo": ph is not None}

@router.get("/citizen-reports")
def list_citizen(status: str = None, s=Depends(db), u=Depends(require("supervisor", "admin"))):
    q = s.query(CitizenReport)
    if status: q = q.filter_by(status=status)
    return [{"ref": c.ref, "name": c.name, "ward": c.ward, "location": c.location, "issue": c.issue, "urgency": c.urgency, "auto_score": c.auto_score, "status": c.status,
             "photo": c.photo_path, "matched_drain_id": c.matched_drain_id, "created": c.created.isoformat()} for c in q.order_by(CitizenReport.auto_score.desc(), CitizenReport.id.desc()).limit(300)]

class CitizenPatch(BaseModel): status: str = Field(pattern="^(reviewed|dispatched|rejected)$")

@router.patch("/citizen-reports/{ref}")
def patch_citizen(ref: str, body: CitizenPatch, s=Depends(db), u=Depends(require("supervisor", "admin"))):
    c = s.query(CitizenReport).filter_by(ref=ref).first()
    if not c: raise HTTPException(404)
    old, c.status, c.reviewed_by = c.status, body.status, u.name
    audit.write(s, u.name, u.role, "Citizen report triaged", ref, f"{old}→{body.status}"); s.commit()
    return {"ref": ref, "status": c.status}
