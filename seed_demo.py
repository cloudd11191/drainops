"""Seed SIMULATED drain segments around Panihati/Kamarhati/Barrackpore for demos.
These are fake coordinates/health values for UI testing, NOT real survey data."""
import random, json, datetime as dt
from app.db import SessionLocal, Drain
random.seed(7)
CENTERS = {"Panihati": (22.6912, 88.3730), "Kamarhati": (22.6720, 88.3740), "Barrackpore": (22.7640, 88.3770)}
s = SessionLocal(); n = 0
for muni, (la, lo) in CENTERS.items():
    for w in range(1, 4):
        for k in range(15):
            lat, lon = la + random.uniform(-.01, .01), lo + random.uniform(-.01, .01)
            n += 1
            s.add(Drain(code=f"{muni[:3].upper()}-W{w}-{k:03d}", ward=f"{muni} Ward {w}", municipality=muni,
                        lat=lat, lon=lon, width_m=random.choice([.3, .45, .6, .9]), depth_m=random.choice([.4, .6, .9]),
                        geometry_geojson=json.dumps({"type": "LineString", "coordinates": [[lon, lat], [lon + .0006, lat + .0003]]}),
                        health=random.choice([25, 40, 55, 70, 85, 95]),
                        last_cleaned=dt.datetime.utcnow() - dt.timedelta(days=random.randint(5, 150))))
s.commit(); print(f"seeded {n} simulated drains")
