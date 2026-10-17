"""Tamper-evident audit trail: each row stores sha256(previous_hash + its own content)."""
import datetime as dt, hashlib, json

def chain_hash(prev: str, e: dict) -> str:
    payload = json.dumps([e["actor"], e["role"], e["action"], e["target"], e["change"], e["timestamp"]],
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((prev + payload).encode()).hexdigest()

def verify_chain(entries: list) -> dict:
    """entries oldest -> newest, each a dict with the fields above plus prev_hash/hash."""
    prev = ""
    for i, e in enumerate(entries):
        if e["prev_hash"] != prev or chain_hash(prev, e) != e["hash"]:
            return {"ok": False, "first_bad_index": i, "checked": len(entries)}
        prev = e["hash"]
    return {"ok": True, "checked": len(entries)}

def write(s, actor: str, role: str, action: str, target: str, change: str):
    """Append an entry (caller commits). NOTE: concurrent writers can race on the previous hash;
    on PostgreSQL serialise with an advisory lock if you run many writers."""
    from .db import AuditLog
    last = s.query(AuditLog).order_by(AuditLog.id.desc()).first()
    e = {"actor": actor or "unknown", "role": role or "unknown", "action": action, "target": target or "", "change": change or "",
         "timestamp": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"}
    prev = last.hash if last else ""
    s.add(AuditLog(**e, prev_hash=prev, hash=chain_hash(prev, e)))
