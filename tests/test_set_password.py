from __future__ import annotations

import importlib.util
from pathlib import Path


def load_script():
    path = Path(__file__).parents[1] / "deploy" / "set-password.py"
    spec = importlib.util.spec_from_file_location("hyboard_set_password", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_password_hash_is_replaced_atomically(tmp_path, monkeypatch):
    script = load_script()
    monkeypatch.setattr(script.os, "chown", lambda *_args: None, raising=False)
    env = tmp_path / "hyboard.env"
    env.write_text(
        "HYBOARD_BACKEND=helper\nHYBOARD_ADMIN_PASSWORD_HASH=old\nHYBOARD_PORT=28474\n",
        encoding="utf-8",
    )
    script.update_env(env, "$argon2id$new")
    assert env.read_text(encoding="utf-8") == (
        "HYBOARD_BACKEND=helper\n"
        "HYBOARD_ADMIN_PASSWORD_HASH=$argon2id$new\n"
        "HYBOARD_PORT=28474\n"
    )


def test_windows_line_ending_is_not_part_of_password():
    assert "secret-from-powershell\r\n".rstrip("\r\n") == "secret-from-powershell"
