"""Ward and contractor reports (pure functions). Output layout matches the frontend's CSV export exactly."""
import csv, io, math, statistics, datetime as dt

HEADER = ["Reporting month", "Row type", "Ward / municipality", "Drain ID", "Drain name", "Drains",
          "Mean health / 100", "High risk drains", "Health / 100", "Risk / 100", "Verified cleanings"]
HIGH_RISK_AT = 65

def round_half_up(x: float) -> int: return int(math.floor(x + 0.5))   # JS Math.round semantics

def ward_report(drains: list, risk_of, verified_by_drain: dict, ward: str = "All") -> list:
    """drains: dicts with code,name,ward,health. risk_of(d)->score. Wards keep first-appearance order."""
    groups = {}
    for d in drains:
        if ward != "All" and d["ward"] != ward: continue
        groups.setdefault(d["ward"], []).append({
            "drain_id": d["code"], "drain_name": d["name"], "health": round_half_up(d["health"]),
            "risk": risk_of(d), "verified_cleanings": verified_by_drain.get(d["code"], 0)})
    out = []
    for w, rec in groups.items():
        out.append({"ward": w, "drains": len(rec), "mean_health": round_half_up(sum(r["health"] for r in rec) / len(rec)),
                    "high_risk": sum(r["risk"] >= HIGH_RISK_AT for r in rec),
                    "verified_cleanings": sum(r["verified_cleanings"] for r in rec), "records": rec})
    return out

def ward_csv(rows: list, month: str) -> str:
    """UTF-8 BOM + CRLF + every field quoted, as the browser export produces (opens cleanly in Excel)."""
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    w.writerow(HEADER)
    for r in rows:
        w.writerow([month, "Ward summary", r["ward"], "", "", r["drains"], r["mean_health"], r["high_risk"], "", "", r["verified_cleanings"]])
        for d in r["records"]:
            w.writerow([month, "Drain detail", r["ward"], d["drain_id"], d["drain_name"], "", "", "", d["health"], d["risk"], d["verified_cleanings"]])
    return "\ufeff" + buf.getvalue().rstrip("\r\n")   # the browser export has no trailing line break

def contractor_stats(jobs: list, later_critical, now: dt.datetime, window_days: int = 30) -> dict:
    """jobs: dicts {contractor, assigned_at, completed_at, drain_id, verified_at}. Only verified jobs count.
    later_critical(drain_id, after, until) -> True/False, or None when there is no trustworthy re-inspection in that window.
    Repeat-blockage rate only uses cleanings old enough for the full window to have elapsed (else None)."""
    by = {}
    for j in jobs:
        if not j.get("verified_at"): continue
        by.setdefault(j["contractor"] or "Unassigned", []).append(j)
    def stats(js):
        hrs = [(j["completed_at"] - j["assigned_at"]).total_seconds() / 3600 for j in js if j.get("completed_at") and j.get("assigned_at")]
        old = [j for j in js if j.get("completed_at") and (now - j["completed_at"]).days >= window_days]
        res = [later_critical(j["drain_id"], j["completed_at"], j["completed_at"] + dt.timedelta(days=window_days)) for j in old]
        res = [r for r in res if r is not None]          # None = no trustworthy re-inspection data in the window
        return {"verified_cleanings": len(js), "median_response_hours": round(statistics.median(hrs), 1) if hrs else None,
                "repeat_blockage_rate": round(sum(res) / len(res), 3) if res else None, "repeat_eligible": len(res)}
    rows = [{"contractor": k, **stats(v)} for k, v in sorted(by.items())]
    allj = [j for v in by.values() for j in v]
    return {"contractors": rows, "totals": {"contractor_count": len(rows), **stats(allj)}}
