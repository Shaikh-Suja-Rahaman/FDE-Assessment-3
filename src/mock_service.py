"""Start the mock vendor-risk API in-process when it is not already running.

Used by evaluation scripts so results never depend on remembering to start
`run_local.py` first (otherwise every vendor would look "unavailable").
"""
from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlparse

import requests


def is_up(base_url: str) -> bool:
    try:
        return requests.get(base_url.rstrip("/") + "/health", timeout=1).ok
    except requests.RequestException:
        return False


def ensure_running(base_url: str | None = None, timeout_seconds: float = 10.0) -> bool:
    base_url = base_url or os.getenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:8001")
    if is_up(base_url):
        return True
    parsed = urlparse(base_url)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        return False

    import uvicorn
    from mock_api.app import app

    server = uvicorn.Server(uvicorn.Config(app, host=parsed.hostname, port=parsed.port or 80, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if is_up(base_url):
            return True
        time.sleep(0.2)
    return False
