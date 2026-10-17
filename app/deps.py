from dataclasses import dataclass
from fastapi import Depends, Header, HTTPException
from . import config
from .db import SessionLocal, User
from .security import read_token

@dataclass
class Actor:
    id: int; name: str; role: str; email: str

def db():
    s = SessionLocal()
    try: yield s
    finally: s.close()

def optional_user(authorization: str = Header(None), x_actor: str = Header(None), s=Depends(db)):
    if config.AUTH_MODE == "open":                       # LOCAL DEV ONLY: no authentication, everyone is admin
        return Actor(0, (x_actor or "Local dev")[:60], "admin", "dev@local")
    if not authorization or not authorization.lower().startswith("bearer "): return None
    data = read_token(authorization[7:], config.SECRET_KEY)
    u = s.get(User, data["sub"]) if data else None
    return Actor(u.id, u.name, u.role, u.email) if u and u.active else None

def current_user(u=Depends(optional_user)):
    if not u: raise HTTPException(401, "Sign in required", headers={"WWW-Authenticate": "Bearer"})
    return u

def require(*roles):
    def dep(u=Depends(current_user)):
        if u.role not in roles: raise HTTPException(403, f"Requires role: {', '.join(roles)}")
        return u
    return dep
