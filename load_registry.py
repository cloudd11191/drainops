"""Load data/registry.xlsx into the Digital Twin database.
Called automatically on first start when the database is empty; safe to run by hand too
(existing drains are updated, which RESETS their health to the registry value)."""
import sys, json
from app.registry import load_registry
from app.db import SessionLocal, Drain

def run(path="data/registry.xlsx"):
    df = load_registry(path)
    s = SessionLocal(); added = updated = 0
    try:
        for r in df.itertuples():
            d = s.query(Drain).filter_by(code=r.drain_id).first()
            if not d: d = Drain(code=r.drain_id); s.add(d); added += 1
            else: updated += 1
            d.name, d.state, d.municipality, d.ward_no = r.drain_name, r.state, r.municipality, int(r.ward_no)
            d.ward = f"{r.municipality} Ward {int(r.ward_no)}"
            d.lat, d.lon = float(r.latitude), float(r.longitude)
            d.geometry_geojson = json.dumps({"type": "Point", "coordinates": [d.lon, d.lat]})
            d.width_m, d.depth_m, d.material = float(r.width_m), float(r.depth_m), r.material
            d.health = float(r.current_health_score); d.blockage_condition = r.blockage_condition
            d.last_inspected = r.last_inspected.to_pydatetime()
            d.rain_snapshot_mm, d.registry_risk, d.registry_priority = float(r.rain_snapshot_mm), float(r.computed_risk_score), r.priority_status
        s.commit()
    finally:
        s.close()
    print(f"registry loaded: {added} added, {updated} updated, {len(df)} total")

if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "data/registry.xlsx")
