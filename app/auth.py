import base64
import hmac
import ipaddress
import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Optional

from fastapi import Request

from . import config


def _signature_secret() -> bytes:
    key = config.ACCESS_KEY or config.DEFAULT_API_KEY
    if not key:
        key = "gpt-image-panel-dev-secret"
    return key.encode("utf-8")


def _base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def create_access_token() -> tuple[str, datetime]:
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=config.ACCESS_KEY_SESSION_MINUTES
    )
    payload = {"exp": int(expires_at.timestamp())}
    payload_raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_part = _base64url_encode(payload_raw)
    signature = hmac.new(
        _signature_secret(), payload_part.encode("ascii"), sha256
    ).digest()
    return f"{payload_part}.{_base64url_encode(signature)}", expires_at


def verify_access_token(token: Optional[str]) -> Optional[datetime]:
    if not token or "." not in token:
        return None

    payload_part, signature_part = token.split(".", 1)
    expected_signature = hmac.new(
        _signature_secret(), payload_part.encode("ascii"), sha256
    ).digest()

    try:
        actual_signature = _base64url_decode(signature_part)
    except Exception:
        return None

    if not hmac.compare_digest(actual_signature, expected_signature):
        return None

    try:
        payload = json.loads(_base64url_decode(payload_part))
        expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc)
    except Exception:
        return None

    if expires_at <= datetime.now(timezone.utc):
        return None
    return expires_at


def get_client_ip(request: Request) -> str:
    if config.TRUST_PROXY_HEADERS:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip()

        real_ip = request.headers.get("x-real-ip", "")
        if real_ip:
            return real_ip.strip()

    return request.client.host if request.client else ""


def _allowlist_entries() -> list[str]:
    return [
        entry.strip()
        for entry in config.IP_ALLOWLIST.replace(";", ",").split(",")
        if entry.strip()
    ]


def is_ip_allowed(ip_text: str) -> bool:
    entries = _allowlist_entries()
    if not entries:
        return True

    try:
        client_ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False

    for entry in entries:
        try:
            if "/" in entry:
                if client_ip in ipaddress.ip_network(entry, strict=False):
                    return True
            elif client_ip == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue

    return False
