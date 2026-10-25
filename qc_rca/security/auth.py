"""API-key authentication, role-based access control and rate limiting.

* Keys are random 32-byte tokens shown once at creation; only an HMAC-SHA256
  (keyed with ``API_KEY_PEPPER``) is stored.
* Roles are ordered viewer < analyst < admin.
* Rate limiting is an in-process sliding window per user, adequate for the
  single-process local deployment (see docs/SECURITY.md for multi-instance).
"""
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session

from qc_rca.config import get_settings
from qc_rca.db.models import ApiUser, Role
from qc_rca.db.session import session_scope

ROLE_ORDER = {Role.viewer: 0, Role.analyst: 1, Role.admin: 2}
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True)
class Principal:
    name: str
    role: Role


def hash_key(raw: str) -> str:
    pepper = get_settings().api_key_pepper.get_secret_value().encode()
    return hmac.new(pepper, raw.encode(), hashlib.sha256).hexdigest()


def create_api_user(session: Session, name: str, role: Role) -> str:
    """Create (or rotate) a user's key. Returns the raw key; it is not stored."""
    raw = "qc_" + secrets.token_urlsafe(32)
    user = session.scalar(select(ApiUser).where(ApiUser.name == name))
    if user:
        user.role, user.key_hash, user.key_prefix, user.is_active = role, hash_key(raw), raw[:10], True
    else:
        session.add(ApiUser(name=name, role=role, key_hash=hash_key(raw), key_prefix=raw[:10], is_active=True))
    return raw


def authenticate(raw_key: str | None) -> Principal | None:
    if not raw_key or len(raw_key) > 200:
        return None
    digest = hash_key(raw_key)
    with session_scope() as s:
        user = s.scalar(select(ApiUser).where(ApiUser.key_hash == digest, ApiUser.is_active.is_(True)))
        if not user or not hmac.compare_digest(user.key_hash, digest):
            return None
        return Principal(name=user.name, role=user.role)


def current_principal(request: Request, raw_key: str | None = Depends(api_key_header)) -> Principal:
    if not get_settings().auth_enabled:
        principal = Principal(name="local-dev", role=Role.admin)
    else:
        principal = authenticate(raw_key)
        if principal is None and get_settings().public_demo and not raw_key:
            principal = _visitor(request)
        if principal is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid API key",
                                headers={"WWW-Authenticate": "API-Key"})
    request.state.principal = principal
    return principal


VISITOR_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _visitor(request: Request) -> Principal:
    """Anonymous public-demo user, scoped to a random per-browser id (or the client IP)."""
    vid = request.headers.get("X-Visitor-Id", "")
    if not VISITOR_ID_RE.fullmatch(vid):
        vid = hashlib.sha256(_client_ip(request).encode()).hexdigest()[:16]
    return Principal(name=f"visitor-{vid[:24]}", role=Role(get_settings().public_demo_role))


def require_role(minimum: Role):
    def dep(principal: Principal = Depends(current_principal)) -> Principal:
        if ROLE_ORDER[principal.role] < ROLE_ORDER[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role '{minimum.value}' or higher")
        return principal
    return dep


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, float]:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False, round(60 - (now - q[0]), 1)
            q.append(now)
            return True, 0.0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(get_settings().rate_limit_per_minute)
    return _limiter


def rate_limited(minimum: Role):
    """RBAC + per-user rate limit, for expensive endpoints (investigations)."""
    def dep(request: Request, principal: Principal = Depends(require_role(minimum))) -> Principal:
        # Visitors can mint new ids, so they are limited per IP rather than per name.
        key = f"ip:{_client_ip(request)}" if principal.name.startswith("visitor-") else principal.name
        ok, retry_after = get_rate_limiter().check(key)
        if not ok:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded",
                                headers={"Retry-After": str(int(retry_after) + 1)})
        return principal
    return dep
