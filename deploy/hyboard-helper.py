#!/usr/bin/env python3
"""Root-owned, deliberately narrow bridge between HyBoard and hy-access."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HY_ACCESS = Path("/usr/local/sbin/hy-access")
USERS = Path("/etc/hysteria/users.json")
ACCESS_DIR = Path("/root/hysteria-access")
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
URI = re.compile(r"hysteria2://[^\s`'\"]+")
ALLOWED = {"list", "status", "add", "show", "rotate", "revoke"}


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def output(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def check_name(value: str) -> str:
    if not USERNAME.fullmatch(value):
        fail("Invalid username")
    return value


def run_hy_access(action: str, username: str) -> None:
    result = subprocess.run(
        [str(HY_ACCESS), action, username],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
    )
    if result.returncode:
        fail((result.stderr or result.stdout or "hy-access failed").strip()[:240])


def find_uri(username: str) -> str:
    path = ACCESS_DIR / f"{username}.txt"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        fail("Access link was not generated")
    match = URI.search(content)
    if not match:
        fail("Access link has an unexpected format")
    return match.group(0)


def list_users() -> None:
    try:
        data = json.loads(USERS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("Cannot read Hysteria users")
    if not isinstance(data, dict):
        fail("Hysteria users file has an unexpected format")
    output({"users": [{"username": name} for name in sorted(data) if USERNAME.fullmatch(name)]})


def has_udp_443(ss_output: str) -> bool:
    columns = [line.split() for line in ss_output.splitlines()]
    return any(parts[3].endswith(":443") for parts in columns if len(parts) >= 5)


def status() -> None:
    service = subprocess.run(
        ["/usr/bin/systemctl", "is-active", "hysteria-server.service"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if service.returncode:
        service = subprocess.run(
            ["/usr/bin/systemctl", "is-active", "hysteria.service"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    sockets = subprocess.run(
        ["/usr/bin/ss", "-H", "-lun"], capture_output=True, text=True, timeout=5, check=False
    )
    udp443 = has_udp_443(sockets.stdout)
    output({"service": service.stdout.strip() or "unknown", "udp443": udp443, "mode": "native"})


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ALLOWED:
        fail("Unsupported operation", 2)
    action = sys.argv[1]
    if action == "list":
        if len(sys.argv) != 2:
            fail("Unexpected arguments", 2)
        list_users()
        return
    if action == "status":
        if len(sys.argv) != 2:
            fail("Unexpected arguments", 2)
        status()
        return
    if len(sys.argv) != 3:
        fail("Username required", 2)
    username = check_name(sys.argv[2])
    run_hy_access(action, username)
    if action == "revoke":
        output({"ok": True})
        return
    output({"username": username, "uri": find_uri(username)})


if __name__ == "__main__":
    main()
