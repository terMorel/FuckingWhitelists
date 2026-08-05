#!/usr/bin/env python3
"""Root broker exposing the narrow hyboard-helper CLI over a group-only Unix socket."""

from __future__ import annotations

import json
import os
import re
import socketserver
import subprocess
from pathlib import Path

SOCKET_PATH = Path("/run/hyboard/helper.sock")
HELPER_PATH = Path("/usr/local/libexec/hyboard-helper")
ALLOWED = {"list", "status", "monitor", "add", "show", "rotate", "revoke"}
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        line = self.rfile.readline(4097)
        if not line or len(line) > 4096:
            self.respond(error="Invalid request")
            return
        try:
            request = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.respond(error="Invalid JSON")
            return
        action = request.get("action")
        username = request.get("username")
        if action not in ALLOWED:
            self.respond(error="Unsupported operation")
            return
        command = [str(HELPER_PATH), action]
        if action not in {"list", "status", "monitor"}:
            if not isinstance(username, str) or not USERNAME.fullmatch(username):
                self.respond(error="Invalid username")
                return
            command.append(username)
        elif username is not None:
            self.respond(error="Unexpected username")
            return
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=25,
                check=False,
                env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
            )
        except (OSError, subprocess.TimeoutExpired):
            self.respond(error="Helper unavailable")
            return
        if result.returncode:
            self.respond(error=(result.stderr or result.stdout or "Operation failed").strip()[:240])
            return
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.respond(error="Invalid helper response")
            return
        self.respond(result=payload)

    def respond(self, *, result: dict | None = None, error: str | None = None) -> None:
        payload = {"ok": error is None}
        payload["result" if error is None else "error"] = result if error is None else error
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode() + b"\n")


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    SOCKET_PATH.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    with Server(str(SOCKET_PATH), Handler) as server:
        os.chmod(SOCKET_PATH, 0o660)
        server.serve_forever()


if __name__ == "__main__":
    main()
