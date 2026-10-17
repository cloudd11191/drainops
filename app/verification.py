"""Anti-ghost-cleaning checks for a before/after pair. Pure functions, no DB."""
import math
from dataclasses import dataclass
from typing import Optional

def haversine_m(la1, lo1, la2, lo2) -> float:
    p1, p2 = math.radians(la1), math.radians(la2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))

@dataclass
class Evidence:
    has_before: bool; has_after: bool
    drain_lat: float; drain_lon: float
    lat: Optional[float]; lon: Optional[float]
    before_ts: Optional[float]; after_ts: Optional[float]      # device clock, epoch seconds
    received_ts: float                                          # server clock
    before_health: Optional[float]; after_health: Optional[float]
    model_mode: str

def evaluate_cleaning(ev: Evidence, min_gain=10.0, max_dist_m=250.0, max_gap_h=72.0, auto_verify=True) -> dict:
    c, why = {}, []
    c["photo_pair"] = ev.has_before and ev.has_after
    c["gps_present"] = ev.lat is not None and ev.lon is not None
    d = haversine_m(ev.drain_lat, ev.drain_lon, ev.lat, ev.lon) if c["gps_present"] else None
    c["near_drain"] = d is not None and d <= max_dist_m
    ordered = ev.before_ts is not None and ev.after_ts is not None and ev.after_ts > ev.before_ts
    c["order_ok"] = ordered
    c["gap_ok"] = ordered and (ev.after_ts - ev.before_ts) / 3600 <= max_gap_h
    c["clock_sane"] = ev.after_ts is not None and abs(ev.received_ts - ev.after_ts) <= 7 * 86400
    gain = None if ev.before_health is None or ev.after_health is None else round(ev.after_health - ev.before_health, 1)
    c["gain_ok"] = gain is not None and gain >= min_gain
    msgs = {"photo_pair": "needs both before and after photos", "gps_present": "no GPS fix", "near_drain": f"photo location is not within {max_dist_m:.0f} m of the drain",
            "order_ok": "after photo is not later than before photo", "gap_ok": f"before/after more than {max_gap_h:.0f} h apart",
            "clock_sane": "device clock differs from server by over 7 days", "gain_ok": f"health gain below {min_gain:.0f} points"}
    why = [msgs[k] for k, v in c.items() if not v]
    passed = all(c.values())
    trained = ev.model_mode == "onnx"
    if not c["photo_pair"]: status = "incomplete"
    elif passed and trained and auto_verify: status = "verified"
    else:
        status = "pending_review"          # demo-heuristic scores can never self-verify a cleaning
        if passed and not trained: why.append("scores come from the demo heuristic, so a supervisor must confirm")
    return {"checks": c, "gain": gain, "distance_m": None if d is None else round(d), "passed": passed, "status": status, "reasons": why}
