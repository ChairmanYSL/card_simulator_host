"""Drive the EMV Test Host REST API for joint-debug runs.

Usage examples::

    # one case
    python tools/joint_debug/run_cases.py --scheme visa --case VISA-CL-004

    # every scheme, all cases, never stop on failure
    python tools/joint_debug/run_cases.py --all --on-failure continue --retry 0

The script only uses the REST API (no imports from backend), waits for the run
to reach a terminal status, then prints per-case results and writes a JSON
summary under logs/joint_debug/.

While the runs execute it also captures `adb logcat` for both devices
(`--pos-serial` / `--card-serial`, disable with `--no-logcat`) into
`logs/joint_debug/logcat/<stamp>-{pos,card}.log`, so every attempt keeps its
kernel evidence even after the device ring buffer rotates.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TERMINAL = {"FINISHED", "STOPPED", "ABORTED", "INTERRUPTED"}
#: 已知长耗时用例（N12 静默让 POS 内核等满自身超时）：放到每个 scheme 的队尾，
#: 避免它们迟到收尾时把下一个 scheme 的首例饿死（run #14 的 MC-CL-001 就是这样
#: 被判 INCONCLUSIVE 的）。
SLOW_CASES = {"UPI-CL-006", "UPI-CL-007"}


def order_cases(case_ids: list[str]) -> list[str]:
    """Stable order with the known slow cases moved to the end."""
    return ([c for c in case_ids if c not in SLOW_CASES]
            + [c for c in case_ids if c in SLOW_CASES])
DEFAULT_ADB = r"E:\Android\Sdk\platform-tools\adb.exe"
DEFAULT_POS_SERIAL = "256068b7"
DEFAULT_CARD_SERIAL = "463169e6"


class LogcatCapture:
    """Start `adb logcat` for the given serials, stop it on exit."""

    def __init__(self, adb: str, serials: dict[str, str], out_dir: Path, stamp: str):
        self.adb = adb
        self.serials = serials
        self.out_dir = out_dir
        self.stamp = stamp
        self.procs: dict[str, tuple[subprocess.Popen, object]] = {}

    def _online(self, serial: str) -> bool:
        try:
            out = subprocess.run([self.adb, "devices"], capture_output=True,
                                 text=True, timeout=15).stdout
        except Exception:
            return False
        return any(line.split()[:2] == [serial, "device"] for line in out.splitlines())

    def start(self) -> dict[str, str]:
        if not Path(self.adb).exists():
            print(f"  logcat: adb not found at {self.adb}, capture disabled")
            return {}
        self.out_dir.mkdir(parents=True, exist_ok=True)
        started = {}
        for role, serial in self.serials.items():
            if not serial or not self._online(serial):
                print(f"  logcat[{role}]: serial {serial!r} not online, skipped")
                continue
            path = self.out_dir / f"{self.stamp}-{role}.log"
            handle = open(path, "wb")
            proc = subprocess.Popen(
                [self.adb, "-s", serial, "logcat", "-v", "threadtime"],
                stdout=handle, stderr=subprocess.STDOUT)
            self.procs[role] = (proc, handle)
            started[role] = str(path)
            print(f"  logcat[{role}]: capturing {serial} -> {path}")
        return started

    def stop(self) -> None:
        for role, (proc, handle) in self.procs.items():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            handle.close()
            print(f"  logcat[{role}]: stopped")


def parse_serials(value: str | None) -> dict[str, str]:
    """Parse `role=serial[,role=serial]`."""
    if not value:
        return {"pos": DEFAULT_POS_SERIAL, "card": DEFAULT_CARD_SERIAL}
    result: dict[str, str] = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        role, _, serial = item.partition("=")
        result[role.strip()] = serial.strip()
    return result


def api(base: str, path: str, method: str = "GET", payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def start(base: str, scheme: str, mode: str, kind: str, case_ids: list[str],
          on_failure: str, retry: int) -> str:
    body = {
        "scheme": scheme, "mode": mode, "kind": kind, "case_ids": case_ids,
        "on_failure": on_failure, "retry_count": retry,
    }
    result = api(base, "/api/run", "POST", body)
    return result["run_id"]


def wait_terminal(base: str, run_id: str, timeout: float) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        snap = api(base, "/api/run")
        session = snap.get("session")
        if session is None:
            raise SystemExit("run vanished from server snapshot")
        if session.get("run_id") != run_id:
            # Our run already finished and another run took the single slot.
            detail = api(base, f"/api/history/{run_id}")
            other = detail.get("run", {})
            if other.get("status") in TERMINAL:
                print(f"  [{time.strftime('%H:%M:%S')}] run={run_id} "
                      f"finished ({other.get('status')}); another run is active",
                      flush=True)
                return other
            time.sleep(1.5)
            continue
        state = (session.get("run_status"), session.get("current", {}).get("state"),
                 session.get("current", {}).get("case_id"))
        if state != last:
            print(f"  [{time.strftime('%H:%M:%S')}] run={run_id} "
                  f"status={state[0]} case={state[2]} state={state[1]}",
                  flush=True)
            last = state
        if session.get("run_status") in TERMINAL:
            return session
        time.sleep(1.5)
    raise SystemExit(f"run {run_id} did not finish within {timeout}s")


def report(base: str, run_id: str) -> dict:
    detail = api(base, f"/api/history/{run_id}")
    run = detail["run"]
    print(f"\n=== run {run_id} status={run.get('status')} "
          f"reason={run.get('abort_reason')} ===")
    rows = []
    for case_id in run.get("queue", []):
        record = detail.get("results", {}).get(case_id, {})
        status = record.get("status", "?")
        note = record.get("note", "")
        # v1.2 T20: the case-level failure reason is the actionable field for a
        # FAIL/ERROR (result_timeout / pos_not_connected / assertion_failed / ...)
        reason = record.get("failure_reason") or ""
        verdict = record.get("verdict")
        detail_lines = []
        if isinstance(verdict, dict):
            for check in verdict.get("rules", verdict.get("checks", [])):
                if check.get("ok"):
                    continue
                detail_lines.append(
                    f"{check.get('path')} {check.get('op')} "
                    f"expect={check.get('expect')!r} actual={check.get('actual')!r}")
        print(f"  {case_id:<20} {status:<14} {reason:<28} {note}")
        for line in detail_lines:
            print(f"      - {line}")
        rows.append({"case_id": case_id, "status": status,
                     "failure_reason": reason, "note": note,
                     "failed_checks": detail_lines})
    return {"run": run, "cases": rows}


def parse_versions(value: str | None) -> dict[str, str]:
    """Parse `role=version[,role=version]`（与 parse_serials 同构）。"""
    if not value:
        return {}
    result: dict[str, str] = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        role, _, version = item.partition("=")
        result[role.strip()] = version.strip()
    return result


def preflight(base: str, expect: dict[str, str]) -> bool:
    """跑前核对：两端必须 connected；若给了期望版本则必须一致。

    单方就绪不开跑、版本不符不开跑（避免整轮空跑与"跑错版本"的归因污染）。
    """
    try:
        health = api(base, "/api/health")
    except Exception as exc:  # noqa: BLE001 - 网络异常一律拒绝开跑
        print(f"preflight: 无法访问 host {base}: {exc}")
        return False
    devices = health.get("devices") or {}
    ok = True
    for role in ("android", "pos"):
        info = devices.get(role) or {}
        state = "connected" if info.get("connected") else "DISCONNECTED"
        version = info.get("app_version") or "-"
        line = f"preflight: {role:7s} {state:12s} app_version={version}"
        want = expect.get(role) or expect.get(
            "card" if role == "android" else "pos")
        if want and version != want:
            line += f"  ← 期望 {want}（不一致）"
            ok = False
        if not info.get("connected"):
            ok = False
        print(line)
    if health.get("busy"):
        print("preflight: host 已有 run 在跑（busy=true）→ 拒绝开跑")
        ok = False
    # v1.2: /api/health does NOT expose protocol_version — it is negotiated in
    # the handshake.  Confirm 1.2 from the host log line
    # `handshake ok: ... protocol=1.2` (or the device's own log) before a run;
    # a wrong version is rejected at handshake, so a device that shows
    # connected=true has already passed the gate.
    for role in ("android", "pos"):
        info = devices.get(role) or {}
        if info.get("connected"):
            print(f"preflight: {role:7s} handshake 版本请以 host 日志 "
                  f"'protocol=1.2' 为准（/api/health 不返回该字段）")
    if not ok:
        print("preflight: 未通过 → 不开跑（等两端 READY 后重试）")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--scheme")
    parser.add_argument("--mode", default="contactless")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--all", action="store_true",
                        help="run every scheme/mode full")
    parser.add_argument("--on-failure", default="stop",
                        choices=["stop", "continue"],
                        help="v1.2: the host always stops the run on the first "
                             "FAIL, so 'continue' is ignored (kept for "
                             "compatibility with v1.1 invocations)")
    parser.add_argument("--retry", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--settle", type=float, default=15.0,
                        help="每个 scheme 之间的静默等待秒数（等 POS 收尾上一笔）")
    parser.add_argument("--out", default=None)
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--serials", default=None,
                        help="role=serial pairs, default pos=256068b7,card=463169e6")
    parser.add_argument("--no-logcat", action="store_true",
                        help="disable per-device logcat capture")
    parser.add_argument("--expect-versions", default=None,
                        help="role=version pairs, e.g. pos=1.0.13,card=1.0.31；"
                             "开跑前核对，不一致则拒绝开跑")
    parser.add_argument("--no-preflight", action="store_true",
                        help="跳过开跑前的双端在线/版本核对")
    args = parser.parse_args()

    if not args.no_preflight:
        if not preflight(args.base, parse_versions(args.expect_versions)):
            return 2

    if args.all:
        tree = api(args.base, "/api/tree")
        targets = [(s["scheme"], m["mode"])
                   for s in tree["schemes"] for m in s["entry_modes"]]
    elif args.scheme:
        targets = [(args.scheme, args.mode)]
    else:
        parser.error("need --scheme or --all")

    stamp = time.strftime("%Y%m%dT%H%M%S")
    out_path = Path(args.out) if args.out else (
        REPO / "logs" / "joint_debug" / f"run_{stamp}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    capture = None
    if not args.no_logcat:
        capture = LogcatCapture(args.adb, parse_serials(args.serials),
                                REPO / "logs" / "joint_debug" / "logcat", stamp)
        capture.start()

    summaries = []
    try:
        for scheme, mode in targets:
            kind = "single" if len(args.case) == 1 else (
                "selected" if args.case else "full")
            case_ids = list(args.case)
            if kind == "full":
                # resolve ids via the tree so the run is deterministic
                tree = api(args.base, "/api/tree")
                node = next(s for s in tree["schemes"] if s["scheme"] == scheme)
                mode_node = next(m for m in node["entry_modes"] if m["mode"] == mode)
                case_ids = [c["case_id"] for c in mode_node["cases"]]
            case_ids = order_cases(case_ids)
            print(f"\n### {scheme}/{mode} kind={kind} cases={len(case_ids)}",
                  flush=True)
            run_id = None
            for attempt in range(24):
                try:
                    run_id = start(args.base, scheme, mode, kind, case_ids,
                                   args.on_failure, args.retry)
                    break
                except urllib.error.HTTPError as exc:
                    detail = exc.read().decode("utf-8", "replace")
                    if exc.code == 409 and attempt < 23:
                        print(f"  start busy (409), waiting for the other run to "
                              f"finish: {detail}", flush=True)
                        time.sleep(5)
                        continue
                    print(f"  start rejected: {exc.code} {detail}")
                    summaries.append({"scheme": scheme, "mode": mode,
                                      "error": f"{exc.code}"})
                    break
            if run_id is None:
                continue
            wait_terminal(args.base, run_id, args.timeout)
            summaries.append(report(args.base, run_id))
            # Let the POS finish any in-flight transaction before the next
            # scheme starts: a case that timed out on the host side may still
            # be running on the POS, and the POS then ignores the next
            # case_started ("上一笔交易尚未结束"), which shows up as
            # INCONCLUSIVE on the following case.
            if args.settle > 0:
                print(f"  settle {args.settle}s before the next scheme", flush=True)
                time.sleep(args.settle)
    finally:
        if capture is not None:
            capture.stop()

    out_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\nsummary written: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
