from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from fastapi import Header, HTTPException

from common import config


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    ).hex()
    return f"pbkdf2${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, salt, digest = encoded.split("$", 2)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), encoded)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session_token(username: str, ttl_seconds: int = 86400) -> str:
    payload = {
        "username": username,
        "exp": int(time.time()) + ttl_seconds,
        "nonce": secrets.token_hex(8),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(
        config.AUTH_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    return (
        base64.urlsafe_b64encode(body).decode("utf-8").rstrip("=")
        + "."
        + base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")
    )


def decode_session_token(token: str) -> dict[str, Any]:
    try:
        body_part, sig_part = token.split(".", 1)
        body = base64.urlsafe_b64decode(body_part + "=" * (-len(body_part) % 4))
        signature = base64.urlsafe_b64decode(sig_part + "=" * (-len(sig_part) % 4))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="invalid session token") from exc

    expected = hmac.new(
        config.AUTH_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="invalid session token")

    payload = json.loads(body.decode("utf-8"))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=401, detail="session expired")
    return payload


def extract_bearer(value: str | None) -> str:
    if not value:
        raise HTTPException(status_code=401, detail="missing authorization header")
    if not value.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="expected bearer token")
    return value.split(" ", 1)[1].strip()


def require_user(authorization: str | None = Header(default=None)) -> str:
    token = extract_bearer(authorization)
    payload = decode_session_token(token)
    return str(payload["username"])


def random_token(prefix: str = "") -> str:
    body = secrets.token_urlsafe(24)
    return f"{prefix}{body}" if prefix else body


def ensure_bootstrap_token() -> str:
    token = config.env("RACKPATCH_AGENT_BOOTSTRAP_TOKEN", config.DEFAULT_AGENT_BOOTSTRAP_TOKEN)
    if not token or token == "bootstrap-me":
        token = f"rackpatch-bootstrap-{secrets.token_urlsafe(18)}"
        os.environ["RACKPATCH_AGENT_BOOTSTRAP_TOKEN"] = token
    return token
