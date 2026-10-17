"""Crypto/token primitives: Argon2id passwords, hashed opaque tokens,
origin-check CSRF defence, in-process sliding-window rate limiter."""
import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# ---------------------------------------------------------------------------
# Tuning: RS 1000 G11 (8 vCPU) — verify cost ≈ 60-120 ms, comfortably safe.
# ---------------------------------------------------------------------------
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(pw_hash: str, password: str) -> bool:
    try:
        return _ph.verify(pw_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError):
        return False


def needs_rehash(pw_hash: str) -> bool:
    try:
        return _ph.check_needs_rehash(pw_hash)
    except InvalidHashError:
        return True


MIN_PASSWORD_LENGTH = 10


def password_policy_ok(password: str) -> bool:
    return isinstance(password, str) and len(password) >= MIN_PASSWORD_LENGTH


# ---------------------------------------------------------------------------
# Tokens: random urlsafe secrets; only SHA-256 hashes hit the database.
# ---------------------------------------------------------------------------
def new_token(prefix: str = "") -> str:
    t = secrets.token_urlsafe(32)
    return f"{prefix}{t}" if prefix else t


def token_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


SESSION_COOKIE = "ff_session"


# ---------------------------------------------------------------------------
# CSRF: state-changing requests must originate from OUR origin. Combined with
# SameSite=Lax cookies this stops forged cross-site posts without noisy
# per-form tokens. Allowed origins arrive via env (comma separated).
# ---------------------------------------------------------------------------
_ALLOWED_ORIGINS_CACHE: tuple[float, frozenset] = (0.0, frozenset())


def _allowed_origins() -> frozenset:
    global _ALLOWED_ORIGINS_CACHE
    import os
    raw = os.environ.get("FF_PUBLIC_ORIGIN", "")
    cache_ts, cached = _ALLOWED_ORIGINS_CACHE
    if cache_ts != int(os.stat.__module__ and time.time() // 60):
        parsed = frozenset(o.strip().rstrip("/") for o in raw.split(",") if o.strip())
        _ALLOWED_ORIGINS_CACHE = (int(time.time() // 60), parsed)
        return parsed
    return cached


def origin_ok(request_headers) -> bool:
    """True when Origin/Referer (if present) matches a trusted origin.
    Missing headers on unsafe methods are rejected (strict)."""
    hdrs = getattr(request_headers, "headers", request_headers)
    origin = (hdrs.get("origin") or "").strip().rstrip("/")
    referer = (hdrs.get("referer") or "").strip()
    allowed = _allowed_origins()
    if not allowed:
        return True  # misconfigured deployment: fail open on CORS, rely on SameSite
    if origin:
        return origin.lower() in allowed
    if referer:
        from urllib.parse import urlparse
        try:
            base = f"{urlparse(referer).scheme}://{urlparse(referer).netloc}"
        except ValueError:
            return False
        return base.lower() in allowed
    return False


# ---------------------------------------------------------------------------
# Sliding-window rate limiter (per bucket+key). Enough for a friends-sized
# instance; brute-force bursts are throttled, legit humans never notice.
# ---------------------------------------------------------------------------
_buckets: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def rate_hit(bucket: str, key: str, max_events: int, window_s: float) -> bool:
    """Record an event; return True if the caller exceeded the allowance."""
    k = (bucket, key.lower())
    cutoff = time.monotonic() - window_s
    with _lock:
        dq = _buckets[k]
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= max_events:
            dq.append(time.monotonic())
            return True
        dq.append(time.monotonic())
        return False
