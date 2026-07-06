import base64
from typing import TypeGuard
from urllib.parse import urlsplit

from pydantic import BaseModel, field_validator, ValidationError


class _StatePayload(BaseModel):
    nonce: str
    return_to: str | None = None

    @field_validator("nonce")
    @classmethod
    def nonce_not_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("nonce must not be empty")
        return v


def build_state(nonce: str, return_to: str | None) -> str:
    payload = _StatePayload(nonce=nonce, return_to=return_to)
    raw = payload.model_dump_json(exclude_none=True).encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def parse_state(state: str) -> tuple[str, str | None]:
    """Never raises. Returns (raw_state, None) on any parse failure."""
    padding = "=" * (-len(state) % 4)
    try:
        raw = base64.urlsafe_b64decode(state + padding)
        payload = _StatePayload.model_validate_json(raw)
        return payload.nonce, payload.return_to
    except ValueError, ValidationError:
        return state, None


def origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def is_allowed_return_to(url: str | None, allowed_origins: list[str]) -> TypeGuard[str]:
    if not url:
        return False
    origin = origin_of(url)
    return origin is not None and origin in set(allowed_origins)
