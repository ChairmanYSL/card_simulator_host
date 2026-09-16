"""CLI: `python -m emv_host serve` plus thin control client subcommands.

Control subcommands talk to the running server's REST API (urllib only, no
extra deps), keeping CLI and web UI on the same command channel.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__
from .appconfig import AppConfig, acquire_lock, load_tokens
from .logging_setup import setup_logging

logger = logging.getLogger("emv_host.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="card_simulator_host",
        description="EMV auto-test host: TCP device hub + web UI. "
                    "Run it with no arguments to start the server with "
                    "default settings (web 127.0.0.1:8000, device TCP :9100).")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the host server (default)")
    serve.add_argument("--version", action="version", version=__version__)
    serve.add_argument("--host", default="127.0.0.1", help="web bind host")
    serve.add_argument("--port", type=int, default=8000, help="web port (default 8000)")
    serve.add_argument("--device-host", default="0.0.0.0",
                       help="device TCP bind host (android/POS dial this)")
    serve.add_argument("--device-port", type=int, default=9100,
                       help="device TCP port (default 9100)")
    serve.add_argument("--cases", default=None,
                       help="case library root (default: <repo>/cases)")
    serve.add_argument("--runs", default=None,
                       help="run artifacts root (default: <repo>/runs)")
    serve.add_argument("--tokens", default=None,
                       help="client tokens JSON (default: <repo>/tokens.json)")
    serve.add_argument("--static", default=None,
                       help="built frontend directory (default: <repo>/frontend/dist)")
    serve.add_argument("--log-level", default="info", choices=[
        "debug", "info", "warning", "error"])
    serve.add_argument("--log-dir", default=None,
                       help="log directory (default: <repo>/logs)")
    serve.add_argument("--trace-enabled", action="store_true",
                       help="enable the Android APDU trace channel (Phase A)")
    serve.add_argument("--card-ack-timeout", type=float, default=15.0,
                       help="seconds to wait for a card-side ack (default 15)")
    serve.add_argument("--pos-ack-timeout", type=float, default=15.0,
                       help="seconds to wait for a POS-side ack (default 15)")
    serve.add_argument("--kernel-params-dir", default=None,
                       help="out-of-repo directory holding per-brand POS kernel "
                            "parameter sets (<brand>.json); it carries CAPK "
                            "moduli, so it must never live inside the repository")

    for name, help_text in (
        ("status", "show server health and current run"),
        ("list", "list the case library tree"),
        ("run", "start a run"),
        ("stop", "stop after the current case"),
        ("abort", "abort the current case immediately"),
        ("skip", "skip the case currently waiting for a POS result"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--port", type=int, default=8000, help="server web port")
        if name == "run":
            cmd.add_argument("--brand", required=True)
            cmd.add_argument("--mode", required=True)
            cmd.add_argument("--all", action="store_true",
                             help="run the whole brand/mode")
            cmd.add_argument("--case", action="append", default=[],
                             help="case id (repeatable; kind=selected)")
            cmd.add_argument("--resume", action="store_true",
                             help="resume from the latest run of this brand/mode")
    return parser


def _api(port: int, path: str, method: str = "GET",
         payload: dict | None = None) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", str(exc))
        except Exception:  # noqa: BLE001
            detail = str(exc)
        raise SystemExit(f"server said {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach server at 127.0.0.1:{port}: {exc}") from exc


def client_command(args: argparse.Namespace) -> None:
    if args.command == "status":
        print(json.dumps(_api(args.port, "/api/health"), indent=2))
    elif args.command == "list":
        tree = _api(args.port, "/api/tree")
        for brand in tree["brands"]:
            print(f"{brand['brand']:<14} {brand['display_name']} "
                  f"({brand['case_count']} cases)")
            for mode in brand["entry_modes"]:
                print(f"  {mode['mode']:<12} {mode['case_count']} cases")
                for case in mode["cases"]:
                    print(f"    {case['case_id']:<24} {case['title']}")
        for issue in tree["issues"]:
            print(f"  [{issue['severity']}] {issue['path']}: {issue['message']}")
    elif args.command == "run":
        kind = "resume" if args.resume else \
            ("full" if args.all else ("single" if len(args.case) == 1
                                      else "selected"))
        result = _api(args.port, "/api/run", "POST", {
            "brand": args.brand, "mode": args.mode, "kind": kind,
            "case_ids": args.case})
        print(f"run started: {result['run_id']}")
    elif args.command in ("stop", "abort", "skip"):
        result = _api(args.port, f"/api/run/{args.command}", "POST", {})
        print(result.get("message", "ok"))


async def serve(args: argparse.Namespace) -> None:
    from .cases import CaseLibraryError, load_library
    from .protocol.server import ProtocolServer
    from .runner import RunExecutor
    from .store.run_store import RunStore
    from .web import AppContext, EventBus, create_app

    import uvicorn

    config = AppConfig.from_args(args)
    setup_logging(config.log_dir, config.log_level)

    # Own, harmless signal handling:
    # * uvicorn's capture_signals() installs its handler while serving and
    #   *re-raises the captured signal* after graceful shutdown.  With our own
    #   flag-only handler installed here, uvicorn restores *ours* before the
    #   re-raise, so the re-raised signal just sets a flag instead of letting
    #   asyncio.run cancel the main task and exit via a KeyboardInterrupt
    #   traceback (Python 3.14 turns that into a red stack trace + non-zero
    #   exit code).
    # * pressing Ctrl+C during startup (before uvicorn is running) also just
    #   sets the flag; the watcher below turns it into a graceful exit.
    interrupted = threading.Event()

    def _flag_signal(signum, frame):  # noqa: ARG001
        interrupted.set()

    previous_handlers: dict[int, object] = {}
    for sig in (signal.SIGINT, signal.SIGTERM,
                getattr(signal, "SIGBREAK", None)):
        if sig is None:
            continue
        try:
            previous_handlers[sig] = signal.signal(sig, _flag_signal)
        except (OSError, ValueError):  # pragma: no cover - non-main thread etc.
            pass

    # The whole body below is one try/finally so the single-instance lock is
    # removed on *every* exit path (normal, Ctrl+C, startup failure).
    lock: Path | None = None
    watcher: asyncio.Task | None = None
    executor: RunExecutor | None = None
    protocol: ProtocolServer | None = None
    bus: EventBus | None = None
    served = False
    try:
        lock = acquire_lock(config.runs_dir)
        tokens = load_tokens(config.tokens_path)

        try:
            library = load_library(config.cases_dir,
                                   kernel_params_dir=config.kernel_params_dir)
        except CaseLibraryError as exc:
            logger.error("启动失败：%s", exc)
            raise SystemExit(1) from exc
        for issue in library.issues:
            (logger.error if issue.severity == "error" else logger.warning)(
                "%s: %s", issue.path, issue.message)

        store = RunStore(config.runs_dir)
        recovered = store.recover_interrupted_runs()
        if recovered:
            logger.warning("recovered interrupted runs: %s", recovered)

        bus = EventBus()

        async def sink(event: str, data: dict) -> None:
            await bus.publish(event, data)

        protocol = ProtocolServer(
            tokens, sink,
            heartbeat_interval=config.heartbeat_interval,
            heartbeat_warn=config.heartbeat_warn,
            disconnect_timeout=config.disconnect_timeout,
        )
        executor = RunExecutor(library, protocol, store, sink,
                           trace_enabled=config.trace_enabled,
                           card_ack_timeout=config.card_ack_timeout,
                           pos_ack_timeout=config.pos_ack_timeout,
                           stop_ack_timeout=config.stop_ack_timeout,
                           abort_ack_timeout=config.abort_ack_timeout)
        protocol.on_event = executor.on_device_event
        await protocol.start(config.device_host, config.device_port)

        context = AppContext(library=library, executor=executor, store=store,
                             bus=bus, static_dir=config.static_dir)
        app = create_app(context)

        uvicorn_config = uvicorn.Config(
            app, host=config.host, port=config.port,
            log_level=config.log_level.lower(),
            # bounded graceful shutdown: never hang on lingering connections
            timeout_graceful_shutdown=3,
        )
        server = uvicorn.Server(uvicorn_config)
        logger.info("EMV test host v%s starting: web http://%s:%s, device TCP "
                    "%s:%s, cases=%s", __version__, config.host, config.port,
                    config.device_host, config.device_port, config.cases_dir)

        # When uvicorn flips should_exit (first Ctrl+C) - or our flag fires
        # early - wake every WebSocket subscriber so their endpoint tasks
        # finish and graceful shutdown completes immediately.
        async def _watch_exit() -> None:
            while not server.should_exit and not interrupted.is_set():
                await asyncio.sleep(0.1)
            if interrupted.is_set() and not server.should_exit:
                server.should_exit = True
            await bus.shutdown()

        watcher = asyncio.create_task(_watch_exit())
        served = True
        await server.serve()
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Belt & braces: any remaining interrupt/cancel path is a clean exit.
        logger.info("ctrl+c received, shutting down")
    finally:
        if watcher is not None:
            watcher.cancel()
            try:
                await watcher
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if executor is not None:
            await executor.shutdown()
        if protocol is not None:
            await protocol.stop()
        if bus is not None:
            await bus.shutdown()
        # restore whatever signal handlers were active before serve()
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except (OSError, ValueError):  # pragma: no cover
                pass
        if lock is not None:
            try:
                lock.unlink(missing_ok=True)
            except OSError:  # pragma: no cover
                pass
        if served:
            logger.info("bye")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    # `None` means "read the real command line" (setuptools console scripts and
    # `python -m emv_host` call main() without arguments).
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    # No subcommand -> default to `serve` so `card_simulator_host --port 8123`
    # and even plain `card_simulator_host` start the server with defaults,
    # while `--help`/`--version` still print instead of starting anything.
    if not argv or argv[0] not in ("serve", "status", "list", "run", "stop",
                                   "abort", "skip"):
        argv = ["serve"] + argv
    args = parser.parse_args(argv)
    if args.command == "serve":
        try:
            asyncio.run(serve(args))
        except KeyboardInterrupt:
            # ultimate safety net: Ctrl+C must never exit via a traceback
            return
    else:
        client_command(args)


if __name__ == "__main__":
    main()
