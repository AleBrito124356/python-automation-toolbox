"""Shared fixtures for the offline test-suite.

The scripts have names like 04_folder_backup.py, which are not importable
module names, so they are loaded by path. Set TOOLBOX_SCRIPTS_DIR to point
the whole suite at another checkout (for example a worktree of an older
commit, to prove a regression test fails there).

Nothing here touches the network, the clipboard, the screen or the
registry: those are replaced by fakes, and every socket connection to a
non-loopback address raises (in-process and in subprocesses).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parent
SCRIPTS_DIR = Path(os.environ.get("TOOLBOX_SCRIPTS_DIR", REPO / "scripts")).resolve()
FIXTURES = TESTS_DIR / "fixtures"
OFFLINE_DIR = TESTS_DIR / "_offline"

_guard_spec = importlib.util.spec_from_file_location(
    "toolbox_offline_guard", OFFLINE_DIR / "sitecustomize.py")
offline = importlib.util.module_from_spec(_guard_spec)
_guard_spec.loader.exec_module(offline)   # same guard the subprocesses get; installs itself


def script_path(stem: str) -> Path:
    matches = sorted(SCRIPTS_DIR.glob(f"{stem}*.py"))
    if not matches:
        raise FileNotFoundError(f"no script matching {stem!r} in {SCRIPTS_DIR}")
    return matches[0]


def load_script(stem: str):
    """Import scripts/<stem>*.py as a fresh module object."""
    path = script_path(stem)
    name = "toolbox_" + path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def offline_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONIOENCODING", "PYTHONUTF8", "UPTIME_WEBHOOK_URL")}
    env["PYTHONPATH"] = str(OFFLINE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


def run_script(stem: str, *args: str, cwd: Path | None = None, stdin: str | None = None,
               env: dict[str, str] | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
    """Run a script in a real subprocess (offline guard active)."""
    return subprocess.run(
        [sys.executable, str(script_path(stem)), *map(str, args)],
        cwd=cwd, input=stdin if stdin is not None else "", capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        env=env or offline_env(), timeout=timeout,
    )


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """In-process guard: only loopback sockets are allowed during tests."""
    monkeypatch.setattr(offline.socket.socket, "connect", offline._connect)
    monkeypatch.setattr(offline.socket.socket, "connect_ex", offline._connect_ex)
    monkeypatch.setattr(offline.socket, "getaddrinfo", offline._getaddrinfo)
    monkeypatch.delenv("UPTIME_WEBHOOK_URL", raising=False)


@pytest.fixture
def run(tmp_path):
    def _run(stem: str, *args: str, **kwargs):
        kwargs.setdefault("cwd", tmp_path)
        return run_script(stem, *args, **kwargs)
    return _run


@pytest.fixture
def fixture_json():
    def _load(name: str):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return _load


class FakeSite:
    """Mutable behaviour for the local test server."""

    def __init__(self) -> None:
        self.status = 200
        self.body = "<html><body>all systems operational</body></html>"
        self.delay = 0.0
        self.hook_status = 200
        self.hook_posts: list[dict] = []
        self.lock = threading.Lock()


@pytest.fixture
def http_server():
    """ThreadingHTTPServer on 127.0.0.1 with /site (toggleable) and /hook (records POSTs)."""
    state = FakeSite()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep test output clean
            pass

        def do_GET(self):
            if self.path.startswith("/site"):
                if state.delay:
                    threading.Event().wait(state.delay)
                body = state.body.encode("utf-8")
                self.send_response(state.status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            with state.lock:
                state.hook_posts.append(json.loads(raw or b"{}"))
            self.send_response(state.hook_status)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
