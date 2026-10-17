import os, re
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func
from .. import audit as au, config
from ..db import AuditLog, Drain, TrainingExample
from ..deps import db, require
from ..inference import _session

router = APIRouter(tags=["admin"])

@router.get("/health")
def health(): return {"status": "ok", "model_mode": "onnx" if _session else "demo_heuristic", "auth_mode": config.AUTH_MODE}

@router.get("/stats")
def stats(s=Depends(db)):
    by = {}
    for d in s.query(Drain):
        b = by.setdefault(d.state, {"drains": 0, "green": 0, "amber": 0, "red": 0}); b["drains"] += 1
        b["green" if d.health >= 66 else "amber" if d.health >= 33 else "red"] += 1
    n = sum(v["drains"] for v in by.values())
    return {"total": n, "by_state": by, "avg_health": round(s.query(func.avg(Drain.health)).scalar() or 0, 1)}

@router.get("/audit")
def audit_list(limit: int = Query(100, ge=1, le=500), s=Depends(db), u=Depends(require("admin"))):
    """Newest first, in the shape the Governance panel renders."""
    return [{"action": a.action, "actor": a.actor, "role": a.role, "target": a.target, "change": a.change, "timestamp": a.timestamp}
            for a in s.query(AuditLog).filter(AuditLog.hash.isnot(None)).order_by(AuditLog.id.desc()).limit(limit)]

@router.get("/audit/verify")
def audit_verify(s=Depends(db), u=Depends(require("admin"))):
    rows = [{"actor": a.actor, "role": a.role, "action": a.action, "target": a.target, "change": a.change, "timestamp": a.timestamp, "prev_hash": a.prev_hash, "hash": a.hash}
            for a in s.query(AuditLog).filter(AuditLog.hash.isnot(None)).order_by(AuditLog.id)]
    return au.verify_chain(rows)

@router.get("/training/examples")
def training(s=Depends(db), u=Depends(require("admin"))):
    q = s.query(TrainingExample)
    return {"total": q.count(), "pending_export": q.filter_by(exported=False).count(),
            "recent": [{"id": t.id, "corrected_health": t.corrected_health, "plastic": t.plastic, "silt": t.silt, "organic": t.organic, "actor": t.actor, "created": t.created.isoformat()}
                       for t in q.order_by(TrainingExample.id.desc()).limit(50)]}

@router.get("/media/{name}")
def media(name: str, u=Depends(require("supervisor", "admin"))):
    if not re.fullmatch(r"[0-9a-f]{32}\.(jpg|webm|ogg|m4a|wav|mp4|bin)", name): raise HTTPException(404)
    p = os.path.join(config.UPLOAD_DIR, name)
    if not os.path.isfile(p): raise HTTPException(404)
    h = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    return FileResponse(p, media_type="image/jpeg", headers=h) if name.endswith(".jpg") else FileResponse(p, headers=h, filename=name)
