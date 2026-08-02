from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from .backend import BackendError, DemoBackend, SocketBackend
from .config import Settings
from .db import Database
from .security import LoginLimiter, csrf_token, require_admin, verify_csrf, verify_password

ROOT = Path(__file__).parent


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=200)
    expires_at: datetime | None = None


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

    app = FastAPI(title="Fucking Whitelists Control", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.db = db
    app.state.backend = backend
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
        users = backend.list_users()
        metadata = db.metadata()
        now = datetime.now(timezone.utc)
        for user in users:
            meta = metadata.get(user["username"], {})
            user.update(meta)
            expiry = meta.get("expires_at")
            user["expired"] = bool(expiry and datetime.fromisoformat(expiry) <= now)
        return {
            "users": users,
            "status": backend.status(),
            "audit": db.recent_audit(),
            "demo": settings.demo,
        }

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
