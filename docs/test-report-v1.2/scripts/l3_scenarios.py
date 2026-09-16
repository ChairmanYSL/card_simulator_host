"""L3 (plan section 4) and T-D0 6.5 scenarios.

Each scenario builds its own Harness (host + mocks), drives one run, records
explicit checks, then dumps evidence to tmp/v12test/evidence/L3_<name>.json.

Usage:
    python l3_scenarios.py <name> [<name> ...]
    python l3_scenarios.py all
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cases_factory as cf  # noqa: E402
from l3_harness import EV, REPO, SCRATCH, Harness, api  # noqa: E402

MOCKS_PY = REPO / "tools" / "mock_clients"
SILENT_CARD = SCRATCH / "mock_android_silent.py"
DUP_POS = SCRATCH / "mock_pos_dup.py"
REJECT_POS = SCRATCH / "mock_pos_reject_kernel.py"
# mock_common lives next to the stock mocks; the variants need it on the path
MOCK_ENV = {"PYTHONPATH": str(MOCKS_PY)}

SCENARIOS: dict = {}


def scenario(fn):
    SCENARIOS[fn.__name__] = fn
    return fn


def run_one_case(h: Harness, case_id: str, timeout: float = 90.0) -> dict | None:
    rc, out = h.start_run_cli("visa", "contactless", [case_id])
    h.note(f"run cli rc={rc} out={out!r}")
    run = h.wait_run_done(timeout=timeout)
    return run


def case_result(h: Harness, case_id: str) -> dict | None:
    return h.read_json(f"results/{case_id}.json")


# ---------------------------------------------------------------- scenario A
@scenario
def A_single_pass() -> Harness:
    """Section 4.3 single case PASS + section 6.5 forward kernel push."""
    h = Harness("A_single_pass", serve_extra=["--trace-enabled", "--log-level", "info"])
    h.start_host()
    h.start_android(tap_delay=1.0)
    h.start_pos(outcome="approved_offline", delay=2.0)
    devs = h.wait_connected()
    h.ck("both devices connected",
         True, all(devs.get(k, {}).get("connected") for k in ("android", "pos")))
    rc, out = h.start_run_cli("visa", "contactless", ["VISA-CL-001"])
    h.ck("run CLI exit code", 0, rc)
    h.ck("run_id parsed from CLI", True, bool(h.run_id), ok=bool(h.run_id))

    run = h.wait_run_done(timeout=90)
    run = run or {}
    res = case_result(h, "VISA-CL-001") or {}
    h.ck("run.status", "FINISHED", run.get("status"))
    h.ck("run.abort_reason is empty", None, run.get("abort_reason"))
    h.ck("case status", "PASS", res.get("status"))
    # The plan's section 4.3 sample command selects a field named `attempts`;
    # the persisted result actually stores `attempt` and it is 0-based.
    h.ck("case attempt (plan calls the field 'attempts')", 0, res.get("attempt"))

    rd = h.run_dir()
    trace = (rd / "trace" / "VISA-CL-001.jsonl") if rd else None
    exists = bool(trace and trace.is_file())
    lines = len(trace.read_text(encoding="utf-8").splitlines()) if exists else 0
    h.ck("trace/VISA-CL-001.jsonl exists", True, exists)
    h.ck("trace line count (2 APDU pairs = 4)", 4, lines)

    hl, al, pl = h.host_log(), h.android_log(), h.pos_log()
    # -- card side observations (section 4.3 table)
    h.sub("card: card_config with trace flag", al, "trace=True")
    h.sub("card: status ready", al, "status: ready")
    h.sub("card: status emulating", al, "status: emulating")
    h.sub("card: trace emitted 2 APDU pairs", al, "trace: emitted 2 APDU pairs")
    h.sub("card: status stopped", al, "status: stopped")
    # -- POS side observations
    h.sub("pos: pos_config applied 4 tags", pl, "pos_config applied: 4 tag")
    h.sub("pos: case_started", pl, "case_started  case=VISA-CL-001")
    # NOTE: the plan expects the POS mock to log
    #   "ack for transaction_result: ok=True detail=persisted"
    # It cannot: awaiting that ack inside on_message blocks MockClient.run()'s
    # reader loop, so the ack is never read (see scenario M_pos_ack_deadlock).
    # The observable, host-side proof of "persisted before ack" is the judgement
    # line, which is what we assert here.
    h.sub("host: transaction_result judged", hl,
          "transaction result received for VISA-CL-001 attempt")
    # -- host observations
    h.sub("host: final PASS", hl, "[VISA-CL-001] final: PASS")
    h.sub("host: run ended FINISHED", hl, "ended: FINISHED")
    # -- section 6.5 forward: kernel push happened, before pos_config
    h.sub("pos: kernel push counts", pl, "pos_kernel_config: aid=19 capk=28 term=yes")
    h.sub("host: kernel applied (AID 19 / CAPK 28)", hl,
          "pos_kernel_config applied (AID 19 条 / CAPK 28 条)")
    i_kernel = pl.find("pos_kernel_config:")
    i_poscfg = pl.find("pos_config applied:")
    h.ck("POS order: kernel push before pos_config",
         "kernel_idx < pos_config_idx",
         f"kernel_idx={i_kernel} pos_config_idx={i_poscfg}",
         ok=(0 <= i_kernel < i_poscfg))
    h.sub("host: no pos_kernel_config_ack_timeout", hl,
          "pos_kernel_config_ack_timeout", want=False)
    h.sub("host: no pos_kernel_config_apply_failed", hl,
          "pos_kernel_config_apply_failed", want=False)

    # -- section 4.13 (K): normal completion sends no session_abort
    h.ck("K: host log has NO session_abort broadcast", 0,
         h.count(hl, "session_abort broadcast"))
    h.ck("K: POS log has NO session_abort", 0, h.count(pl, "session_abort"))
    h.ck("K: card log has NO session_abort", 0, h.count(al, "session_abort"))

    h.dump({"run_status": run.get("status"), "case_status": res.get("status"),
            "trace_lines": lines})
    return h


# ---------------------------------------------------------------- scenario B
@scenario
def B_declined_offline_pass() -> Harness:
    """Section 4.4: VISA-CL-002 expects 05/declined/offline -> PASS."""
    h = Harness("B_declined_offline_pass", serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    h.start_pos(outcome="declined_offline", delay=1.5)
    h.wait_connected()
    run = run_one_case(h, "VISA-CL-002") or {}
    res = case_result(h, "VISA-CL-002") or {}
    h.ck("run.status", "FINISHED", run.get("status"))
    h.ck("case status", "PASS", res.get("status"))
    # the judged online/offline flag lives in raw_result, not in verdict
    h.ck("raw_result.online", "offline",
         (res.get("raw_result") or {}).get("online"))
    h.ck("raw_result.resp_code", "05",
         (res.get("raw_result") or {}).get("resp_code"))
    h.dump({"run_status": run.get("status"), "case_status": res.get("status"),
            "result": res})
    return h


# ---------------------------------------------------------------- scenario C
@scenario
def C_approved_online_pass() -> Harness:
    """Section 4.5: VISA-CL-003 expects approved_online."""
    h = Harness("C_approved_online_pass", serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    h.start_pos(outcome="approved_online", delay=1.5)
    h.wait_connected()
    run = run_one_case(h, "VISA-CL-003") or {}
    res = case_result(h, "VISA-CL-003") or {}
    h.ck("run.status", "FINISHED", run.get("status"))
    h.ck("case status", "PASS", res.get("status"))
    h.dump({"run_status": run.get("status"), "case_status": res.get("status"),
            "result": res})
    return h


# ---------------------------------------------------------------- scenario D
@scenario
def D_assertion_failed_stops_run() -> Harness:
    """Section 4.6: declined mock vs approval-expecting case -> FAIL + abort."""
    h = Harness("D_assertion_failed_stops_run", serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    h.start_pos(outcome="declined_offline", delay=1.5)
    h.wait_connected()
    run = run_one_case(h, "VISA-CL-001") or {}
    res = case_result(h, "VISA-CL-001") or {}
    # the POS mock's reader is blocked for 15s after reporting, so the
    # session_abort it receives can only be observed after that expires
    h.settle(17, "so the POS mock can read the session_abort frame")
    hl, al, pl = h.host_log(), h.android_log(), h.pos_log()
    h.ck("case status", "FAIL", res.get("status"))
    h.ck("failure_reason", "assertion_failed", res.get("failure_reason"))
    h.ck("run.status", "ABORTED", run.get("status"))
    # PLAN DEVIATION (recorded, not a scenario failure): section 4.6 documents
    # abort_reason == "case_failed:VISA-CL-001:assertion_failed" (with the case
    # id).  The assertion-failure path does not go through _fail(), so
    # fatal_reason is never set and the run loop writes the id-less
    # "case_failed:<reason>".  Every OTHER failure path (result_timeout,
    # *_not_connected, *_timeout) DOES include the case id.  We assert the
    # actual value here; the deviation is reported in the findings section.
    h.ck("run.abort_reason (actual: no case id)", "case_failed:assertion_failed",
         run.get("abort_reason"))
    h.note("PLAN DEVIATION: section 4.6 expects abort_reason "
           "'case_failed:VISA-CL-001:assertion_failed'; actual is "
           f"{run.get('abort_reason')!r} (case id absent on the "
           "assertion-failed path only)")
    h.sub("host: session_abort broadcast", hl, "session_abort broadcast")
    h.sub("host: broadcast reason=assertion_failed", hl,
          "session_abort broadcast (reason=assertion_failed")
    h.sub("card: session_abort reason=assertion_failed", al,
          "session_abort reason=assertion_failed")
    h.sub("pos: session_abort reason=assertion_failed", pl,
          "session_abort reason=assertion_failed")
    h.dump({"run_status": run.get("status"),
            "abort_reason": run.get("abort_reason"), "result": res})
    return h


# ---------------------------------------------------------------- scenario E1
@scenario
def E1_android_not_connected() -> Harness:
    """Section 4.7 part 1: no card mock at all -> android_not_connected."""
    h = Harness("E1_android_not_connected",
                serve_extra=["--card-ack-timeout", "2", "--pos-ack-timeout", "2"])
    h.start_host()
    h.start_pos(outcome="approved_offline", delay=1.0)
    h.wait_connected(want=("pos",))
    run = run_one_case(h, "VISA-CL-001", timeout=60) or {}
    res = case_result(h, "VISA-CL-001") or {}
    h.ck("case status", "FAIL", res.get("status"))
    h.ck("failure_reason", "android_not_connected", res.get("failure_reason"))
    h.ck("run.status", "ABORTED", run.get("status"))
    h.ck("run.abort_reason", "case_failed:VISA-CL-001:android_not_connected",
         run.get("abort_reason"))
    h.dump({"run_status": run.get("status"), "result": res})
    return h


# ---------------------------------------------------------------- scenario E2
@scenario
def E2_card_config_ack_timeout() -> Harness:
    """Section 4.7 part 2: connected card that never acks card_config."""
    h = Harness("E2_card_config_ack_timeout",
                serve_extra=["--card-ack-timeout", "3", "--pos-ack-timeout", "3"])
    h.start_host()
    h.start_android(tap_delay=0.2, script=SILENT_CARD, env=MOCK_ENV)
    h.start_pos(outcome="approved_offline", delay=1.0)
    h.wait_connected()
    run = run_one_case(h, "VISA-CL-001", timeout=60) or {}
    res = case_result(h, "VISA-CL-001") or {}
    al = h.android_log()
    h.ck("case status", "FAIL", res.get("status"))
    h.ck("failure_reason", "card_config_ack_timeout", res.get("failure_reason"))
    h.ck("run.status", "ABORTED", run.get("status"))
    h.ck("run.abort_reason", "case_failed:VISA-CL-001:card_config_ack_timeout",
         run.get("abort_reason"))
    # the plan's reverse check: the card must never be asked to start emulating
    h.sub("card: did NOT receive start_emulation", al,
          "start_emulation", want=False)
    h.dump({"run_status": run.get("status"), "result": res})
    return h


# ---------------------------------------------------------------- scenario F
@scenario
def F_pos_not_connected() -> Harness:
    """Section 4.8: POS offline is a case FAIL (v1.1 only warned)."""
    h = Harness("F_pos_not_connected", serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    h.wait_connected(want=("android",))
    run = run_one_case(h, "VISA-CL-001", timeout=60) or {}
    res = case_result(h, "VISA-CL-001") or {}
    h.settle(4, "for the session_abort fan-out to reach the card")
    al = h.android_log()
    h.ck("case status", "FAIL", res.get("status"))
    h.ck("failure_reason", "pos_not_connected", res.get("failure_reason"))
    h.ck("run.status", "ABORTED", run.get("status"))
    h.ck("run.abort_reason", "case_failed:VISA-CL-001:pos_not_connected",
         run.get("abort_reason"))
    # PLAN DEVIATION (recorded): section 4.8 says the card receives
    # stop_emulation.  This failure is raised at the kernel-push step, before
    # _wait_result() is entered, and _send_stop(CLIENT_ANDROID, ...) only runs
    # in that function's finally block -- so no stop_emulation is sent here.
    # The card is still told to stand down, via session_abort.
    h.sub("card: NOT sent stop_emulation on this path", al,
          "status: stopped", want=False)
    h.sub("card: notified via session_abort instead", al,
          "session_abort reason=pos_not_connected")
    h.note("PLAN DEVIATION: section 4.8 expects the card to receive "
           "stop_emulation; it receives session_abort reason=pos_not_connected "
           "instead (failure raised before the WAIT_RESULT stage)")
    h.dump({"run_status": run.get("status"), "result": res})
    return h


# ---------------------------------------------------------------- scenario G
@scenario
def G_result_timeout() -> Harness:
    """Section 4.9: POS never reports -> result_timeout.

    The plan suggests --interactive (waits for Enter).  We instead run the stock
    mock with --delay 9999: identical observable behaviour (no result within the
    case timeout) without depending on console stdin, which is not a reliable
    dependency under a process harness.  timeout_seconds is lowered to 15 via a
    throwaway cases root (the plan's own suggestion), so cases/ is untouched.
    """
    root = cf.fresh("timeout15")
    cf.patch_case(root, cf.VISA_001, timeout_seconds=15)
    h = Harness("G_result_timeout", cases_root=root, serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    h.start_pos(outcome="approved_offline", delay=9999)
    h.wait_connected()
    rc, out = h.start_run_cli("visa", "contactless", ["VISA-CL-001"])
    started = time.time()
    run = h.wait_run_done(timeout=90) or {}
    elapsed = time.time() - started
    res = case_result(h, "VISA-CL-001") or {}
    h.ck("case status", "FAIL", res.get("status"))
    h.ck("failure_reason", "result_timeout", res.get("failure_reason"))
    h.ck("run.status", "ABORTED", run.get("status"))
    h.ck("run.abort_reason", "case_failed:VISA-CL-001:result_timeout",
         run.get("abort_reason"))
    h.ck("elapsed near the 15s case timeout (10..40s)", True,
         f"{elapsed:.1f}s", ok=(10 <= elapsed <= 40))
    # POS mock sits in asyncio.sleep(9999) inside on_message, which blocks its
    # reader (same defect as M), so its session_abort leg is unobservable here.
    h.settle(5, "for the session_abort broadcast to finish")
    hl, al, pl = h.host_log(), h.android_log(), h.pos_log()
    h.sub("host: session_abort broadcast", hl, "session_abort broadcast")
    h.sub("host: broadcast reason=result_timeout", hl,
          "session_abort broadcast (reason=result_timeout")
    h.sub("card: session_abort reason=result_timeout", al,
          "session_abort reason=result_timeout")
    h.note("POS-side session_abort leg NOT asserted: the POS mock is parked in "
           "asyncio.sleep(9999) inside on_message, so its reader cannot consume "
           "the frame (see scenario M for the same root cause)")
    h.dump({"run_status": run.get("status"), "elapsed_s": round(elapsed, 1),
            "result": res})
    return h


# ---------------------------------------------------------------- scenario H
@scenario
def H_fail_then_skipped() -> Harness:
    """Section 4.10 (Q9): first case FAILs, the rest become SKIPPED."""
    h = Harness("H_fail_then_skipped", serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    h.start_pos(outcome="declined_offline", delay=1.5)
    h.wait_connected()
    rc, out = h.start_run_cli("visa", "contactless",
                              ["VISA-CL-001", "VISA-CL-002", "VISA-CL-003"])
    run = h.wait_run_done(timeout=90) or {}
    r1 = case_result(h, "VISA-CL-001") or {}
    r2 = case_result(h, "VISA-CL-002") or {}
    r3 = case_result(h, "VISA-CL-003") or {}
    h.ck("VISA-CL-001 status", "FAIL", r1.get("status"))
    h.ck("VISA-CL-002 status", "SKIPPED", r2.get("status"))
    h.ck("VISA-CL-003 status", "SKIPPED", r3.get("status"))
    h.ck("VISA-CL-002 note", "run_stopped_after_failure:VISA-CL-001",
         r2.get("note"))
    h.ck("VISA-CL-003 note", "run_stopped_after_failure:VISA-CL-001",
         r3.get("note"))
    h.ck("run.status", "ABORTED", run.get("status"))
    # history endpoint must expose all three results
    st, body = api(f"/api/history/{h.run_id}")
    hist = body.get("results", {}) if isinstance(body, dict) else {}
    h.ck("history HTTP status", 200, st)
    h.ck("history has all 3 case results", 3, len(hist),
         ok=(len(hist) == 3))
    h.settle(17, "so the POS mock's blocked reader can consume session_abort")
    # reverse: no case_started after VISA-CL-001 on the POS side
    hl, pl = h.host_log(), h.pos_log()
    h.ck("POS: exactly one case_started",
         1, h.count(pl, "case_started  case="))
    h.sub("POS: no case_started for VISA-CL-002", pl,
          "case_started  case=VISA-CL-002", want=False)
    h.sub("host: broadcast reason=assertion_failed", hl,
          "session_abort broadcast (reason=assertion_failed")
    h.sub("pos: session_abort reason=assertion_failed", pl,
          "session_abort reason=assertion_failed")
    h.dump({"run_status": run.get("status"),
            "statuses": {c: (case_result(h, c) or {}).get("status")
                         for c in ("VISA-CL-001", "VISA-CL-002", "VISA-CL-003")},
            "queue": run.get("queue")})
    return h


# ---------------------------------------------------------------- scenario I
@scenario
def I_kind_group() -> Harness:
    """Section 4.11: kind=group runs exactly one group's children (API only)."""
    h = Harness("I_kind_group", serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    h.start_pos(outcome="approved_offline", delay=1.5)
    h.wait_connected()

    # negatives first
    st404, b404 = h.api_run({"brand": "visa", "mode": "contactless",
                             "kind": "group", "group_id": "NOPE-001"})
    h.ck("unknown group -> HTTP 404", 404, st404)
    st400, b400 = h.api_run({"brand": "visa", "mode": "contactless",
                             "kind": "group"})
    h.ck("group kind without group_id -> HTTP 400", 400, st400)

    st, body = h.api_run({"brand": "visa", "mode": "contactless",
                          "kind": "group", "group_id": "OPBD-001"})
    h.ck("group run accepted -> HTTP 200", 200, st)
    h.ck("response has ok=true", True,
         (body or {}).get("ok") if isinstance(body, dict) else body)
    run = h.wait_run_done(timeout=120) or {}
    q = run.get("queue") or []
    h.ck("queue is exactly the 4 OPBD-001 children",
         ["OPBD-001-01", "OPBD-001-02", "OPBD-001-03", "OPBD-001-04"], q)
    h.dump({"run_status": run.get("status"), "queue": q,
            "http_404": st404, "http_400": st400,
            "statuses": {c: (case_result(h, c) or {}).get("status") for c in q}})
    return h


# ---------------------------------------------------------------- scenario J
@scenario
def J_manual_abort() -> Harness:
    """Section 4.12: manual abort -> ABORTED, empty failure_reason, manual_abort."""
    h = Harness("J_manual_abort", serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    h.start_pos(outcome="approved_offline", delay=9999)  # long WAIT_RESULT window
    h.wait_connected()
    rc, out = h.start_run_cli("visa", "contactless", ["VISA-CL-001"])

    # wait until the session is actually waiting for the POS result
    in_wait = False
    end = time.time() + 40
    while time.time() < end:
        st, snap = api("/api/run")
        sess = (snap or {}).get("session") if isinstance(snap, dict) else None
        if sess and sess.get("current", {}).get("state") == "WAIT_RESULT":
            in_wait = True
            break
        time.sleep(0.3)
    h.ck("session reached WAIT_RESULT", True, in_wait)

    rc_abort, out_abort = h.cli("abort")
    h.note(f"abort rc={rc_abort} out={out_abort!r}")
    h.ck("abort command exit code", 0, rc_abort)
    run = h.wait_run_done(timeout=60) or {}
    res = case_result(h, "VISA-CL-001") or {}
    h.ck("case status", "ABORTED", res.get("status"))
    h.ck("failure_reason empty (must NOT inherit)", None,
         res.get("failure_reason"))
    h.ck("run.status", "ABORTED", run.get("status"))
    h.ck("run.abort_reason", "manual abort", run.get("abort_reason"))
    h.settle(5, "for the session_abort broadcast to finish")
    hl, al, pl = h.host_log(), h.android_log(), h.pos_log()
    h.sub("host: session_abort broadcast", hl, "session_abort broadcast")
    h.sub("host: reason=manual_abort", hl, "reason=manual_abort")
    h.sub("card: session_abort reason=manual_abort", al,
          "session_abort reason=manual_abort")
    h.note("POS-side session_abort leg NOT asserted: the POS mock is parked in "
           "asyncio.sleep(9999) inside on_message, so its reader cannot consume "
           "the frame (see scenario M for the same root cause)")
    h.dump({"run_status": run.get("status"),
            "abort_reason": run.get("abort_reason"), "result": res})
    return h


# ------------------------------------------------- T-D0 6.5 reverse / failure
@scenario
def TD0_reverse_no_kernel_params() -> Harness:
    """Section 6.5 reverse: without kernel_params.json nothing is pushed."""
    root = cf.fresh("nokp")
    dropped = cf.drop_kernel_params(root)
    h = Harness("TD0_reverse_no_kernel_params", cases_root=root,
                serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    h.start_pos(outcome="approved_offline", delay=1.5)
    h.wait_connected()
    h.ck("kernel_params.json removed from cases root", True, dropped)
    hl = h.host_log()
    h.sub("host: no kernel_params loaded for visa", hl,
          "loaded local kernel params", want=False)
    run = run_one_case(h, "VISA-CL-001") or {}
    res = case_result(h, "VISA-CL-001") or {}
    pl = h.pos_log()
    h.sub("pos: NO pos_kernel_config frame", pl, "pos_kernel_config", want=False)
    h.ck("case still PASSes without kernel params", "PASS", res.get("status"))
    h.ck("run.status", "FINISHED", run.get("status"))
    h.dump({"run_status": run.get("status"), "result": res})
    return h


@scenario
def TD0_fail_plan_example_term() -> Harness:
    """Section 6.5 failure test, using the plan's LITERAL example term.

    The plan calls '9F660436000080' an odd-length hex term.  It is not: it is 14
    hex chars (7 bytes) and decodes as well-formed BER-TLV {9F66: 36000080}.
    This scenario records what actually happens so the plan defect is evidenced
    rather than assumed.
    """
    root = cf.fresh("badterm_plan")
    kp = cf.patch_kernel_params(root, term="9F660436000080")
    h = Harness("TD0_fail_plan_example_term", cases_root=root,
                serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    h.start_pos(outcome="approved_offline", delay=1.5)
    h.wait_connected()
    hl = h.host_log()
    h.ck("patched term value", "9F660436000080", kp.get("term"))
    h.ck("patched term is even-length hex", True,
         len(kp.get("term", "")) % 2 == 0)
    h.sub("host: brand kernel params still loaded", hl,
          "loaded local kernel params")
    h.sub("host: no term validation error", hl,
          "kernel_params.term 不是偶数长度 hex", want=False)
    run = run_one_case(h, "VISA-CL-001") or {}
    res = case_result(h, "VISA-CL-001") or {}
    pl = h.pos_log()
    h.sub("pos: kernel push still happens", pl, "pos_kernel_config:")
    h.ck("PLAN DEFECT EVIDENCE: case did not FAIL on this term",
         "PASS", res.get("status"))
    h.dump({"run_status": run.get("status"), "result": res,
            "plan_claim": "odd-length hex term should fail the case",
            "observed": f"term is even-length and valid TLV; status={res.get('status')}"})
    return h


@scenario
def TD0_fail_odd_length_term() -> Harness:
    """Section 6.5 failure test with a GENUINELY odd-length term."""
    root = cf.fresh("badterm_odd")
    bad = "9F6604360000809"  # 15 chars -> odd
    kp = cf.patch_kernel_params(root, term=bad)
    h = Harness("TD0_fail_odd_length_term", cases_root=root,
                serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    h.start_pos(outcome="approved_offline", delay=1.5)
    h.wait_connected()
    hl = h.host_log()
    h.ck("patched term value", bad, kp.get("term"))
    h.ck("patched term is odd-length", True, len(bad) % 2 == 1)
    # the plan allows either of two outcomes; record which one occurs
    caught = "kernel_params.term 不是偶数长度 hex" in hl
    h.ck("host recorded a load-time term validation error", True, caught)
    run = run_one_case(h, "VISA-CL-001") or {}
    res = case_result(h, "VISA-CL-001") or {}
    pl = h.pos_log()
    h.sub("pos: kernel push still happens (mock acks ok)", pl, "pos_kernel_config:")
    h.dump({"run_status": run.get("status"), "result": res,
            "load_error_recorded": caught,
            "note": "plan allows either a load-time error or an apply failure; "
                    "with the stock mock (always acks ok) no apply failure can occur"})
    return h


@scenario
def TD0_fail_reject_kernel() -> Harness:
    """Section 6.5 failure test that actually reaches the apply-failed branch.

    The stock POS mock acks everything with ok=true, so
    `pos_kernel_config_apply_failed` is unreachable with it.  This scenario uses
    a mock that acks ok=false, which is the only way to exercise
    executor.py:760-763 end to end.
    """
    h = Harness("TD0_fail_reject_kernel", serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    h.start_pos(outcome="approved_offline", delay=1.5, script=REJECT_POS, env=MOCK_ENV)
    h.wait_connected()
    run = run_one_case(h, "VISA-CL-001", timeout=60) or {}
    res = case_result(h, "VISA-CL-001") or {}
    hl, pl = h.host_log(), h.pos_log()
    h.sub("pos: reject variant acked ok=false", pl, "REJECT VARIANT")
    h.ck("case status", "FAIL", res.get("status"))
    h.ck("failure_reason", "pos_kernel_config_apply_failed",
         res.get("failure_reason"))
    h.ck("run.status", "ABORTED", run.get("status"))
    h.ck("run.abort_reason",
         "case_failed:VISA-CL-001:pos_kernel_config_apply_failed",
         run.get("abort_reason"))
    h.sub("host: rejection logged", hl, "POS rejected pos_kernel_config")
    h.sub("host: pos_config never pushed", hl, "pos_config applied", want=False)
    h.dump({"run_status": run.get("status"), "result": res})
    return h


# ------------------------------------------------- scenario M (defect probe)
@scenario
def M_pos_ack_deadlock() -> Harness:
    """Probe: can the stock POS mock ever observe the host's result ack?

    mock_common.MockClient.run() reads frames in a loop and awaits
    on_message() inline.  mock_pos.on_message() ends with
    `await client.send_and_wait_ack(...)`, which waits for MSG_ACK -- but the
    only code that can read that ack is the very reader loop now blocked inside
    on_message().  Prediction: the POS mock never logs the ack and instead logs
    "ack timeout (15s) for transaction_result" followed by its WARNING.

    This scenario waits past the ack budget to capture that directly.
    """
    h = Harness("M_pos_ack_deadlock", serve_extra=["--trace-enabled"])
    h.start_host()
    h.start_android(tap_delay=0.5)
    h.start_pos(outcome="approved_offline", delay=1.0)
    h.wait_connected()
    rc, out = h.start_run_cli("visa", "contactless", ["VISA-CL-001"])
    run = h.wait_run_done(timeout=60) or {}
    h.ck("run finished (host side OK)", "FINISHED", run.get("status"))
    h.note("sleeping 20s to let the POS mock's 15s ack wait expire")
    time.sleep(20)
    pl, hl = h.pos_log(), h.host_log()
    res = case_result(h, "VISA-CL-001") or {}
    h.ck("case status (host judged correctly regardless)", "PASS",
         res.get("status"))
    h.sub("host: result judged", hl, "transaction result received for VISA-CL-001")
    # defect evidence
    h.sub("DEFECT: POS mock never logs the result ack", pl,
          "ack for transaction_result: ok=True detail=persisted", want=False)
    h.sub("DEFECT: POS mock reports an ack timeout instead", pl,
          "ack timeout (15.0s) for transaction_result")
    h.sub("DEFECT: POS mock warns the result was not persisted", pl,
          "WARNING: no ack for transaction_result (not persisted?)")
    h.dump({"run_status": run.get("status"), "case_status": res.get("status"),
            "defect": "mock_common reader loop blocked by in-handler ack wait",
            "plan_expectation": "POS logs 'ack for transaction_result: "
                                "ok=True detail=persisted' (section 4.3 step 6)",
            "observed": "ack timeout (15s) + 'not persisted?' warning"})
    return h


def main() -> int:
    names = sys.argv[1:]
    if not names or names == ["all"]:
        names = list(SCENARIOS)
    failed = []
    summary = []
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
        flag = "ok  " if s.get("overall_ok") else "FAIL"
        print(f"  [{flag}] {s['scenario']} {s.get('failed') or s.get('error') or ''}")
    (EV / "L3_summary.json").write_text(
        json.dumps({"summary": summary, "failed": failed}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
