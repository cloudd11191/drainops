from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from .. import audit, config
from ..db import User
from ..deps import db, current_user, require
from ..security import DUMMY_HASH, RateLimiter, hash_password, make_token, verify_password

router = APIRouter(tags=["auth"])
login_limiter = RateLimiter(10, 300)

class LoginIn(BaseModel):
    email: str = Field(max_length=200); password: str = Field(max_length=200)
class UserIn(BaseModel):
    email: str = Field(max_length=200); name: str = Field(max_length=60); role: str; password: str = Field(min_length=10, max_length=200)

def _pub(u): return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "active": bool(u.active)}

@router.post("/auth/login")
def login(body: LoginIn, request: Request, s=Depends(db)):
    ip = request.client.host if request.client else "?"
    if not login_limiter.allow(ip): raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    u = s.query(User).filter(func.lower(User.email) == body.email.strip().lower()).first()
    ok = verify_password(body.password, u.pw_hash if u else DUMMY_HASH)
    if not (u and u.active and ok):
        audit.write(s, body.email.strip()[:80], "anonymous", "Failed sign-in", "auth", f"from {ip}"); s.commit()
        raise HTTPException(401, "Invalid email or password")
    return {"token": make_token({"sub": u.id, "role": u.role}, config.SECRET_KEY, config.TOKEN_TTL_S), "token_type": "bearer", "user": _pub(u)}

@router.get("/auth/me")
def me(u=Depends(current_user)): return {"name": u.name, "role": u.role, "email": u.email, "auth_mode": config.AUTH_MODE}

@router.get("/users")
def users(s=Depends(db), u=Depends(require("admin"))): return [_pub(x) for x in s.query(User).order_by(User.id)]

@router.post("/users", status_code=201)
def create_user(body: UserIn, s=Depends(db), u=Depends(require("admin"))):
    if body.role not in config.ROLES: raise HTTPException(422, f"role must be one of {config.ROLES}")
    email = body.email.strip().lower()
    if "@" not in email: raise HTTPException(422, "Invalid email")
    if s.query(User).filter(func.lower(User.email) == email).first(): raise HTTPException(409, "Email already registered")
    nu = User(email=email, name=body.name.strip(), role=body.role, pw_hash=hash_password(body.password)); s.add(nu)
    audit.write(s, u.name, u.role, "User created", email, f"role={body.role}"); s.commit()
    return _pub(nu)

class ActiveIn(BaseModel): active: bool

@router.patch("/users/{uid}/active")
def set_active(uid: int, body: ActiveIn, s=Depends(db), u=Depends(require("admin"))):
    t = s.get(User, uid)
    if not t: raise HTTPException(404)
    if t.id == u.id and not body.active: raise HTTPException(409, "You cannot deactivate your own account")
    t.active = body.active; audit.write(s, u.name, u.role, "User activated" if body.active else "User deactivated", t.email, ""); s.commit()
    return _pub(t)
