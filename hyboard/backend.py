from __future__ import annotations

import base64
import io
import json
import re
import socket
from dataclasses import dataclass

import qrcode

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class BackendError(RuntimeError):
    pass


def validate_username(value: str) -> str:
    value = value.strip()
    if not USERNAME_RE.fullmatch(value):
        raise BackendError("Имя: латиница, цифры, точка, дефис или подчёркивание; до 64 знаков")
    return value


@dataclass
class AccessBundle:
    username: str
    uri: str
    qr_data_url: str


def qr_data_url(uri: str) -> str:
    image = qrcode.make(uri)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    payload = base64.b64encode(stream.getvalue()).decode()
    return f"data:image/png;base64,{payload}"


class SocketBackend:
    def __init__(self, socket_path: str):
        self.socket_path = socket_path

    def _call(self, *args: str) -> dict:
        request = {"action": args[0]}
        if len(args) > 1:
            request["username"] = args[1]
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(20)
                client.connect(self.socket_path)
                client.sendall(json.dumps(request).encode() + b"\n")
                client.shutdown(socket.SHUT_WR)
                chunks = []
                while True:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if sum(map(len, chunks)) > 1_000_000:
                        raise BackendError("Системный помощник вернул слишком большой ответ")
        except (OSError, TimeoutError) as exc:
            raise BackendError("Системный помощник недоступен") from exc
        try:
            response = json.loads(b"".join(chunks))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BackendError("Системный помощник вернул некорректный ответ") from exc
        if not response.get("ok"):
            raise BackendError(str(response.get("error") or "Операция не выполнена")[:240])
        return response["result"]

    def list_users(self) -> list[dict]:
        return self._call("list")["users"]

    def status(self) -> dict:
        return self._call("status")

    def create(self, username: str) -> AccessBundle:
        data = self._call("add", validate_username(username))
        return AccessBundle(
            username=username, uri=data["uri"], qr_data_url=qr_data_url(data["uri"])
        )

    def access(self, username: str) -> AccessBundle:
        data = self._call("show", validate_username(username))
        return AccessBundle(
            username=username, uri=data["uri"], qr_data_url=qr_data_url(data["uri"])
        )

    def rotate(self, username: str) -> AccessBundle:
        data = self._call("rotate", validate_username(username))
        return AccessBundle(
            username=username, uri=data["uri"], qr_data_url=qr_data_url(data["uri"])
        )

    def revoke(self, username: str) -> None:
        self._call("revoke", validate_username(username))


class DemoBackend:
    """In-memory preview backend. It never contacts or changes the VPN server."""

    def __init__(self):
        self.users = {
            "kirill-phone": {"username": "kirill-phone"},
            "family-tablet": {"username": "family-tablet"},
            "travel-laptop": {"username": "travel-laptop"},
            "owner": {"username": "owner"},
        }

    def list_users(self) -> list[dict]:
        return list(self.users.values())

    def status(self) -> dict:
        return {"service": "active", "udp443": True, "mode": "demo"}

    @staticmethod
    def _bundle(username: str) -> AccessBundle:
        uri = f"hysteria2://DEMO_TOKEN@vpn.example:443/?insecure=1&obfs=salamander#FW-{username}"
        return AccessBundle(username=username, uri=uri, qr_data_url=qr_data_url(uri))

    def create(self, username: str) -> AccessBundle:
        username = validate_username(username)
        if username in self.users:
            raise BackendError("Пользователь уже существует")
        self.users[username] = {"username": username}
        return self._bundle(username)

    def access(self, username: str) -> AccessBundle:
        if username not in self.users:
            raise BackendError("Пользователь не найден")
        return self._bundle(username)

    def rotate(self, username: str) -> AccessBundle:
        return self.access(username)

    def revoke(self, username: str) -> None:
        if username not in self.users:
            raise BackendError("Пользователь не найден")
        del self.users[username]
