"""Normalized overflow-risk model, calibrated against data/registry.xlsx (101 drains).

score = 100 * sum(weight_i * factor_i), each factor scaled 0-1, clipped at 100.
Calibration (training/validate_risk.py): matches the registry's Normal/Warning/Critical
bands on ~98% of drains. Limits of that calibration:
  * registry forecasts only span 8-25 mm/24h, so behaviour in heavy-rain events is extrapolated;
  * the registry's own score ignores drain size (fitted weight ~0), so the capacity weight is a
    small physically-motivated prior, not something the data confirmed;
  * the registry has no last-cleaned dates, so the neglect term is skipped when unknown.
"""
from dataclasses import dataclass
from typing import Optional

WEIGHTS = {"blockage": 0.80, "rain": 0.32, "capacity": 0.05, "neglect": 0.10, "flood": 0.10}
RAIN_CAP_MM = 50.0        # 24-48h forecast at/above this counts as full rain pressure
NEGLECT_CAP_DAYS = 90.0
CAPACITY_REF_M2 = 10.0    # cross-section at/above which capacity risk ~ 0 (registry median ~4.7 m2)
CRITICAL_AT, WARNING_AT = 65.0, 35.0   # bands observed in the registry

@dataclass
class RiskResult:
    score: float
    level: str
    breakdown: dict

def compute_risk(health: float, rain_mm: float, days_since_clean: Optional[float],
                 cross_section_m2: float, flood_count: Optional[int] = None) -> RiskResult:
    parts = {
        "blockage": (100 - max(0.0, min(100.0, health))) / 100,
        "rain": min(max(rain_mm, 0) / RAIN_CAP_MM, 1),
        "capacity": 1 - min(max(cross_section_m2, 0) / CAPACITY_REF_M2, 1),
    }
    if days_since_clean is not None:                    # skipped entirely when unknown
        parts["neglect"] = min(max(days_since_clean, 0) / NEGLECT_CAP_DAYS, 1)
    if flood_count is not None:                         # UNCALIBRATED prior: the registry has no flood history
        parts["flood"] = min(flood_count / 3, 1)
    score = min(100.0, 100 * sum(WEIGHTS[k] * v for k, v in parts.items()))
    level = "CRITICAL" if score >= CRITICAL_AT else "WARNING" if score >= WARNING_AT else "NORMAL"
    return RiskResult(round(score, 1), level, {k: round(v, 3) for k, v in parts.items()})

def legacy_dashboard_risk(health: float, width_m: float, depth_m: float, rain_mm: float) -> int:
    """The formula currently hard-coded in the frontend (index.html). UNCALIBRATED and it scores LARGER drains as riskier.
    Kept only so existing exports can be reproduced; prefer compute_risk."""
    import math
    area = width_m * depth_m
    v = (100 - health) * 0.68 + (rain_mm / 14) * 18 + (min(22, area * 2.6) if area > 0 else 0)
    return int(max(5, min(99, math.floor(v + 0.5))))
