from __future__ import annotations

import hashlib
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from urllib.parse import urlparse

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import get_settings

_hasher = PasswordHasher()
_login_hits: dict[str, list[float]] = defaultdict(list)

PURPOSE_CATEGORY = {
    "subtopic_map": "first_subtopics",
    "subtopic_regenerate": "other",
    "weekly_enrich": "recurring_subtopics",
    "question": "revision",
    "evaluate": "revision",
    "evaluate_and_next": "revision",
    "final_summary": "revision",
    "key_test": "other",
}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def create_access_token(user_id: int, role: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])


def new_refresh_secret() -> str:
    return secrets.token_urlsafe(48)


def as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_expiry(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now + timedelta(days=get_settings().refresh_token_days)


def encrypt_secret(value: str) -> str:
    keys = get_settings().fernet_key_list
    if not keys:
        raise RuntimeError("FERNET_KEYS is not set")
    fernet = MultiFernet([Fernet(key.encode("utf-8")) for key in keys])
    return fernet.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    keys = get_settings().fernet_key_list
    if not keys:
        raise RuntimeError("FERNET_KEYS is not set")
    fernet = MultiFernet([Fernet(key.encode("utf-8")) for key in keys])
    try:
        return fernet.decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Could not decrypt secret") from exc


def allow_login_ip(ip: str, now: float | None = None) -> bool:
    import time

    settings = get_settings()
    moment = now if now is not None else time.time()
    window_start = moment - settings.login_ip_window_seconds
    hits = [item for item in _login_hits[ip] if item >= window_start]
    if len(hits) >= settings.login_ip_limit:
        _login_hits[ip] = hits
        return False
    hits.append(moment)
    _login_hits[ip] = hits
    return True


def reset_login_limits() -> None:
    _login_hits.clear()


_BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}


def validate_base_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Base URL must be https")
    host = parsed.hostname.lower()
    if host in _BLOCKED_HOSTS:
        raise ValueError("Base URL host is not allowed")
    try:
        ip = ip_address(host)
    except ValueError:
        return url.strip()
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise ValueError("Base URL must not point at a private address")
    return url.strip()
