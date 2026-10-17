import datetime as dt, re
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func
from .. import audit
from ..db import Contractor, Drain, FieldReport, Inspection, WorkOrder
from ..deps import db, require
from ..reports import contractor_stats, round_half_up, ward_csv, ward_report
from ..services import DEFAULT_RAIN, drain_risk

router = APIRouter(tags=["reports"])
READERS = ("supervisor", "engineer", "admin")

def _month(m):
    if m and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", m): return m
    return dt.datetime.utcnow().strftime("%Y-%m")

def _bounds(m):
    y, mo = int(m[:4]), int(m[5:]); a = dt.datetime(y, mo, 1)
    return a, dt.datetime(y + (mo == 12), mo % 12 + 1, 1)

def _rows(s, month, ward, rain, model):
    a, b = _bounds(month)
    ver = dict(s.query(Drain.code, func.count(FieldReport.id)).join(FieldReport, FieldReport.drain_id == Drain.id)
               .filter(FieldReport.status == "verified", FieldReport.verified_at >= a, FieldReport.verified_at < b).group_by(Drain.code).all())
    ds = [{"code": d.code, "name": d.name, "ward": d.municipality or d.ward, "health": d.health, "obj": d} for d in s.query(Drain).order_by(Drain.id)]
    return ward_report(ds, lambda x: round_half_up(drain_risk(x["obj"], rain, model)), ver, ward)

@router.get("/reports/ward")
def ward_json(month: str = None, ward: str = "All", rain_mm: float = Query(DEFAULT_RAIN, ge=0, le=500), model: str = Query("calibrated", pattern="^(calibrated|legacy)$"), s=Depends(db), u=Depends(require(*READERS))):
    m = _month(month); rows = _rows(s, m, ward, rain_mm, model)
    return {"month": m, "ward": ward, "rain_mm": rain_mm, "model": model, "rows": rows,
            "note": "Health is each drain's current value, not its value at month end."}

@router.get("/reports/ward.csv")
def ward_csv_export(month: str = None, ward: str = "All", rain_mm: float = Query(DEFAULT_RAIN, ge=0, le=500), model: str = Query("calibrated", pattern="^(calibrated|legacy)$"), s=Depends(db), u=Depends(require(*READERS))):
    m = _month(month); body = ward_csv(_rows(s, m, ward, rain_mm, model), m)
    audit.write(s, u.name, u.role, "Report exported", f"ward report {m}", f"ward={ward}, model={model}"); s.commit()
    return Response(body.encode("utf-8"), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="drainops-ward-report-{m}.csv"'})

@router.get("/reports/contractors")
def contractors(month: str = None, s=Depends(db), u=Depends(require("supervisor", "admin"))):
    q = s.query(WorkOrder, Contractor).outerjoin(Contractor, Contractor.id == WorkOrder.contractor_id).filter(WorkOrder.verified_at.isnot(None))
    if month:
        a, b = _bounds(_month(month)); q = q.filter(WorkOrder.verified_at >= a, WorkOrder.verified_at < b)
    jobs = [{"contractor": c.name if c else None, "assigned_at": w.created, "completed_at": w.completed, "drain_id": w.drain_id, "verified_at": w.verified_at} for w, c in q]

    def later_critical(drain_id, a, b):   # only trained-model scores count; otherwise "unknown", never a false 0%
        rows = s.query(Inspection.health).filter(Inspection.drain_id == drain_id, Inspection.created > a, Inspection.created <= b, Inspection.model_mode == "onnx").all()
        return None if not rows else any(h < 33 for (h,) in rows)
    return {"month": month, **contractor_stats(jobs, later_critical, dt.datetime.utcnow()),
            "definitions": {"median_response_hours": "assigned to completed, verified jobs only",
                            "repeat_blockage_rate": "share of cleanings older than 30 days whose drain scored Critical again within 30 days (trained-model scores only); null = not enough data"}}
