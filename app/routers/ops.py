import datetime as dt
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from .. import audit
from ..db import Contractor, Drain, Inspection, WorkOrder
from ..deps import db, require
from ..services import ranked

router = APIRouter(tags=["operations"])
SUP = ("supervisor", "admin")

class GenIn(BaseModel):
    top: int = Field(20, ge=1, le=100); crews: int = Field(3, ge=1, le=20); rain_mm: float | None = Field(None, ge=0, le=500); contractor_id: int | None = None

@router.post("/workorders/generate")
async def generate(body: GenIn, s=Depends(db), u=Depends(require(*SUP))):
    """Prioritised work orders for drains at Warning/Critical risk that have none open. Nearby drains share a crew (north-to-south sweep, not route optimisation)."""
    r = await ranked(s, body.rain_mm)
    open_ids = {w.drain_id for w in s.query(WorkOrder).filter(WorkOrder.status.in_(("assigned", "in_progress", "completed")))}
    pick = sorted([x for x in r[:body.top] if x["severity"] != "Healthy" and x["drain_id"] not in open_ids], key=lambda x: (-x["lat"], x["lon"]))
    size = max(1, -(-len(pick) // body.crews)); batch = int(dt.datetime.utcnow().timestamp()); out = []
    for n, x in enumerate(pick):
        crew = f"Crew {n // size + 1}"
        w = WorkOrder(batch=batch, drain_id=x["drain_id"], risk=x["risk"], crew=crew, contractor_id=body.contractor_id); s.add(w); s.flush()
        out.append({"id": w.id, **x, "crew": crew})
    audit.write(s, u.name, u.role, "Work orders generated", f"batch {batch}", f"{len(out)} orders, rain {'forecast' if body.rain_mm is None else str(body.rain_mm) + ' mm what-if'}"); s.commit()
    return {"batch": batch, "orders": out, "skipped_open": len([x for x in r[:body.top] if x["drain_id"] in open_ids]),
            "warning": None if all(x["forecast_available"] for x in r) or body.rain_mm is not None else "Rain forecast unavailable for some drains; their risk assumes 0 mm"}

@router.get("/workorders")
def list_orders(status: str = None, s=Depends(db), u=Depends(require("supervisor", "engineer", "admin"))):
    q = s.query(WorkOrder, Drain, Contractor).join(Drain, Drain.id == WorkOrder.drain_id).outerjoin(Contractor, Contractor.id == WorkOrder.contractor_id)
    if status: q = q.filter(WorkOrder.status == status)
    return [{"id": w.id, "batch": w.batch, "drain": d.code, "name": d.name, "risk": w.risk, "status": w.status, "crew": w.crew, "contractor": c.name if c else None,
             "assigned": w.created.isoformat(), "completed": w.completed.isoformat() if w.completed else None,
             "response_hours": round((w.completed - w.created).total_seconds() / 3600, 1) if w.completed else None} for w, d, c in q.order_by(WorkOrder.id.desc()).limit(500)]

class PatchIn(BaseModel):
    status: str | None = Field(None, pattern="^(in_progress|completed)$"); crew: str | None = Field(None, max_length=40); contractor_id: int | None = None

@router.patch("/workorders/{wid}")
def patch_order(wid: int, body: PatchIn, s=Depends(db), u=Depends(require(*SUP))):
    """'verified' is never set here: it only follows a verified before/after cleaning report."""
    w = s.get(WorkOrder, wid)
    if not w: raise HTTPException(404)
    if w.status == "verified": raise HTTPException(409, "Verified work orders are closed")
    if body.status == "in_progress" and w.status != "assigned": raise HTTPException(409, "Only assigned orders can start")
    if body.contractor_id is not None and not s.get(Contractor, body.contractor_id): raise HTTPException(404, "Unknown contractor")
    ch = []
    if body.status: w.status = body.status; ch.append(f"status={body.status}")
    if body.status == "in_progress": w.started = dt.datetime.utcnow()
    if body.status == "completed": w.completed = dt.datetime.utcnow()
    if body.crew: w.crew = body.crew; ch.append(f"crew={body.crew}")
    if body.contractor_id is not None: w.contractor_id = body.contractor_id; ch.append(f"contractor={body.contractor_id}")
    audit.write(s, u.name, u.role, "Work order updated", f"WO {w.id}", ", ".join(ch)); s.commit()
    return {"id": w.id, "status": w.status, "crew": w.crew, "contractor_id": w.contractor_id}

class ContractorIn(BaseModel): name: str = Field(min_length=2, max_length=80); zone: str | None = Field(None, max_length=60)

@router.get("/contractors")
def contractors(s=Depends(db), u=Depends(require(*SUP))): return [{"id": c.id, "name": c.name, "zone": c.zone} for c in s.query(Contractor).order_by(Contractor.name)]

@router.post("/contractors", status_code=201)
def add_contractor(body: ContractorIn, s=Depends(db), u=Depends(require(*SUP))):
    if s.query(Contractor).filter_by(name=body.name).first(): raise HTTPException(409, "Contractor already exists")
    c = Contractor(name=body.name, zone=body.zone); s.add(c); audit.write(s, u.name, u.role, "Contractor added", body.name, body.zone or ""); s.commit()
    return {"id": c.id, "name": c.name, "zone": c.zone}
