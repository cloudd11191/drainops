"""48h rainfall from Open-Meteo, cached per 0.1 degree cell (~11 km) so 100s of drains cost a handful of requests.
Returns None when the forecast is unavailable: callers must NOT treat that as 'no rain'."""
import asyncio, time, httpx
_cache: dict = {}; TTL = 1800

def cell(lat, lon): return (round(lat, 1), round(lon, 1))

async def _one(c, hours=48):
    if c in _cache and time.time() - _cache[c][0] < TTL: return _cache[c][1]
    try:
        async with httpx.AsyncClient(timeout=8) as cl:
            r = await cl.get("https://api.open-meteo.com/v1/forecast", params={"latitude": c[0], "longitude": c[1], "hourly": "precipitation", "forecast_days": 3, "timezone": "Asia/Kolkata"})
            r.raise_for_status()
            mm = float(sum(r.json()["hourly"]["precipitation"][:hours]))
    except Exception:
        return None
    _cache[c] = (time.time(), mm); return mm

async def forecasts(coords) -> dict:
    cells = sorted({cell(la, lo) for la, lo in coords})
    return dict(zip(cells, await asyncio.gather(*[_one(c) for c in cells])))
