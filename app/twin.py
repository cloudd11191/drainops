import datetime as dt, json
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from .. import audit
from ..db import Drain, HealthSnapshot, Inspection
from ..deps import db, require
from ..services import DEFAULT_RAIN, drain_props, latest_inspections, ranked

router = APIRouter(tags=["twin"])

@router.get("/drains")
def drains(ward: str = None, state: str = None, municipality: str = None, rain_mm: float = Query(DEFAULT_RAIN, ge=0, le=500),
           model: str = Query("calibrated", pattern="^(calibrated|legacy)$"), s=Depends(db)):
    """Public GeoJSON for the map. `risk`/`severity` use the server model at `rain_mm`; model=legacy reproduces the old frontend formula."""
    q = s.query(Drain)
    if ward: q = q.filter((Drain.municipality == ward) | (Drain.ward == ward))
    if state: q = q.filter_by(state=state)
    if municipality: q = q.filter_by(municipality=municipality)
    ds = q.all(); latest = latest_inspections(s); code = {d.id: d.code for d in s.query(Drain.id, Drain.code)}
    feats = []
    for d in ds:
        p = drain_props(d, rain_mm, latest, model); p["downstream"] = code.get(d.downstream_id)
        geom = json.loads(d.geometry_geojson) if d.geometry_geojson else {"type": "Point", "coordinates": [d.lon, d.lat]}
        feats.append({"type": "Feature", "geometry": geom, "properties": p})
    return {"type": "FeatureCollection", "features": feats, "rain_mm": rain_mm, "model": model}

@router.get("/drains/{code}/history")
def history(code: str, s=Depends(db), u=Depends(require("inspector", "supervisor", "engineer", "admin"))):
    d = s.query(Drain).filter_by(code=code).first()
    if not d: raise HTTPException(404, "Unknown drain")
    q = s.query(Inspection).filter_by(drain_id=d.id).order_by(Inspection.created)
    return [{"id": i.id, "kind": i.kind, "tier": i.corrected_tier or i.tier, "health": i.health, "model_mode": i.model_mode, "at": i.created.isoformat()} for i in q]

@router.get("/twin/timeline")
def timeline(weeks: int = Query(10, ge=1, le=52), s=Depends(db), u=Depends(require("supervisor", "engineer", "admin"))):
    """Weekly health per drain for the time slider. A week is null until the drain has any recorded history; later weeks carry the last value forward."""
    end = dt.datetime.utcnow(); start = end - dt.timedelta(weeks=weeks)
    ends = [start + dt.timedelta(weeks=i + 1) for i in range(weeks)]
    code = {d.id: d.code for d in s.query(Drain.id, Drain.code)}
    per = {}
    for sn in s.query(HealthSnapshot).order_by(HealthSnapshot.taken):
        per.setdefault(sn.drain_id, []).append((sn.taken, sn.health))
    series = {}
    for did, c in code.items():
        pts, k, last, row = per.get(did, []), 0, None, []
        for e in ends:
            while k < len(pts) and pts[k][0] <= e: last = pts[k][1]; k += 1
            row.append(last)
        series[c] = row
    return {"weeks": [{"label": f"Week {i + 1}", "ends": e.date().isoformat()} for i, e in enumerate(ends)], "series": series,
            "note": "null = no recorded health yet. History builds up as inspections are synced."}

@router.get("/twin/network")
def network(s=Depends(db), u=Depends(require("supervisor", "engineer", "admin"))):
    ds = s.query(Drain).all(); by = {d.id: d for d in ds}
    edges = [{"from": d.code, "to": by[d.downstream_id].code} for d in ds if d.downstream_id in by]
    return {"edges": edges, "connected_drains": len(edges), "note": "" if edges else "No connectivity recorded yet. Engineers set it with PUT /drains/{code}/downstream."}

class DownstreamIn(BaseModel): downstream_code: str | None = None

@router.put("/drains/{code}/downstream")
def set_downstream(code: str, body: DownstreamIn, s=Depends(db), u=Depends(require("engineer", "admin"))):
    d = s.query(Drain).filter_by(code=code).first()
    if not d: raise HTTPException(404, "Unknown drain")
    t = s.query(Drain).filter_by(code=body.downstream_code).first() if body.downstream_code else None
    if body.downstream_code and not t: raise HTTPException(404, "Unknown downstream drain")
    seen, cur = set(), t
    while cur is not None:                                   # refuse cycles
        if cur.id == d.id or cur.id in seen: raise HTTPException(409, "That link would create a loop")
        seen.add(cur.id); cur = s.get(Drain, cur.downstream_id) if cur.downstream_id else None
    d.downstream_id = t.id if t else None
    audit.write(s, u.name, u.role, "Drain link changed", code, f"flows into {body.downstream_code or 'nothing'}"); s.commit()
    return {"code": code, "downstream": body.downstream_code}

@router.get("/drains/{code}/impact")
def impact(code: str, s=Depends(db), u=Depends(require("supervisor", "engineer", "admin"))):
    """Segments upstream of this drain that would back up if it were blocked."""
    d = s.query(Drain).filter_by(code=code).first()
    if not d: raise HTTPException(404, "Unknown drain")
    ds = s.query(Drain).all(); kids = {}
    for x in ds:
        if x.downstream_id: kids.setdefault(x.downstream_id, []).append(x)
    out, stack = [], [d.id]
    while stack:
        for k in kids.get(stack.pop(), []): out.append(k.code); stack.append(k.id)
    return {"code": code, "upstream_affected": out, "count": len(out)}

@router.get("/risk/ranking")
async def ranking(top: int = Query(20, ge=1, le=200), rain_mm: float = Query(None, ge=0, le=500), state: str = None, s=Depends(db),
                  u=Depends(require("supervisor", "engineer", "admin"))):
    """Live-forecast ranking, or a what-if with rain_mm."""
    r = await ranked(s, rain_mm, state)
    return {"forecast_available": all(x["forecast_available"] for x in r), "drains": r[:top]}

@router.get("/alerts/rain")
async def rain_alerts(s=Depends(db), u=Depends(require("supervisor", "engineer", "admin"))):
    from .. import config
    r = await ranked(s, None)
    mx = max([x["rain_mm"] or 0 for x in r] or [0])
    return {"threshold_mm": config.RAIN_ALERT_MM, "max_forecast_mm": mx, "triggered": mx >= config.RAIN_ALERT_MM,
            "forecast_available": any(x["forecast_available"] for x in r), "top_drains": r[:20]}
