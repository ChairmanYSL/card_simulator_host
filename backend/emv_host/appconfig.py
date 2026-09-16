"""Application configuration and tokens."""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("emv_host.config")

# Repository root = backend/emv_host/appconfig.py -> parents[2]
REPO_ROOT = Path(__file__).resolve().parents[2]

# default (relative) locations, anchored to the repository root
_DEFAULT_DIRS = {
    "cases_dir": "cases",
    "runs_dir": "runs",
    "tokens_path": "tokens.json",
    "static_dir": "frontend/dist",
    "log_dir": "logs",
}


def resolve_path(value: str | None, default: str) -> str:
    """Path resolution rules:
    * ``None``            -> ``<repo_root>/<default>`` (CLI default);
    * absolute path       -> used as-is;
    * relative path       -> resolved against the current working directory
                             (standard CLI semantics for explicit values).
    """
    if value is None:
        return str(REPO_ROOT / default)
    p = Path(value)
    return str(p if p.is_absolute() else Path.cwd() / p)


@dataclass
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    device_host: str = "0.0.0.0"
    device_port: int = 9100
    cases_dir: str | None = None
    runs_dir: str | None = None
    tokens_path: str | None = None
    static_dir: str | None = None
    log_level: str = "info"
    log_dir: str | None = None
    trace_enabled: bool = False
    #: C3: out-of-repo directory holding one ``<brand>.json`` POS kernel
    #: parameter set per brand.  It lives outside the repository because those
    #: files carry CAPK moduli (key material).  None = load none.
    kernel_params_dir: str | None = None
    heartbeat_interval: float = 10.0
    heartbeat_warn: float = 30.0
    disconnect_timeout: float = 60.0
    # v1.2 ack budgets.  Both devices use the same hardcoded 15s default now
    # that device_config is gone; these knobs only relax/tighten the host side
    # when a device is known to be slow.  stop/abort acks are best-effort
    # (3s, warn-only) so a dead peer cannot stall run finalisation.
    card_ack_timeout: float = 15.0
    pos_ack_timeout: float = 15.0
    stop_ack_timeout: float = 3.0
    abort_ack_timeout: float = 3.0

    def __post_init__(self) -> None:
        for attr, default in _DEFAULT_DIRS.items():
            setattr(self, attr, resolve_path(getattr(self, attr), default))
        # explicit external directory: relative values resolve against the CWD;
        # nothing is ever defaulted into the repository
        if self.kernel_params_dir:
            self.kernel_params_dir = resolve_path(self.kernel_params_dir, "")

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "AppConfig":
        return cls(
            host=args.host,
            port=args.port,
            device_host=args.device_host,
            device_port=args.device_port,
            cases_dir=args.cases,
            runs_dir=args.runs,
            tokens_path=args.tokens,
            static_dir=args.static,
            log_level=args.log_level,
            log_dir=args.log_dir,
            trace_enabled=getattr(args, "trace_enabled", False),
            kernel_params_dir=getattr(args, "kernel_params_dir", None),
            card_ack_timeout=getattr(args, "card_ack_timeout", 15.0),
            pos_ack_timeout=getattr(args, "pos_ack_timeout", 15.0),
        )


def load_tokens(path: str | Path) -> dict[str, str]:
    path = Path(path)
    if not path.is_file():
        default = {"android": "dev-android-token-change-me",
                   "pos": "dev-pos-token-change-me"}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(default, indent=2), encoding="utf-8")
        logger.warning("tokens file not found, created %s with dev tokens - "
                       "change them before real use", path)
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"tokens file {path} is not valid JSON: {exc}") from exc
    for key in ("android", "pos"):
        if key not in data or not isinstance(data[key], str) or not data[key]:
            raise SystemExit(f"tokens file {path} must contain a non-empty "
                             f"string for {key!r}")
    return {k: str(v) for k, v in data.items() if k in ("android", "pos")}


def pid_alive(pid: int) -> bool:
    """True when a process with ``pid`` exists (best effort).

    On Windows ``OpenProcess`` can also fail because the process exists but the
    caller may not query it (``ERROR_ACCESS_DENIED``, 5) — that still means the
    pid is taken, so it must count as alive.  Without ``use_last_error=True``
    there is no reliable way to read the Win32 error after the call.
    """
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: win32
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERROR_ACCESS_DENIED = 5
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                      False, pid)
        if not handle:
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED
        kernel32.CloseHandle(handle)
        return True
    try:  # pragma: posix
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def acquire_lock(runs_dir: str | Path) -> Path:
    """Single-instance lock inside the runs dir.

    A lock whose recorded pid is dead (crashed host) or whose content is
    unreadable is treated as stale and silently replaced, so a previous
    crash never leaves the host unstartable (no ghost-pid lockouts).
    """
    import time

    lock = Path(runs_dir) / ".host.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.is_file():
        try:
            payload = json.loads(lock.read_text(encoding="utf-8"))
            pid = int(payload.get("pid", -1))
            if pid_alive(pid):
                raise SystemExit(
                    f"另一个 host 实例正在运行 (pid {pid})；"
                    f"若确认没有实例，请删除 {lock}")
            logger.warning("removing stale lock from dead pid %s: %s",
                           pid if pid > 0 else "?", lock)
            lock.unlink(missing_ok=True)
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning("unreadable lock file, replacing: %s", lock)
            lock.unlink(missing_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}),
                    encoding="utf-8")
    return lock
