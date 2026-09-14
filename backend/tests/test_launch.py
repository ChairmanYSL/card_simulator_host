"""Launch-path tests: repo-root shim, CWD-independent defaults, lock hygiene.

Covers the reported regressions:
* ``python -m emv_host`` from the repository root must work without install;
* ``python -m emv_host serve`` from ``backend/`` must resolve default paths
  against the repository root (not the CWD);
* a stale lock from a dead pid must self-heal instead of locking the host out;
* a startup failure must never leave the lock file behind.
"""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from emv_host.appconfig import REPO_ROOT, acquire_lock, pid_alive
from emv_host.cli import serve


def _free_ports(n: int = 2) -> list[int]:
    socks = [socket.socket() for _ in range(n)]
    try:
        for sock in socks:
            sock.bind(("127.0.0.1", 0))
        ports = [sock.getsockname()[1] for sock in socks]
    finally:
        for sock in socks:
            sock.close()
    assert len(set(ports)) == n
    return ports


def _clean_env() -> dict:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def _serve_args(web: int, device: int, tmp_path, cases=None):
    import argparse

    return argparse.Namespace(
        host="127.0.0.1", port=web, device_host="127.0.0.1",
        device_port=device,
        cases=cases if cases is not None else str(tmp_path / "missing-cases"),
        runs=str(tmp_path / "runs"), tokens=str(REPO_ROOT / "tokens.json"),
        static=str(REPO_ROOT / "frontend/dist"),
        log_level="error", log_dir=str(tmp_path / "logs"),
    )


def test_pid_alive_true_for_self_false_for_dead_pid():
    """v1.2 lock hygiene: the Windows branch must read GetLastError with
    ``use_last_error=True`` — otherwise a live pid that this process may not
    query (ERROR_ACCESS_DENIED) is misread as dead and its lock is stolen."""
    assert pid_alive(os.getpid()) is True
    assert pid_alive(999_999_999) is False
    assert pid_alive(0) is False
    assert pid_alive(-1) is False


def test_ack_timeout_cli_flags_and_defaults():
    """v1.2: card/pos ack budgets are configurable, 15s by default."""
    from emv_host.appconfig import AppConfig
    from emv_host.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["serve"])
    assert args.card_ack_timeout == 15.0
    assert args.pos_ack_timeout == 15.0

    args = parser.parse_args(["serve", "--card-ack-timeout", "25.5",
                              "--pos-ack-timeout", "8"])
    config = AppConfig.from_args(args)
    assert config.card_ack_timeout == 25.5
    assert config.pos_ack_timeout == 8.0
    # stop/abort acks stay at the best-effort 3s default
    assert config.stop_ack_timeout == 3.0
    assert config.abort_ack_timeout == 3.0


def test_v12_hardcoded_protocol_defaults():
    """device_config is gone: heartbeat/warn/ack defaults live in the host
    config and on the devices, and the negotiated level is 1.2."""
    from emv_host.appconfig import AppConfig
    from emv_host.protocol.messages import PROTOCOL_VERSION_STRING

    config = AppConfig()
    assert PROTOCOL_VERSION_STRING == "1.2"
    assert config.heartbeat_interval == 10.0
    assert config.heartbeat_warn == 30.0
    assert config.card_ack_timeout == 15.0


def test_root_shim_help_works_from_repo_root():
    """`python -m emv_host --help` from the repo root must print help and exit
    (the shim package forwards to backend/emv_host without an install)."""
    proc = subprocess.run(
        [sys.executable, "-m", "emv_host", "--help"],
        cwd=str(REPO_ROOT), env=_clean_env(),
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "usage" in proc.stdout and "--device-port" in proc.stdout
    assert "Uvicorn running" not in proc.stdout  # never started the server


def test_serve_from_backend_dir_uses_repo_defaults(tmp_path):
    """Starting from `backend/` without --cases/--tokens/--static must anchor
    those defaults to the repository root and serve normally."""
    if os.name != "nt":
        pytest.skip("CTRL_BREAK_EVENT delivery is Windows-specific")
    web, device = _free_ports(2)
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(
        [sys.executable, "-m", "emv_host", "serve",
         "--port", str(web), "--device-port", str(device),
         "--runs", str(tmp_path / "runs"),
         "--log-dir", str(tmp_path / "logs")],
        cwd=str(REPO_ROOT / "backend"), env=_clean_env(),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags,
    )
    try:
        for _ in range(120):
            if proc.poll() is not None:
                out = proc.stdout.read().decode(errors="replace")
                pytest.fail(f"server died early (exit {proc.returncode}):\n{out[-2000:]}")
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{web}/api/health", timeout=1):
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        else:
            proc.kill()
            pytest.fail("server did not start (default path resolution broken?)")

        os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        out, _ = proc.communicate(timeout=20)
        text = out.decode(errors="replace")
        assert proc.returncode == 0, f"exit {proc.returncode}:\n{text[-2000:]}"
        assert "Traceback" not in text
    finally:
        if proc.poll() is None:
            proc.kill()


def test_stale_lock_from_dead_pid_self_heals(tmp_path):
    runs = tmp_path / "runs"
    lock = runs / ".host.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)

    # a dead pid (way out of range) must not lock the host out
    lock.write_text(json.dumps({"pid": 999_999_999, "ts": time.time() - 3600}),
                    encoding="utf-8")
    acquired = acquire_lock(str(runs))
    assert acquired == lock and lock.is_file()

    # a live pid (this process) must be rejected as a running instance
    with pytest.raises(SystemExit) as excinfo:
        acquire_lock(str(runs))
    assert "正在运行" in str(excinfo.value)


def test_startup_failure_removes_lock(tmp_path):
    """When load_library fails the server must print a concise error (no
    traceback) and must not leave the lock file behind."""
    args = _serve_args(*_free_ports(2), tmp_path)  # cases = missing dir
    with pytest.raises(SystemExit) as excinfo:
        asyncio.run(serve(args))
    assert excinfo.value.code == 1
    assert not (tmp_path / "runs" / ".host.lock").exists()
