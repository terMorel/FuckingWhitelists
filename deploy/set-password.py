#!/usr/bin/env python3
"""Rotate the HyBoard admin password without exposing it in process arguments."""

from __future__ import annotations

import getpass
import os
import sys
import tempfile
from pathlib import Path

from argon2 import PasswordHasher

ENV_PATH = Path("/etc/hyboard/hyboard.env")


def update_env(path: Path, encoded_hash: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    replacement = f"HYBOARD_ADMIN_PASSWORD_HASH={encoded_hash}"
    updated = False
    result = []
    for line in lines:
        if line.startswith("HYBOARD_ADMIN_PASSWORD_HASH="):
            result.append(replacement)
            updated = True
        else:
            result.append(line)
    if not updated:
        raise RuntimeError("Password hash setting is missing")

    descriptor, temporary = tempfile.mkstemp(prefix="hyboard.env.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("\n".join(result) + "\n")
        os.chmod(temporary, 0o640)
        os.chown(temporary, path.stat().st_uid, path.stat().st_gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Run as root")
    password = (
        getpass.getpass("New admin password: ")
        if sys.stdin.isatty()
        else sys.stdin.readline().rstrip("\r\n")
    )
    if len(password) < 16:
        raise SystemExit("Password must contain at least 16 characters")
    update_env(ENV_PATH, PasswordHasher().hash(password))
    print("Password updated. Restart hyboard.service to apply it.")


if __name__ == "__main__":
    main()
