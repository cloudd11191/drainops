"""SQLAlchemy models. SQLite by default; set DATABASE_URL to PostgreSQL in production."""
import os, datetime as dt
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

URL = os.getenv("DATABASE_URL", "sqlite:///./drainops.db").replace("postgres://", "postgresql://", 1)
engine = create_engine(URL, connect_args={"check_same_thread": False} if URL.startswith("sqlite") else {}, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()
now = dt.datetime.utcnow

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True); name = Column(String); role = Column(String)
    pw_hash = Column(String); active = Column(Boolean, default=True); created = Column(DateTime, default=now)

class Drain(Base):
    __tablename__ = "drains"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, index=True)       # QR / ID tag (registry drain_id)
    name = Column(String); state = Column(String, index=True); ward_no = Column(Integer)
    ward = Column(String, index=True); municipality = Column(String)
    lat = Column(Float); lon = Column(Float); geometry_geojson = Column(Text)
    width_m = Column(Float, default=0.5); depth_m = Column(Float, default=0.6); material = Column(String)
    blockage_condition = Column(String); registry_risk = Column(Float); registry_priority = Column(String); rain_snapshot_mm = Column(Float)
    upstream_id = Column(Integer, ForeignKey("drains.id"), nullable=True)   # legacy, unused
    downstream_id = Column(Integer, ForeignKey("drains.id"), nullable=True)  # this segment drains INTO that one
    health = Column(Float, default=100.0)
    last_inspected = Column(DateTime); last_cleaned = Column(DateTime)       # last_cleaned only set by a VERIFIED cleaning
    flood_history = Column(Integer)                                          # monsoon waterlogging reports; None = unknown

class Inspection(Base):                                  # one scored photo
    __tablename__ = "inspections"
    id = Column(Integer, primary_key=True)
    drain_id = Column(Integer, ForeignKey("drains.id"), index=True)
    report_id = Column(Integer, index=True)              # FieldReport this photo belongs to, if any
    kind = Column(String, default="routine")             # routine | before | after
    tier = Column(String); health = Column(Float); confidence = Column(Float)
    plastic = Column(Float); silt = Column(Float); organic = Column(Float)
    model_mode = Column(String); photo_path = Column(String); inspector = Column(String)
    corrected_tier = Column(String); client_id = Column(String, index=True)
    lat = Column(Float); lon = Column(Float); depth_cm = Column(Float); cover = Column(String); flow = Column(String)
    note = Column(Text); voice_path = Column(String)
    created = Column(DateTime, default=now, index=True)

class FieldReport(Base):                                 # one offline-queue item: checklist + before/after pair
    __tablename__ = "field_reports"
    id = Column(Integer, primary_key=True)
    client_id = Column(String, unique=True, index=True)  # idempotency key from the device queue
    drain_id = Column(Integer, ForeignKey("drains.id"), index=True)
    actor = Column(String); lat = Column(Float); lon = Column(Float)
    depth_cm = Column(Float); cover_type = Column(String); notes = Column(Text); voice_path = Column(String)
    before_id = Column(Integer); after_id = Column(Integer)
    before_health = Column(Float); after_health = Column(Float); model_mode = Column(String)
    before_at = Column(DateTime); after_at = Column(DateTime)         # device clock
    checks_json = Column(Text); status = Column(String, index=True)   # incomplete | pending_review | verified | rejected
    reviewed_by = Column(String); review_note = Column(Text); verified_at = Column(DateTime, index=True)
    work_order_id = Column(Integer); created = Column(DateTime, default=now, index=True)

class Contractor(Base):
    __tablename__ = "contractors"
    id = Column(Integer, primary_key=True); name = Column(String, unique=True); zone = Column(String)

class WorkOrder(Base):
    __tablename__ = "work_orders"
    id = Column(Integer, primary_key=True)
    batch = Column(Integer, index=True); drain_id = Column(Integer, ForeignKey("drains.id"), index=True)
    risk = Column(Float); status = Column(String, default="assigned", index=True)   # assigned|in_progress|completed|verified
    crew = Column(String); contractor_id = Column(Integer, ForeignKey("contractors.id"), nullable=True)
    created = Column(DateTime, default=now)                                          # = assigned
    started = Column(DateTime); completed = Column(DateTime); verified_at = Column(DateTime)

class TrainingExample(Base):                             # inspector corrections awaiting the next retrain
    __tablename__ = "training_examples"
    id = Column(Integer, primary_key=True); client_id = Column(String, unique=True, index=True)
    drain_id = Column(Integer, nullable=True); photo_path = Column(String)
    corrected_health = Column(Integer); plastic = Column(Float); silt = Column(Float); organic = Column(Float)   # fractions 0-1
    predicted_health = Column(Float); confidence = Column(Float); model_mode = Column(String)
    actor = Column(String); role = Column(String); exported = Column(Boolean, default=False); created = Column(DateTime, default=now)

class HealthSnapshot(Base):                              # feeds the Digital Twin time slider
    __tablename__ = "health_snapshots"
    id = Column(Integer, primary_key=True); drain_id = Column(Integer, ForeignKey("drains.id"), index=True)
    taken = Column(DateTime, default=now, index=True); health = Column(Float); source = Column(String)

class CitizenReport(Base):
    __tablename__ = "citizen_reports"
    id = Column(Integer, primary_key=True); ref = Column(String, unique=True, index=True)
    name = Column(String); ward = Column(String); location = Column(String); issue = Column(String); urgency = Column(String)
    lat = Column(Float); lon = Column(Float); photo_path = Column(String); photo_health = Column(Float); model_mode = Column(String)
    auto_score = Column(Integer); matched_drain_id = Column(Integer); status = Column(String, default="queued", index=True)
    reviewed_by = Column(String); created = Column(DateTime, default=now, index=True)

class AuditLog(Base):                                    # append-only; see app/audit.py
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    actor = Column(String); role = Column(String); action = Column(String); target = Column(String); change = Column(Text)
    timestamp = Column(String); prev_hash = Column(String); hash = Column(String)
    detail = Column(Text); created = Column(DateTime, default=now)     # `detail` kept for rows written by older versions

Base.metadata.create_all(engine)

def _migrate():
    """Add columns introduced after a first deploy (SQLite and PostgreSQL)."""
    from sqlalchemy import text, inspect as sa_inspect
    insp = sa_inspect(engine)
    new = {"drains": {"downstream_id": "INTEGER", "flood_history": "INTEGER", "name": "VARCHAR", "state": "VARCHAR", "ward_no": "INTEGER",
                      "blockage_condition": "VARCHAR", "registry_risk": "FLOAT", "registry_priority": "VARCHAR", "rain_snapshot_mm": "FLOAT"},
           "inspections": {"report_id": "INTEGER", "lat": "FLOAT", "lon": "FLOAT", "depth_cm": "FLOAT", "cover": "VARCHAR", "flow": "VARCHAR",
                           "note": "TEXT", "voice_path": "VARCHAR", "client_id": "VARCHAR"},
           "work_orders": {"contractor_id": "INTEGER", "started": "TIMESTAMP", "verified_at": "TIMESTAMP"},
           "audit_log": {"role": "VARCHAR", "target": "VARCHAR", "change": "TEXT", "timestamp": "VARCHAR", "prev_hash": "VARCHAR", "hash": "VARCHAR"}}
    for tbl, cols in new.items():
        have = {c["name"] for c in insp.get_columns(tbl)}
        with engine.begin() as cn:
            for c, ty in cols.items():
                if c not in have: cn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {c} {ty}"))
_migrate()
