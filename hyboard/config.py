from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    db_path: Path
    backend: str
    helper_socket: str
    session_secret: str
    admin_hash: str
    cookie_secure: bool
    bind: str
    port: int
    demo: bool

    @classmethod
    def from_env(cls) -> Settings:
        demo = _bool("HYBOARD_DEMO", False)
        session_secret = os.getenv("HYBOARD_SESSION_SECRET", "")
        admin_hash = os.getenv("HYBOARD_ADMIN_PASSWORD_HASH", "")
        if demo:
            session_secret = session_secret or secrets.token_urlsafe(32)
        return cls(
            db_path=Path(os.getenv("HYBOARD_DB", "./hyboard.db")),
            backend=os.getenv("HYBOARD_BACKEND", "demo" if demo else "helper"),
            helper_socket=os.getenv("HYBOARD_HELPER_SOCKET", "/run/hyboard/helper.sock"),
            session_secret=session_secret,
            admin_hash=admin_hash,
            cookie_secure=_bool("HYBOARD_COOKIE_SECURE", not demo),
            bind=os.getenv("HYBOARD_BIND", "127.0.0.1"),
            port=int(os.getenv("HYBOARD_PORT", "28474")),
            demo=demo,
        )

    def validate(self) -> None:
        if len(self.session_secret) < 32:
            raise RuntimeError("HYBOARD_SESSION_SECRET must contain at least 32 characters")
        if not self.demo and not self.admin_hash.startswith("$argon2"):
            raise RuntimeError("HYBOARD_ADMIN_PASSWORD_HASH must be an Argon2 hash")
