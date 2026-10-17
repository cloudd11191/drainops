"""Password hashing, HS256 tokens and a tiny rate limiter, all stdlib (no extra dependencies)."""
import base64, collections, hashlib, hmac, json, os, time

def hash_password(pw: str, iters: int = 600_000) -> str:
    salt = os.urandom(16)
    return f"pbkdf2${iters}${salt.hex()}${hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, iters).hex()}"

def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it)).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calc, h)

DUMMY_HASH = hash_password("x", 1000)   # verified against for unknown emails so timing does not reveal accounts

def _b64(b: bytes) -> str: return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
def _unb64(s: str) -> bytes: return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
def _sig(msg: str, secret: str) -> str: return _b64(hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest())

def make_token(payload: dict, secret: str, ttl_s: int) -> str:
    now = int(time.time())
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps({**payload, "iat": now, "exp": now + ttl_s}, separators=(",", ":")).encode())
    return f"{head}.{body}.{_sig(head + '.' + body, secret)}"

def read_token(tok: str, secret: str):
    try:
        head, body, sig = tok.split(".")
        if not hmac.compare_digest(sig, _sig(head + "." + body, secret)): return None
        if json.loads(_unb64(head)).get("alg") != "HS256": return None
        data = json.loads(_unb64(body))
    except Exception:
        return None
    return data if data.get("exp", 0) > time.time() else None

class RateLimiter:
    """Per-process sliding window. With several server instances, put a shared limiter (e.g. at the proxy) in front."""
    def __init__(self, limit: int, window_s: int):
        self.limit, self.window, self.h = limit, window_s, collections.defaultdict(collections.deque)
    def allow(self, key: str) -> bool:
        now, q = time.monotonic(), self.h[key]
        while q and now - q[0] > self.window: q.popleft()
        if len(q) >= self.limit: return False
        q.append(now); return True
