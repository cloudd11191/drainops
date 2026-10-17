"""Parse the Drain Digital Twin registry spreadsheet (pandas only, no DB needed)."""
import re
import pandas as pd

from .insights import PLASTIC, SILT, ORGANIC

def tier_from_health(h: float) -> str:
    return "Clear" if h >= 66 else "Moderate" if h >= 33 else "Critical"

def load_registry(path: str = "data/registry.xlsx") -> pd.DataFrame:
    df = pd.read_excel(path).rename(columns={"24h_rainfall_forecast_mm": "rain_snapshot_mm"})
    need = {"drain_id", "latitude", "longitude", "width_m", "depth_m", "current_health_score"}
    missing = need - set(df.columns)
    if missing: raise ValueError(f"registry is missing columns: {missing}")
    if df.drain_id.duplicated().any(): raise ValueError("duplicate drain_id values")
    t = df.blockage_condition.fillna("").str.lower()
    df["plastic"] = t.str.contains(PLASTIC).astype(int)
    df["silt"] = t.str.contains(SILT).astype(int)
    df["organic"] = t.str.contains(ORGANIC).astype(int)
    df["tier"] = df.current_health_score.map(tier_from_health)
    df["area_m2"] = df.width_m * df.depth_m
    return df
