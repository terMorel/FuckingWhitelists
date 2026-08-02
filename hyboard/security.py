from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, status

hasher = PasswordHasher()


class LoginLimiter:
    def __init__(self, attempts: int = 6, window: int = 300):
        self.attempts = attempts
        self.window = window
        self.entries: dict[str, deque[float]] = defaultdict(deque)

    def allowed(self, key: str) -> bool:
        now = time.monotonic()
        queue = self.entries[key]
        while queue and queue[0] < now - self.window:
            queue.popleft()
        return len(queue) < self.attempts

    def fail(self, key: str) -> None:
        self.entries[key].append(time.monotonic())

    def clear(self, key: str) -> None:
        self.entries.pop(key, None)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return hasher.verify(encoded, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def require_admin(request: Request) -> None:
    if request.session.get("authenticated") is not True:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход")


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf"] = token
    return token


def verify_csrf(request: Request) -> None:
    expected = request.session.get("csrf", "")
    supplied = request.headers.get("x-csrf-token", "")
    if not expected or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Сессия устарела")

