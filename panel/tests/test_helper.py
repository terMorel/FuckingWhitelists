from __future__ import annotations

import importlib.util
from pathlib import Path


def load_helper():
    path = Path(__file__).parents[1] / "deploy" / "hyboard-helper.py"
    spec = importlib.util.spec_from_file_location("hyboard_helper", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_udp_443_socket_detection():
    helper = load_helper()
    output = """UNCONN 0 0 0.0.0.0:443 0.0.0.0:*\nUNCONN 0 0 [::]:8443 [::]:*\n"""
    assert helper.has_udp_443(output) is True
    assert helper.has_udp_443("UNCONN 0 0 [::]:8443 [::]:*") is False
