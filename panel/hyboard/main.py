from __future__ import annotations

import hmac
import re
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from .backend import BackendError, DemoBackend, SocketBackend
from .config import Settings
from .db import Database
from .monitoring import (
    DemoStatsClient,
    DisabledStatsClient,
    HysteriaStatsClient,
    MonitoringService,
    TelegramNotifier,
)
from .security import LoginLimiter, csrf_token, require_admin, verify_csrf, verify_password

ROOT = Path(__file__).parent


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=200)
    expires_at: datetime | None = None


class ProbeReport(BaseModel):
    ok: bool
    latency_ms: float | None = Field(default=None, ge=0, le=300_000)
    detail: str = Field(default="", max_length=200)
    network: str = Field(default="", max_length=80)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    db = Database(settings.db_path)
    db.init()
    backend = (
        DemoBackend()
        if settings.backend == "demo"
        else SocketBackend(settings.helper_socket)
    )
    templates = Jinja2Templates(directory=ROOT / "templates")
    limiter = LoginLimiter()
    if settings.demo:
        stats_client = DemoStatsClient()
    elif settings.traffic_stats_enabled:
        stats_client = HysteriaStatsClient(
            settings.traffic_stats_url, settings.traffic_stats_secret
        )
    else:
        stats_client = DisabledStatsClient()
    monitoring = MonitoringService(
        db,
        backend,
        stats_client,
        TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id),
        settings.probe_stale_seconds,
    )

    app = FastAPI(title="BatyaVPN Control", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.db = db
    app.state.backend = backend
    app.state.monitoring = monitoring
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="fwcontrol_session",
        max_age=43200,
        same_site="strict",
        https_only=settings.cookie_secure,
    )
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; object-src 'none'; frame-ancestors 'none'"
        )
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(BackendError)
    async def backend_error(_request: Request, exc: BackendError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if request.session.get("authenticated"):
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"demo": settings.demo, "error": None},
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login(request: Request, password: str = Form(...)):
        key = request.client.host if request.client else "unknown"
        if not limiter.allowed(key):
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "demo": settings.demo,
                    "error": "Слишком много попыток. Подождите 5 минут.",
                },
                status_code=429,
            )
        valid = (
            password == "demo"  # noqa: S105 - intentional local demo credential
            if settings.demo
            else verify_password(settings.admin_hash, password)
        )
        if not valid:
            limiter.fail(key)
            db.audit("login_failed", detail=key)
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"demo": settings.demo, "error": "Неверный пароль"},
                status_code=401,
            )
        limiter.clear(key)
        request.session.clear()
        request.session["authenticated"] = True
        csrf_token(request)
        db.audit("login")
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        require_admin(request)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        if not request.session.get("authenticated"):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"csrf": csrf_token(request), "demo": settings.demo},
        )

    @app.get("/api/state")
    async def state(request: Request):
        require_admin(request)
        if settings.demo:
            monitoring.collect()
        users = backend.list_users()
        metadata = db.metadata()
        monitor_state = monitoring.snapshot()
        traffic = monitor_state["traffic"]
        now = datetime.now(timezone.utc)
        for user in users:
            meta = metadata.get(user["username"], {})
            user.update(meta)
            expiry = meta.get("expires_at")
            user["expired"] = bool(expiry and datetime.fromisoformat(expiry) <= now)
            user["traffic"] = traffic.get(
                user["username"],
                {
                    "tx_total": 0,
                    "rx_total": 0,
                    "tx_rate": 0,
                    "rx_rate": 0,
                    "connections": 0,
                    "online": False,
                    "updated_at": None,
                },
            )
        return {
            "users": users,
            "status": backend.status(),
            "audit": db.recent_audit(),
            "demo": settings.demo,
            "monitoring": monitor_state,
        }

    @app.post("/api/monitoring/collect")
    async def collect_monitoring(request: Request):
        require_admin(request)
        verify_csrf(request)
        return monitoring.collect()

    @app.post("/api/probes/{name}")
    async def report_probe(name: str, payload: ProbeReport, request: Request):
        if not settings.probe_token:
            raise HTTPException(status_code=503, detail="External probes are disabled")
        supplied = request.headers.get("Authorization", "")
        expected = f"Bearer {settings.probe_token}"
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="Invalid probe token")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
            raise HTTPException(status_code=400, detail="Invalid probe name")
        db.record_probe(
            name,
            payload.ok,
            payload.latency_ms,
            payload.detail,
            payload.network,
            datetime.now(timezone.utc),
        )
        return {"ok": True}

    @app.post("/api/users")
    async def create_user(payload: UserCreate, request: Request):
        require_admin(request)
        verify_csrf(request)
        bundle = backend.create(payload.username)
        expiry = (
            payload.expires_at.astimezone(timezone.utc).isoformat()
            if payload.expires_at
            else None
        )
        db.upsert_user(bundle.username, payload.note.strip(), expiry)
        db.audit("user_created", bundle.username, f"expires={expiry or 'never'}")
        return bundle.__dict__

    @app.get("/api/users/{username}/access")
    async def user_access(username: str, request: Request):
        require_admin(request)
        bundle = backend.access(username)
        db.audit("access_viewed", username)
        return bundle.__dict__

    @app.post("/api/users/{username}/rotate")
    async def rotate_user(username: str, request: Request):
        require_admin(request)
        verify_csrf(request)
        bundle = backend.rotate(username)
        db.audit("key_rotated", username)
        return bundle.__dict__

    @app.delete("/api/users/{username}")
    async def revoke_user(username: str, request: Request):
        require_admin(request)
        verify_csrf(request)
        backend.revoke(username)
        db.delete_user(username)
        db.audit("user_revoked", username)
        return {"ok": True}

    return app


def run() -> None:
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.bind, port=settings.port, proxy_headers=False)


if __name__ == "__main__":
    run()
