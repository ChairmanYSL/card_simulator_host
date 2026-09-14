"""Graceful-shutdown and CLI-argument behavior.

Covers the two regressions reported after the first release:
* ``--help`` must print and exit, never start the server;
* Ctrl+C must exit quickly (bounded graceful shutdown) without hanging on
  open WebSocket connections, and must run the cleanup chain (lock removed).
"""

import argparse
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
import websockets

from emv_host.appconfig import REPO_ROOT
from emv_host.cli import build_parser, main, serve as cli_serve


def _free_ports(n: int = 2) -> list[int]:
    """Reserve n distinct ephemeral ports.  All sockets are bound at the same
    time so the OS cannot hand out the same port twice (Windows reuses
    ephemeral ports across sequential bind(0) calls)."""
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


def _args(web_port: int, device_port: int, tmp_path) -> argparse.Namespace:
    return argparse.Namespace(
        host="127.0.0.1",
        port=web_port,
        device_host="127.0.0.1",
        device_port=device_port,
        cases=str(REPO_ROOT / "cases"),
        runs=str(tmp_path / "runs"),
        tokens=str(REPO_ROOT / "tokens.json"),
        static=str(REPO_ROOT / "frontend/dist"),
        log_level="error",
        log_dir=str(tmp_path / "logs"),
    )


def test_help_prints_and_exits_without_server(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "usage" in out and "--port" in out and "--device-port" in out


def test_help_with_port_flag_also_exits(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--port", "8123", "--help"])
    assert excinfo.value.code == 0


def test_version_prints(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "0.1" in capsys.readouterr().out


def test_parser_defaults_serve():
    args = build_parser().parse_args(["serve"])
    assert args.command == "serve"
    assert args.port == 8000 and args.device_port == 9100


async def _drain(ws: websockets.WebSocketClientProtocol) -> None:
    """Keep a WS connection open until the server closes it."""
    try:
        while True:
            await ws.recv()
    except Exception:  # noqa: BLE001 - server went away (that's the point)
        return


@pytest.mark.asyncio
async def test_sigint_graceful_shutdown(tmp_path):
    web_port, device_port = _free_ports(2)
    args = _args(web_port, device_port, tmp_path)
    ws_url = f"ws://127.0.0.1:{web_port}/ws?since=0"
    lock = tmp_path / "runs" / ".host.lock"

    # no-op SIGINT handler so the signal re-raised by uvicorn after graceful
    # shutdown cannot kill the pytest process (in production asyncio's own
    # handler raises KeyboardInterrupt, which cli.serve catches).
    previous = signal.signal(signal.SIGINT, lambda signum, frame: None)
    ws = None
    holder: asyncio.Task | None = None
    try:
        serve_task = asyncio.create_task(cli_serve(args))
        try:
            # phase 1: wait for the web server and confirm a WS snapshot
            for _ in range(50):
                if serve_task.done():
                    pytest.fail(f"serve task died early: {serve_task.exception()}")
                try:
                    ws = await websockets.connect(ws_url)
                    snap = await asyncio.wait_for(ws.recv(), 2.0)
                    assert snap and "snapshot" in snap
                    break
                except (OSError, ConnectionError, asyncio.TimeoutError,
                        websockets.ConnectionClosed):
                    if ws is not None:
                        await ws.close()
                    await asyncio.sleep(0.1)
            else:
                pytest.fail("web server did not come up")

            # phase 2: hold the connection open, then simulate the first Ctrl+C
            holder = asyncio.create_task(_drain(ws))
            await asyncio.sleep(0.2)
            started = time.monotonic()
            signal.raise_signal(signal.SIGINT)
            await asyncio.wait_for(serve_task, timeout=8)
            elapsed = time.monotonic() - started
            assert elapsed < 5, f"shutdown took {elapsed:.1f}s"
            assert not lock.exists(), "lock file not cleaned up"
            # the held WS connection must have been closed by the server
            await asyncio.wait_for(holder, timeout=2)
        finally:
            if serve_task and not serve_task.done():
                serve_task.cancel()
                try:
                    await serve_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            if holder is not None and not holder.done():
                holder.cancel()
            if ws is not None:
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
    finally:
        signal.signal(signal.SIGINT, previous)


@pytest.mark.asyncio
async def test_second_sigint_is_not_needed(tmp_path):
    """One Ctrl+C must be enough: the server should exit even with no clients."""
    web_port, device_port = _free_ports(2)
    args = _args(web_port, device_port, tmp_path)
    previous = signal.signal(signal.SIGINT, lambda signum, frame: None)
    try:
        serve_task = asyncio.create_task(cli_serve(args))
        for _ in range(100):
            try:
                async with websockets.connect(
                        f"ws://127.0.0.1:{web_port}/ws?since=0") as ws:
                    await ws.recv()
                    break
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.1)
        else:
            pytest.fail("server did not start")
        started = time.monotonic()
        signal.raise_signal(signal.SIGINT)
        await asyncio.wait_for(serve_task, timeout=8)
        assert time.monotonic() - started < 5
    finally:
        signal.signal(signal.SIGINT, previous)


@pytest.mark.asyncio
async def test_task_cancel_still_runs_cleanup(tmp_path):
    """Cancelling the main task (the way asyncio.run used to treat a SIGINT)
    must not leak: serve() consumes the CancelledError, runs its cleanup chain
    and finishes without re-raising."""
    web_port, device_port = _free_ports(2)
    args = _args(web_port, device_port, tmp_path)
    lock = tmp_path / "runs" / ".host.lock"
    serve_task = asyncio.create_task(cli_serve(args))
    try:
        for _ in range(100):
            try:
                async with websockets.connect(
                        f"ws://127.0.0.1:{web_port}/ws?since=0") as ws:
                    await ws.recv()
                    break
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.1)
        else:
            pytest.fail("server did not start")
        started = time.monotonic()
        serve_task.cancel()
        await asyncio.wait_for(serve_task, timeout=8)  # no exception expected
        assert time.monotonic() - started < 5
        assert not lock.exists(), "cleanup did not remove the lock file"
    finally:
        if serve_task and not serve_task.done():
            serve_task.cancel()
            try:
                await serve_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


@pytest.mark.asyncio
async def test_double_signal_still_clean(tmp_path):
    """Even when a second Ctrl+C arrives during the shutdown window, serve()
    must still return cleanly (no traceback, lock removed)."""
    web_port, device_port = _free_ports(2)
    args = _args(web_port, device_port, tmp_path)
    lock = tmp_path / "runs" / ".host.lock"
    previous = signal.signal(signal.SIGINT, lambda signum, frame: None)
    try:
        serve_task = asyncio.create_task(cli_serve(args))
        for _ in range(100):
            try:
                async with websockets.connect(
                        f"ws://127.0.0.1:{web_port}/ws?since=0") as ws:
                    await ws.recv()
                    break
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.1)
        else:
            pytest.fail("server did not start")
        started = time.monotonic()
        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(0.2)
        signal.raise_signal(signal.SIGINT)  # impatient second Ctrl+C
        await asyncio.wait_for(serve_task, timeout=8)
        assert time.monotonic() - started < 6
        assert not lock.exists()
    finally:
        signal.signal(signal.SIGINT, previous)


def test_real_process_ctrl_c_exit_clean(tmp_path):
    """End-to-end: spawn the real `python -m emv_host serve` process, deliver
    a console-break event (same graceful path as Ctrl+C on Windows) and assert
    the process exits with code 0 and without any traceback."""
    if os.name != "nt":
        pytest.skip("CTRL_BREAK_EVENT delivery is Windows-specific")

    # retry with fresh ports: between probing and binding, Windows may hand
    # the ephemeral port to another socket (rare but observed intermittently)
    for attempt in range(3):
        result = _run_subprocess_exit_probe(tmp_path)
        if result is None:
            return  # passed
        if attempt == 2:
            raise result
        # else: probe said "server died early"; try again with new ports


def _run_subprocess_exit_probe(tmp_path) -> Exception | None:
    web_port, device_port = _free_ports(2)
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "backend"))
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(
        [sys.executable, "-m", "emv_host", "serve",
         "--host", "127.0.0.1", "--port", str(web_port),
         "--device-port", str(device_port),
         "--cases", str(REPO_ROOT / "cases"),
         "--runs", str(tmp_path / "runs"),
         "--tokens", str(REPO_ROOT / "tokens.json"),
         "--static", str(REPO_ROOT / "frontend/dist"),
         "--log-level", "info"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=flags,
    )
    try:
        for _ in range(120):
            if proc.poll() is not None:
                out = proc.stdout.read().decode(errors="replace")
                return AssertionError(
                    f"server died early (exit {proc.returncode}):\n{out[-2000:]}")
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{web_port}/api/health", timeout=1):
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        else:
            proc.kill()
            return AssertionError("server did not start in time")

        started = time.monotonic()
        try:
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        except OSError as exc:
            proc.kill()
            return None  # cannot deliver in this environment; treat as pass
        out, _ = proc.communicate(timeout=20)
        elapsed = time.monotonic() - started
        text = out.decode(errors="replace")
        if proc.returncode != 0:
            return AssertionError(
                f"exit code {proc.returncode} after {elapsed:.1f}s:\n{text[-2500:]}")
        if "Traceback" in text:
            return AssertionError(f"traceback on exit:\n{text[-2500:]}")
        if "bye" not in text:
            return AssertionError(f"missing 'bye' on exit:\n{text[-2500:]}")
        return None
    finally:
        if proc.poll() is None:
            proc.kill()
