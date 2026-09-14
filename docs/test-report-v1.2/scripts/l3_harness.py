"""L3/L4 orchestration harness for the v1.2 test round.

Manages the host process plus the two mock clients, drives the REST/CLI control
surface, and collects artifacts.  Deliberately uses file-redirected stdio (never
pipes) because the execution sandbox cannot open named pipes.

Not a project deliverable: throwaway test scaffolding under tmp/.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(r"E:\Work\ZT\card_simulator_host")
EV = REPO / "tmp" / "v12test" / "evidence"
SCRATCH = REPO / "tmp" / "v12test" / "scratch"
PROCDIR = REPO / "tmp" / "v12test" / "proc"
ASSET = Path(r"E:\Work\ZT\KS8226\L2-SELFTEST-APP\app\src\main\assets\emv\params")

WEB_PORT = 8000
DEV_PORT = 9100


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Proc:
    """One child process with its combined stdout/stderr redirected to a file."""

    def __init__(self, label: str, args: list[str], log_path: Path,
                 cwd: Path = REPO, extra_env: dict | None = None):
        self.label = label
        self.args = args
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.log_path, "wb")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        if extra_env:
            env.update(extra_env)
        flags = 0
        if os.name == "nt":
            # own process group so CTRL_BREAK_EVENT reaches only this child
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
        self.started = time.time()
        self.popen = subprocess.Popen(
            args, cwd=str(cwd), stdout=self.fh, stderr=subprocess.STDOUT,
            env=env, creationflags=flags)
        self.write(f"### [{now()}] launched: {' '.join(args)} (cwd={cwd}) pid={self.popen.pid}")

    @property
    def pid(self) -> int:
        return self.popen.pid

    def alive(self) -> bool:
        return self.popen.poll() is None

    def write(self, line: str) -> None:
        if getattr(self, "_stopped", False):
            return
        try:
            self.fh.write((line + "\n").encode("utf-8"))
            self.fh.flush()
        except ValueError:
            pass

    def text(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def stop(self, grace: float = 6.0) -> str:
        """Graceful CTRL_BREAK first (so the host runs its cleanup), then kill.

        Idempotent: calling stop() on an already-stopped process is a no-op, so
        scenarios may stop one mock early and still call stop_all() later.
        """
        if getattr(self, "_stopped", False):
            return "already-stopped"
        self._stopped = True
        how = "already-exited"
        if self.alive():
            how = "ctrl_break"
            try:
                if os.name == "nt":
                    os.kill(self.popen.pid, signal.CTRL_BREAK_EVENT)
                else:
                    self.popen.send_signal(signal.SIGINT)
            except (OSError, ValueError):
                how = "ctrl_break-failed"
            try:
                self.popen.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                how += "+terminate"
                self.popen.terminate()
                try:
                    self.popen.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    how += "+kill"
                    self.popen.kill()
                    self.popen.wait(timeout=4)
        self.write(f"### [{now()}] stopped via {how}; exitcode={self.popen.returncode}")
        try:
            self.fh.close()
        except OSError:
            pass
        return how


def _python_procs() -> list[dict]:
    """Enumerate python processes as [{pid, ppid, cmd}] via one PowerShell call."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "ForEach-Object { \"{0}`t{1}`t{2}\" -f $_.ProcessId, "
          "$_.ParentProcessId, $_.CommandLine }")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=90)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in (r.stdout or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[0].strip().isdigit():
            out.append({"pid": int(parts[0]), "ppid": int(parts[1]), "cmd": parts[2]})
    return out


#: cmdline markers identifying processes this harness started
_MY_MARKERS = (r"tmp\v12test\proc", r"mock_clients\mock_",
               r"tmp\v12test\scratch\raw_pos")


def cleanup_stragglers() -> list[dict]:
    """Kill orphaned host/mock processes left by an earlier interrupted run.

    Runs before each scenario so a single crash can never wedge the rest of the
    round (this is exactly what happened when an exception aborted stop_all()).
    Only processes that carry this harness's own cmdline markers AND whose
    parent is gone (or is us) are touched -- never another tool's python.
    """
    me = os.getpid()
    procs = _python_procs()
    live = {p["pid"] for p in procs}
    killed = []
    for p in procs:
        cmd = p["cmd"] or ""
        if p["pid"] == me:
            continue
        if not any(m in cmd for m in _MY_MARKERS):
            continue
        if "emv_host" in cmd and " serve" not in cmd:
            continue
        if p["ppid"] in live and p["ppid"] != me:
            continue  # still owned by a live parent -> not an orphan
        try:
            os.kill(p["pid"], signal.SIGTERM)
        except OSError:
            continue
        killed.append({"pid": p["pid"], "cmd": cmd[:120]})
    if killed:
        time.sleep(1.5)
    return killed


def port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_port(host: str, port: int, timeout: float = 25.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if port_open(host, port):
            return True
        time.sleep(0.25)
    return False


def api(path: str, method: str = "GET", payload: dict | None = None,
        port: int = WEB_PORT, timeout: float = 10.0) -> tuple[int, object]:
    """Return (http_status, parsed_body_or_error_text)."""
    url = f"http://127.0.0.1:{port}{path}"
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)


class Harness:
    """Host + android mock + POS mock for one scenario."""

    def __init__(self, scenario: str, runs_root: Path | None = None,
                 cases_root: Path | None = None, serve_extra: list[str] | None = None,
                 web_port: int = WEB_PORT, dev_port: int = DEV_PORT):
        self.scenario = scenario
        self.runs_root = Path(runs_root) if runs_root else REPO / "runs"
        self.cases_root = Path(cases_root) if cases_root else REPO / "cases"
        self.serve_extra = serve_extra or []
        self.web_port = web_port
        self.dev_port = dev_port
        self.procs: list[Proc] = []
        self.host: Proc | None = None
        self.android: Proc | None = None
        self.pos: Proc | None = None
        self.notes: list[str] = []
        self.checks: list[dict] = []
        self.run_id: str | None = None
        self.started_at = now()
        # never let a previous crash wedge this scenario
        strays = cleanup_stragglers()
        if strays:
            self.notes.append(f"cleaned up {len(strays)} straggler process(es) "
                              f"before start: {strays}")
            print(f"[{scenario}] cleaned stragglers: {strays}", flush=True)

    def note(self, msg: str) -> None:
        line = f"[{now()}] {msg}"
        self.notes.append(line)
        print(line, flush=True)

    # -- assertions ---------------------------------------------------------
    def ck(self, name: str, expected, actual, ok: bool | None = None) -> bool:
        if ok is None:
            ok = (expected == actual)
        self.checks.append({"name": name, "expected": expected,
                            "actual": actual, "ok": bool(ok)})
        print(f"  [{'ok ' if ok else 'FAIL'}] {name}: expected={expected!r} "
              f"actual={actual!r}", flush=True)
        return bool(ok)

    def sub(self, name: str, haystack: str, needle: str, want: bool = True,
            shown: str | None = None) -> bool:
        """Assert a literal substring is (not) present in captured text."""
        present = needle in haystack
        detail = shown if shown is not None else f"{'present' if present else 'absent'}: {needle!r}"
        shown_actual = f"{detail} (count={haystack.count(needle)})"
        return self.ck(name, f"present={want}", shown_actual,
                       ok=(present == want))

    def count(self, haystack: str, needle: str) -> int:
        return haystack.count(needle)

    def settle(self, seconds: float, why: str = "") -> None:
        """Wait for deferred side effects (e.g. session_abort fan-out)."""
        self.note(f"settle {seconds}s {why}".strip())
        time.sleep(seconds)

    def ok(self) -> bool:
        return all(c["ok"] for c in self.checks)

    # -- process control ----------------------------------------------------
    def start_host(self) -> Proc:
        args = [sys.executable, "-m", "emv_host", "serve",
                "--port", str(self.web_port), "--device-port", str(self.dev_port),
                "--cases", str(self.cases_root), "--runs", str(self.runs_root),
                "--log-dir", str(PROCDIR / f"{self.scenario}_logs")]
        args += self.serve_extra
        self.host = Proc("host", args, PROCDIR / f"{self.scenario}_host.log")
        self.procs.append(self.host)
        if not wait_port("127.0.0.1", self.web_port, 30):
            self.note("HOST FAILED TO START (web port never opened)")
        if not wait_port("127.0.0.1", self.dev_port, 10):
            self.note("device TCP port never opened")
        return self.host

    def start_android(self, tap_delay: float = 1.0, extra: list[str] | None = None,
                      script: Path | None = None, env: dict | None = None) -> Proc:
        args = [sys.executable,
                str(script or (REPO / "tools" / "mock_clients" / "mock_android.py")),
                "--port", str(self.dev_port), "--tap-delay", str(tap_delay)]
        args += extra or []
        self.android = Proc("android", args, PROCDIR / f"{self.scenario}_android.log",
                            extra_env=env)
        self.procs.append(self.android)
        return self.android

    def start_pos(self, outcome: str = "approved_offline", delay: float = 2.0,
                  interactive: bool = False, extra: list[str] | None = None,
                  script: Path | None = None, env: dict | None = None) -> Proc:
        args = [sys.executable,
                str(script or (REPO / "tools" / "mock_clients" / "mock_pos.py")),
                "--port", str(self.dev_port), "--outcome", outcome,
                "--delay", str(delay)]
        if interactive:
            args.append("--interactive")
        args += extra or []
        self.pos = Proc("pos", args, PROCDIR / f"{self.scenario}_pos.log",
                        extra_env=env)
        self.procs.append(self.pos)
        return self.pos

    def start_script(self, label: str, args: list[str],
                     env: dict | None = None) -> Proc:
        """Start an arbitrary helper script as a managed background child."""
        p = Proc(label, args, PROCDIR / f"{self.scenario}_{label}.log",
                 extra_env=env)
        self.procs.append(p)
        return p

    def wait_connected(self, timeout: float = 25.0, want=("android", "pos")) -> dict:
        end = time.time() + timeout
        last = {}
        while time.time() < end:
            st, body = api("/api/health", port=self.web_port)
            if st == 200 and isinstance(body, dict):
                last = body.get("devices", {})
                if all(last.get(k, {}).get("connected") for k in want):
                    return last
            time.sleep(0.4)
        self.note(f"wait_connected TIMEOUT; last devices={json.dumps(last, ensure_ascii=False)}")
        return last

    def wait_disconnected(self, which: str, timeout: float = 40.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            st, body = api("/api/health", port=self.web_port)
            if st == 200 and isinstance(body, dict):
                if not body.get("devices", {}).get(which, {}).get("connected"):
                    return True
            time.sleep(0.4)
        return False

    # -- run control --------------------------------------------------------
    def start_run_cli(self, scheme: str, mode: str, cases: list[str]) -> tuple[int, str]:
        args = [sys.executable, "-m", "emv_host", "run",
                "--scheme", scheme, "--mode", mode]
        for c in cases:
            args += ["--case", c]
        p = subprocess.run(args, cwd=str(REPO), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        out = (p.stdout or "") + (p.stderr or "")
        m = re.search(r"run started:\s*(\S+)", out)
        if m:
            self.run_id = m.group(1)
        return p.returncode, out.strip()

    def api_run(self, payload: dict) -> tuple[int, object]:
        st, body = api("/api/run", "POST", payload, port=self.web_port)
        if st == 200 and isinstance(body, dict):
            self.run_id = body.get("run_id")
        return st, body

    def cli(self, *args: str, timeout: float = 30.0) -> tuple[int, str]:
        p = subprocess.run([sys.executable, "-m", "emv_host", *args], cwd=str(REPO),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()

    def run_script(self, args: list[str], timeout: float = 120.0,
                   env: dict | None = None) -> tuple[int, str]:
        e = dict(os.environ)
        e["PYTHONIOENCODING"] = "utf-8"
        if env:
            e.update(env)
        p = subprocess.run(args, cwd=str(REPO), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           env=e)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    def wait_dir(self, path: Path, timeout: float = 20.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if Path(path).is_dir():
                return True
            time.sleep(0.1)
        return False

    def run_dir(self, run_id: str | None = None) -> Path | None:
        rid = run_id or self.run_id
        if not rid:
            return None
        hits = sorted(self.runs_root.glob(f"*/{rid}"))
        return hits[0] if hits else None

    def read_json(self, rel: str, run_id: str | None = None):
        d = self.run_dir(run_id)
        if d is None:
            return None
        p = d / rel
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def wait_run_done(self, timeout: float = 120.0, run_id: str | None = None) -> dict | None:
        """Poll run.json until status leaves PENDING/RUNNING."""
        end = time.time() + timeout
        last = None
        while time.time() < end:
            last = self.read_json("run.json", run_id)
            if isinstance(last, dict) and last.get("status") not in ("PENDING", "RUNNING"):
                return last
            time.sleep(0.5)
        self.note(f"wait_run_done TIMEOUT after {timeout}s; last status="
                  f"{(last or {}).get('status')}")
        return last

    def wait_run_status(self, want: str, timeout: float = 60.0,
                        run_id: str | None = None) -> dict | None:
        end = time.time() + timeout
        last = None
        while time.time() < end:
            last = self.read_json("run.json", run_id)
            if isinstance(last, dict) and last.get("status") == want:
                return last
            time.sleep(0.4)
        return last

    def host_log(self) -> str:
        return self.host.text() if self.host else ""

    def android_log(self) -> str:
        return self.android.text() if self.android else ""

    def pos_log(self) -> str:
        return self.pos.text() if self.pos else ""

    def stop_all(self) -> None:
        """Stop every managed child; one failure must never skip the rest.

        (An earlier version let an exception from an already-stopped Proc escape
        this loop, which leaked the host process and wedged the following runs.)
        """
        errors = []
        for p in (self.pos, self.android, self.host):
            if p is None:
                continue
            try:
                p.stop()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{p.label}: {type(exc).__name__}: {exc}")
        self.procs = [p for p in self.procs if p.alive()]
        for other in list(self.procs):
            try:
                other.stop()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{other.label}: {type(exc).__name__}: {exc}")
        self.procs = []
        if errors:
            print(f"[stop_all] errors (suppressed): {errors}", flush=True)

    # -- evidence -----------------------------------------------------------
    def dump(self, extra: dict) -> Path:
        try:
            self.stop_all()
        finally:
            pass
        blob = {
            "scenario": self.scenario,
            "started_at": self.started_at,
            "finished_at": now(),
            "run_id": self.run_id,
            "runs_root": str(self.runs_root),
            "cases_root": str(self.cases_root),
            "serve_extra": self.serve_extra,
            "overall_ok": self.ok(),
            "checks": self.checks,
            "failed_checks": [c["name"] for c in self.checks if not c["ok"]],
            "notes": self.notes,
            "extra": extra,
            "logs": {
                "host": self.host_log(),
                "android": self.android_log(),
                "pos": self.pos_log(),
            },
        }
        out = EV / f"L3_{self.scenario}.json"
        out.write_text(json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{self.scenario}] overall_ok={blob['overall_ok']} "
              f"failed={blob['failed_checks']} -> {out}", flush=True)
        return out
