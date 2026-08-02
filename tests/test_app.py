from __future__ import annotations

import re

from fastapi.testclient import TestClient

from hyboard.config import Settings
from hyboard.main import create_app


def client(tmp_path):
    settings = Settings(
        db_path=tmp_path / "test.db",
        backend="demo",
        helper_socket="unused",
        session_secret="x" * 48,
        admin_hash="",
        cookie_secure=False,
        bind="127.0.0.1",
        port=28474,
        demo=True,
    )
    return TestClient(create_app(settings))


def login(test_client: TestClient) -> str:
    response = test_client.post("/login", data={"password": "demo"}, follow_redirects=False)
    assert response.status_code == 303
    dashboard = test_client.get("/")
    assert dashboard.status_code == 200
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', dashboard.text)
    assert match
    return match.group(1)


def test_health_and_auth(tmp_path):
    test_client = client(tmp_path)
    health = test_client.get("/healthz")
    assert health.json() == {"status": "ok"}
    assert health.headers["x-frame-options"] == "DENY"
    assert health.headers["cache-control"] == "no-store"
    assert test_client.get("/api/state").status_code == 401
    login(test_client)
    assert test_client.get("/api/state").status_code == 200


def test_create_view_rotate_and_revoke(tmp_path):
    test_client = client(tmp_path)
    csrf = login(test_client)
    headers = {"X-CSRF-Token": csrf}
    created = test_client.post(
        "/api/users",
        headers=headers,
        json={"username": "new-phone", "note": "Test phone", "expires_at": None},
    )
    assert created.status_code == 200
    assert created.json()["uri"].startswith("hysteria2://")
    state = test_client.get("/api/state").json()
    assert any(user["username"] == "new-phone" for user in state["users"])
    assert test_client.get("/api/users/new-phone/access").status_code == 200
    assert test_client.post("/api/users/new-phone/rotate", headers=headers).status_code == 200
    assert test_client.delete("/api/users/new-phone", headers=headers).status_code == 200


def test_csrf_and_username_validation(tmp_path):
    test_client = client(tmp_path)
    login(test_client)
    assert test_client.post(
        "/api/users", json={"username": "good-name", "note": "", "expires_at": None}
    ).status_code == 403
    csrf = re.search(
        r'<meta name="csrf-token" content="([^"]+)"', test_client.get("/").text
    ).group(1)
    response = test_client.post(
        "/api/users",
        headers={"X-CSRF-Token": csrf},
        json={"username": "bad name!", "note": "", "expires_at": None},
    )
    assert response.status_code == 400
