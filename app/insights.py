"""Derived per-drain indicators (pure). Mosquito and plastic figures are ESTIMATES from proxies, not measurements."""
import re
PLASTIC = r"plastic|polythene|polybag|thermocol|packaging|wrapp|pouch|cups|crates|beverage|spools|bags"
SILT = r"silt|sediment|sand|mud|sludge|slurry|dust|gravel|slag|coal|rubble"
ORGANIC = r"organic|vegetable|veg\b|bio|floral|flower|offering|food|garbage|rotten|foliage|leaf|weed|hyacinth|bamboo|vegetation"

def flags(text: str) -> dict:
    t = (text or "").lower()
    return {"plastic": bool(re.search(PLASTIC, t)), "silt": bool(re.search(SILT, t)), "organic": bool(re.search(ORGANIC, t))}

def debris_type(text: str) -> str:
    f = flags(text); n = sum(f.values())
    if n == 1: return "Plastic" if f["plastic"] else "Silt" if f["silt"] else "Organic"
    return "Mixed"

def debris_shares(text: str, mix: dict = None) -> dict:
    """Prefer the latest model/inspector debris mix (fractions); fall back to registry text keywords."""
    if mix and sum(v or 0 for v in mix.values()) > 0:
        t = sum(mix.values()); return {k: round(100 * (v or 0) / t) for k, v in mix.items()}
    f = flags(text); n = sum(f.values())
    return {k: (round(100 / n) if v else 0) for k, v in f.items()} if n else {"plastic": 0, "silt": 0, "organic": 0}

def severity(score: float) -> str:
    return "Critical" if score >= 65 else "Warning" if score >= 35 else "Healthy"

def mosquito_risk(health: float, organic_share: float, stagnant: bool = False) -> int:
    """Proxy for standing water + organic load: 55% blockage, 35% organic share, +10 if an inspector noted stagnant flow."""
    return round(min(100, 0.55 * (100 - health) + 0.35 * organic_share + (10 if stagnant else 0)))

ISSUE_BASE = {"Blocked inlet": 60, "Overflowing culvert": 75, "Standing water": 55, "Bad smell": 45}
URGENCY_BOOST = {"High": 18, "Medium": 10, "Low": 4}

def citizen_score(issue: str, urgency: str, photo_health: float = None) -> int:
    """Priority 0-99 for a resident report. With a photo: 70% blockage from the scored image + 30% issue type, plus urgency.
    Without one it relies on the issue type alone. A heuristic for triage, not a measurement."""
    base = ISSUE_BASE.get(issue, 50)
    core = base if photo_health is None else 0.7 * (100 - photo_health) + 0.3 * base
    return int(min(99, round(core * 0.8 + URGENCY_BOOST.get(urgency, 10) * 1.0 + 0.0)))
