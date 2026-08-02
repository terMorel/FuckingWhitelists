from __future__ import annotations

from .backend import BackendError, SocketBackend
from .config import Settings
from .db import Database


def run() -> None:
    settings = Settings.from_env()
    settings.validate()
    db = Database(settings.db_path)
    db.init()
    backend = SocketBackend(settings.helper_socket)
    active = {item["username"] for item in backend.list_users()}
    for username in db.due_users():
        if username in active:
            try:
                backend.revoke(username)
                db.audit("user_expired", username)
            except BackendError as exc:
                db.audit("expire_failed", username, str(exc))
                continue
        db.delete_user(username)


if __name__ == "__main__":
    run()
