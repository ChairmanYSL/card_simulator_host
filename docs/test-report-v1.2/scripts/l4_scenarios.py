"""L4 (plan section 5) fault-injection scenarios.

Usage:
    python l4_scenarios.py <name> [<name> ...]
    python l4_scenarios.py all
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, r"E:\Work\ZT\card_simulator_host\backend")

import cases_factory as cf  # noqa: E402
from l3_harness import EV, REPO, SCRATCH, Harness, api  # noqa: E402

TOKENS = json.loads((REPO / "tokens.json").read_text(encoding="utf-8"))
INJECTOR = SCRATCH / "inject_badframe.py"
RAW_POS_DUP = SCRATCH / "raw_pos_dup.py"
RAW_POS_RETRY = SCRATCH / "raw_pos_retry.py"
CASES = cf.ROOT

SCENARIOS: dict = {}


def scenario(fn):
    SCENARIOS[fn.__name__] = fn
    return fn


def wait_for_marker(h: Harness, marker: str, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if marker in h.host_log():
            return True
        time.sleep(0.3)
    return False


# ------------------------------------------------------------------- 5.1
@scenario
def L4_1_bad_frames() -> Harness:
    """Section 5.1: 4 bad frames tolerated, the 5th drops the connection."""
    h = Harness("L4_1_bad_frames")
    h.start_host()
    rc, out = h.run_script([sys.executable, str(INJECTOR), str(h.dev_port),
                            TOKENS["android"], "injector"], timeout=90)
    h.ck("injector exit code", 0, rc)
    m = re.search(r"SUMMARY (\{.*\})", out)
    summary = json.loads(m.group(1)) if m else {}
    h.ck("injector handshake acked", "handshake_ack", summary.get("handshake"))
    h.ck("injector sent 5 bad frames", 5, summary.get("sent_bad"))
    h.ck("injector sent 4 good frames", 4, summary.get("sent_good"))
    h.ck("host dropped the connection on the 5th", True,
         summary.get("connection_dropped"))

    h.settle(2, "for host log flush")
    hl = h.host_log()
    for n in (1, 2, 3, 4):
        h.sub(f"host: skipping bad frame ({n}/5)", hl,
              f"skipping bad frame ({n}/5)")
    h.sub("host: 5 bad frames, dropping", hl, "bad frames, dropping")
    for i in range(4):
        h.sub(f"host: good frame alive{i} still processed", hl,
              f"[android] alive{i}")

    st, _ = api("/api/health")
    h.ck("host still healthy after injection", 200, st)
    h.ck("host process never died", True, h.host.alive())
    h.dump({"injector_summary": summary})
    return h


# ------------------------------------------------------------------- 5.2
@scenario
def L4_2_duplicate_result() -> Harness:
    """Section 5.2 (D2 dedup), using a client that can actually read acks."""
    h = Harness("L4_2_duplicate_result", serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    client = h.start_script("rawposdup", [
        sys.executable, str(RAW_POS_DUP), "--port", str(h.dev_port),
        "--token", TOKENS["pos"], "--device-id", "rawpos",
        "--resend-delay", "0.5", "--second-delay", "0", "--lifetime", "30",
        "--single-case"])
    h.wait_connected()
    rc, out = h.start_run_cli("visa", "contactless", ["VISA-CL-001"])
    client.popen.wait(timeout=60)
    run = h.wait_run_done(timeout=60) or {}
    h.settle(2, "for host log flush")

    m = re.search(r"SUMMARY (\{.*\})", client.text())
    s = json.loads(m.group(1)) if m else {}
    h.ck("first result ack detail", "persisted", s.get("first_detail"))
    h.ck("resend (same msg_id) ack detail", "duplicate", s.get("resend_detail"))
    hl = h.host_log()
    h.sub("host: duplicate ignored", hl, "duplicate transaction_result ignored")
    rd = h.run_dir()
    res = h.read_json("results/VISA-CL-001.json") or {}
    h.ck("VISA-CL-001 persisted once as PASS", "PASS", res.get("status"))
    h.ck("quarantine.jsonl must NOT exist", False,
         bool(rd and (rd / "quarantine.jsonl").exists()))
    h.ck("run.status", "FINISHED", run.get("status"))
    h.dump({"client_summary": s, "run_status": run.get("status")})
    return h


@scenario
def L4_2b_late_resend_is_quarantined() -> Harness:
    """Boundary of the D2 dedup window (recorded deviation from section 5.2).

    Two cases: the resend of case 1's frame is sent 1s later, by which time case
    2 has begun and begin_attempt() has cleared judged_msg_ids.  The resend is
    therefore NOT deduplicated; it is quarantined with reason "case_id mismatch"
    and quarantine.jsonl IS written, whereas section 5.2 states quarantine.jsonl
    must not exist.  Asserts the actual behaviour and records the deviation.
    """
    h = Harness("L4_2b_late_resend_is_quarantined", serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    client = h.start_script("rawposdup", [
        sys.executable, str(RAW_POS_DUP), "--port", str(h.dev_port),
        "--token", TOKENS["pos"], "--device-id", "rawpos",
        "--resend-delay", "1.0", "--second-delay", "8.0", "--lifetime", "40"])
    h.wait_connected()
    rc, out = h.start_run_cli("visa", "contactless",
                              ["VISA-CL-001", "VISA-CL-002"])
    client.popen.wait(timeout=60)
    h.wait_run_done(timeout=60)
    h.settle(2, "for host log flush")

    m = re.search(r"SUMMARY (\{.*\})", client.text())
    s = json.loads(m.group(1)) if m else {}
    h.ck("first result persisted", "persisted", s.get("first_detail"))
    h.ck("late resend is quarantined, not deduplicated", "quarantined",
         s.get("resend_detail"))
    hl = h.host_log()
    h.sub("host: late resend quarantined on case_id mismatch", hl,
          "quarantined transaction result (case_id mismatch)")
    h.sub("host: no duplicate log line", hl,
          "duplicate transaction_result ignored", want=False)
    rd = h.run_dir()
    h.ck("quarantine.jsonl IS written on this path", True,
         bool(rd and (rd / "quarantine.jsonl").exists()))
    h.note("DEVIATION FROM PLAN 5.2: dedup is per-attempt "
           "(Session.begin_attempt resets judged_msg_ids), so a resend arriving "
           "after the next case begins is quarantined and quarantine.jsonl is "
           "created, not the 'duplicate, no quarantine.jsonl' the plan states")
    h.dump({"client_summary": s})
    return h


# ------------------------------------------------------------------ 5.3a
@scenario
def L4_3a_persist_failed_then_timeout() -> Harness:
    """Section 5.3: persist failure -> ok=false/persist_failed, run keeps waiting.

    Injection method: the plan suggests `attrib +R` on the results dir, but on
    Windows a read-only *directory* attribute does not prevent creating files
    inside it.  We instead place a DIRECTORY where results/<case>.json must go,
    which makes RunStore._atomic_write_json's os.replace fail.  The collision is
    removed once the failure is observed so the subsequent timeout record can be
    written normally.
    """
    root = cf.fresh("timeout15_persist")
    cf.patch_case(root, cf.VISA_001, timeout_seconds=15)
    h = Harness("L4_3a_persist_failed_then_timeout", cases_root=root,
                serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    h.start_pos(outcome="approved_offline", delay=6.0)
    h.wait_connected()
    rc, out = h.start_run_cli("visa", "contactless", ["VISA-CL-001"])
    rd = None
    for _ in range(50):
        rd = h.run_dir()
        if rd is not None and (rd / "results").is_dir():
            break
        time.sleep(0.2)
    results = rd / "results"
    blocker = results / "VISA-CL-001.json"
    blocker.mkdir(parents=True, exist_ok=True)
    h.ck("blocking directory created before the result arrives", True,
         blocker.is_dir())
    found = wait_for_marker(h, "结果落盘失败", timeout=20)
    h.ck("host reported the persist failure", True, found)
    blocker.rmdir()
    run = h.wait_run_done(timeout=90) or {}
    res = h.read_json("results/VISA-CL-001.json") or {}
    hl = h.host_log()
    h.sub("host: 结果落盘失败，未 ack 成功", hl, "结果落盘失败，未 ack 成功")
    h.sub("host: waiter NOT resolved on failed persist", hl,
          "transaction result received for VISA-CL-001", want=False)
    h.ck("case eventually judged", "FAIL", res.get("status"))
    h.ck("failure_reason", "result_timeout", res.get("failure_reason"))
    h.ck("run.status", "ABORTED", run.get("status"))
    h.dump({"run_status": run.get("status"), "result": res,
            "injection": "directory collision at results/VISA-CL-001.json",
            "plan_method": "attrib +R on the results dir (ineffective on Windows)"})
    return h


# ------------------------------------------------------------------ 5.3b
@scenario
def L4_3b_persist_failed_then_retransmit_passes() -> Harness:
    """Section 5.3 second half: after unblocking, a new-msg_id retry PASSes."""
    h = Harness("L4_3b_persist_failed_then_retransmit_passes",
                serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    # the retry client IS the POS here: it must hold the attempt_id across both
    # attempts, which a restarted mock cannot do (it would never see case_started)
    retry = h.start_script("rawposretry", [
        sys.executable, str(RAW_POS_RETRY), "--port", str(h.dev_port),
        "--token", TOKENS["pos"], "--device-id", "rawretry",
        "--initial-delay", "5", "--retry-delay", "6",
        "--max-attempts", "4", "--lifetime", "70"])
    h.wait_connected()
    rc, out = h.start_run_cli("visa", "contactless", ["VISA-CL-001"])
    rd = None
    for _ in range(50):
        rd = h.run_dir()
        if rd is not None and (rd / "results").is_dir():
            break
        time.sleep(0.2)
    blocker = rd / "results" / "VISA-CL-001.json"
    blocker.mkdir(parents=True, exist_ok=True)
    found = wait_for_marker(h, "结果落盘失败", timeout=25)
    h.ck("host reported the persist failure", True, found)
    h.ck("run still waiting (not judged)", True,
         (h.read_json("run.json") or {}).get("status") == "RUNNING",
         ok=((h.read_json("run.json") or {}).get("status") == "RUNNING"))
    # unblock; the retry client resends the same logical result with a NEW msg_id
    blocker.rmdir()
    run = h.wait_run_done(timeout=90) or {}
    try:
        retry.popen.wait(timeout=30)
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"SUMMARY (\{.*\})", retry.text())
    rs = json.loads(m.group(1)) if m else {}
    attempts = rs.get("attempts") or []
    h.ck("retry client made at least 2 attempts", True, len(attempts) >= 2,
         ok=(len(attempts) >= 2))
    h.ck("first attempt was persist_failed",
         "persist_failed",
         attempts[0].get("detail") if attempts else None)
    h.ck("a later attempt was persisted",
         "persisted",
         next((a.get("detail") for a in attempts
               if a.get("detail") == "persisted"), None))
    res = h.read_json("results/VISA-CL-001.json") or {}
    h.ck("retry persisted as PASS", "PASS", res.get("status"))
    h.ck("run.status", "FINISHED", run.get("status"))
    h.dump({"run_status": run.get("status"), "result": res,
            "retry_attempts": attempts})
    return h


# ------------------------------------------------------------------ 5.4
def _disconnect_case(name: str, victim: str) -> Harness:
    h = Harness(name, serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    h.start_pos(outcome="approved_offline", delay=9999)
    h.wait_connected()
    rc, out = h.start_run_cli("visa", "contactless", ["VISA-CL-001"])
    in_wait = False
    end = time.time() + 40
    while time.time() < end:
        st, snap = api("/api/run")
        sess = (snap or {}).get("session") if isinstance(snap, dict) else None
        if sess and sess.get("current", {}).get("state") == "WAIT_RESULT":
            in_wait = True
            break
        time.sleep(0.3)
    h.ck("session reached WAIT_RESULT before the cut", True, in_wait)
    proc = h.android if victim == "android" else h.pos
    proc.stop()
    h.note(f"cut the {victim} mock")
    run = h.wait_run_done(timeout=90) or {}
    res = h.read_json("results/VISA-CL-001.json") or {}
    h.ck("case status", "FAIL", res.get("status"))
    h.ck("failure_reason", f"{victim}_disconnected", res.get("failure_reason"))
    h.ck("run.status", "ABORTED", run.get("status"))
    h.ck("run.abort_reason",
         f"case_failed:VISA-CL-001:{victim}_disconnected", run.get("abort_reason"))
    h.dump({"run_status": run.get("status"), "result": res, "victim": victim})
    return h


@scenario
def L4_4a_android_disconnect() -> Harness:
    """Section 5.4: card-side disconnect mid-case."""
    return _disconnect_case("L4_4a_android_disconnect", "android")


@scenario
def L4_4b_pos_disconnect() -> Harness:
    """Section 5.4: POS-side disconnect mid-case (v1.1 only warned)."""
    return _disconnect_case("L4_4b_pos_disconnect", "pos")


# ------------------------------------------------------------------- 5.5
@scenario
def L4_5_loader_tolerance() -> Harness:
    """Section 5.5: a broken file must not abort the scan; duplicate ids flagged."""
    from emv_host.cases import load_library

    h = Harness("L4_5_loader_tolerance")
    base = cf.fresh("badlib")
    mode_dir = base / "visa" / "contactless" / "cases"
    (mode_dir / "BROKEN-001.json").write_text("{ not json", encoding="utf-8")

    # duplicate id: standalone file vs an identical id inside a group dir
    standalone = json.loads((mode_dir / "VISA-CL-001.json").read_text(encoding="utf-8"))
    standalone["title"] = "STANDALONE COPY WINS"
    (mode_dir / "VISA-CL-001.json").write_text(
        json.dumps(standalone, ensure_ascii=False, indent=2), encoding="utf-8")
    group_dir = mode_dir / "OPBD-900"
    group_dir.mkdir(parents=True, exist_ok=True)
    (group_dir / "group.json").write_text(json.dumps({
        "group_id": "OPBD-900", "title": "dup probe", "summary": "dup probe",
        "brand": "visa", "entry_mode": "contactless",
        "test_plan": [{"step": 1, "action": "a", "expect": "b"}],
        "pass_criteria": ["x"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    group_copy = dict(standalone)
    group_copy["title"] = "GROUP COPY LOSES"
    (group_dir / "VISA-CL-001.json").write_text(
        json.dumps(group_copy, ensure_ascii=False, indent=2), encoding="utf-8")

    raised = None
    lib = None
    try:
        lib = load_library(str(base))
    except Exception as exc:  # noqa: BLE001
        raised = f"{type(exc).__name__}: {exc}"
    h.ck("load_library did not raise", None, raised)
    if lib is not None:
        ids = lib.all_case_ids("visa", "contactless")
        errors = [i.message for i in lib.issues if i.severity == "error"]
        # the offending file is named in LoadIssue.path, not necessarily in the
        # message text -- search both, like a human reading the issue list
        blob = "\n".join(f"{i.path}: {i.message}" for i in lib.issues
                         if i.severity == "error")
        h.ck("VISA-CL-001 still listed", True, "VISA-CL-001" in ids)
        h.sub("BROKEN-001 recorded as an error", blob, "BROKEN-001", want=True)
        h.sub("duplicate case_id is an error", blob, "重复", want=True)
        case = lib.get_case("visa", "contactless", "VISA-CL-001")
        kept = case.title if case is not None else None
        h.ck("standalone copy kept (not the group copy)",
             "STANDALONE COPY WINS", kept)
        h.dump({"ids": ids, "errors": errors,
                "error_paths": [i.path for i in lib.issues
                                if i.severity == "error"],
                "kept_title": kept})
    else:
        h.dump({"raised": raised})
    return h


# ------------------------------------------------------------------- 5.6
@scenario
def L4_6_atomic_no_tmp() -> Harness:
    """Section 5.6: no *.tmp residue anywhere under runs/."""
    h = Harness("L4_6_atomic_no_tmp")
    tmp_files = sorted(str(p.relative_to(REPO))
                       for p in (REPO / "runs").rglob("*.tmp"))
    h.ck("no *.tmp residue under runs/", [], tmp_files)
    strays = sorted(str(p.relative_to(REPO))
                    for p in CASES.rglob("*.tmp")) if CASES.exists() else []
    h.ck("no *.tmp residue under the temp cases roots", [], strays)
    h.dump({"runs_tmp": tmp_files, "cases_tmp": strays,
            "runs_scanned": str(REPO / "runs")})
    return h


def main() -> int:
    names = sys.argv[1:]
    if not names or names == ["all"]:
        names = list(SCENARIOS)
    failed, summary = [], []
    for name in names:
        fn = SCENARIOS.get(name)
        if fn is None:
            print(f"unknown scenario {name!r}; known: {sorted(SCENARIOS)}")
            return 2
        print(f"\n================ {name} ================", flush=True)
        try:
            h = fn()
            summary.append({"scenario": name, "overall_ok": h.ok(),
                            "failed": [c["name"] for c in h.checks if not c["ok"]]})
            if not h.ok():
                failed.append(name)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            summary.append({"scenario": name, "overall_ok": False,
                            "error": f"{type(exc).__name__}: {exc}"})
            failed.append(name)
    print("\n================ summary ================")
    for s in summary:
        print(f"  [{'ok  ' if s.get('overall_ok') else 'FAIL'}] {s['scenario']} "
              f"{s.get('failed') or s.get('error') or ''}")
    (EV / "L4_summary.json").write_text(
        json.dumps({"summary": summary, "failed": failed}, ensure_ascii=False,
                   indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
