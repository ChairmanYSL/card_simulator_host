"""Regression tests for the v1.2 test-audit fixes (F2, F3, H1, H2, H-LOG, C3).

One test per audited defect, named after its ID so a failing run maps straight
back to DEFECTS.md:

* F2 — ``run.abort_reason`` must always carry the case id, including the
  assertion-FAIL path that never goes through ``_fail()``;
* F3 — both ``results/<case>.json`` writers emit ``attempt`` AND ``attempts``;
* H1 — ``ERROR`` is a terminal run status and survives a later crash;
* H2 — once the card reports ``emulating``, every exit path sends
  ``stop_emulation`` (and cases that never started do NOT);
* H-LOG — ``emulating.registered_aids`` is logged (ruling 3.1c);
* C3 — external ``kernel_params_dir`` precedence and its misconfiguration.

The pinned behaviours are unchanged; only the POS-side mechanics moved to
protocol v2 (CTEL2): the case is started by ``act`` (not ``case_started``) and
the outcome arrives as ``out`` (not ``transaction_result``), so the POS-side
config failure is now the single v2 ``config_rejected`` (POS ``config`` carries
the kernel AID/CAPK/TERM) rather than a separate ``pos_config``/
``pos_kernel_config_apply_failed`` pair.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from emv_host.cases.models import (
    AidKernelParam,
    CardProfile,
    KernelParams,
    MatchRule,
    OperatorInstructions,
    PosParam,
    SuitePolicy,
    TestCase,
    TlvField,
    TransactionParams,
)
from emv_host.runner.executor import TERMINAL_RUN_STATUS, RunExecutor
from emv_host.runner.session import RunSession
from emv_host.store.run_store import RunStore

CASE_ID = "VISA-CL-001"
TERM = "9F6604360000809F1A0201569F7A0101"


async def _sink(event, data):  # noqa: ARG001
    pass


def make_case(case_id: str = CASE_ID, *, pos_params: bool = False) -> TestCase:
    return TestCase(
        case_id=case_id, title=case_id, brand="visa",
        profile=CardProfile(id=case_id),
        transaction=TransactionParams(
            operator_instructions=OperatorInstructions(),
            pos_params=[PosParam(tag="9F02", value="100.00")] if pos_params else []),
        timeout_seconds=10)


class StubLibrary:
    def __init__(self, cases, kernel=None):
        self.cases = cases
        self._kernel = kernel
        self.brands = {"visa": type("S", (), {
            "suite_policy": SuitePolicy(retry_count=0),
            "kernel_params": kernel})()}

    def get_case(self, brand, mode, case_id):  # noqa: ARG002
        return self.cases.get(case_id)

    def kernel_params_for(self, brand):  # noqa: ARG002
        return self._kernel


class Conn:
    """A device stub: records frames, acks everything unless told otherwise.

    ``reject`` is keyed by the frame's ``config_type`` (card link) or ``type``
    (CTEL2 signals); the override value is a full ack frame, so a v1.2-style
    ``{"ok": False}`` and a v2 ``signal_ack("NO")`` both work.
    """

    def __init__(self, label: str, *, reject: dict[str, dict] | None = None):
        self.label = label
        self.reject = reject or {}
        self.sent: list[dict] = []

    async def send(self, frame: dict) -> None:
        self.sent.append(frame)

    async def send_and_wait_ack(self, frame, timeout=15.0, event=None):  # noqa: ARG002
        self.sent.append(frame)
        key = frame.get("config_type") or frame.get("type")
        return self.reject.get(key, {"ok": True, "detail": ""})

    async def send_and_wait_signal(self, frame, expect_type, timeout=15.0,  # noqa: ARG002
                                   event=None):
        self.sent.append(frame)
        key = frame.get("type")
        return self.reject.get(key, signal_ack("YES"))

    def frames(self, kind: str) -> list[dict]:
        return [f for f in self.sent if f.get("type") == kind]


class StubProtocol:
    heartbeat_interval = 10.0
    heartbeat_warn = 30.0

    def __init__(self, android, pos):
        self.android, self.pos = android, pos

    def get_client(self, client_type):
        return self.android if client_type == "android" else self.pos

    async def send_to(self, client_type, frame):
        conn = self.get_client(client_type)
        if conn is None:
            return False
        await conn.send(frame)
        return True

    async def send_to_and_ack(self, client_type, frame, timeout=15.0):
        conn = self.get_client(client_type)
        if conn is None:
            return None
        return await conn.send_and_wait_ack(frame, timeout=timeout)

    async def send_to_and_ack_signal(self, client_type, frame, expect_type,
                                     timeout=15.0):
        """Mirror protocol/server.py:148 — the v2 POS signal request."""
        conn = self.get_client(client_type)
        if conn is None:
            return None
        return await conn.send_and_wait_signal(frame, expect_type,
                                               timeout=timeout)


def signal_ack(code: str = "YES") -> dict:
    """A CTEL2 ``*_ack`` as the POS sends it (design section 2.2)."""
    return {"signalData": [{"id": "ResponseCode", "value": code}]}


#: An "approved" CTEL2 OUT (OPS + DataRecord) that PASSes VISA-CL-001.
APPROVED = [
    {"id": "OPS", "value": "1000F00030F0FF00"},
    {"id": "DataRecord", "value": "8A0230309F270140"},
]
#: The same shape but declined.  With no ARC in the DataRecord the derived
#: ``resp_code`` is "05", so VISA-CL-001's ``resp_code == "00"`` assertion FAILs.
DECLINED = [{"id": "OPS", "value": "20F0F00030F0FF00"}]


#: Sentinel: "the caller did not say anything about this device" (-> give it a
#: stub).  An explicit ``None`` means "that device is NOT connected".
_DEFAULT = object()


DEFAULT_ANDROID = object()


def build(tmp_path, cases, *, kernel=None, android_reject=None, android=DEFAULT_ANDROID,
          pos_reject=None, pos=_DEFAULT, emit_emulating=True,
          result_payload=None) -> tuple[RunExecutor, Conn, Conn]:
    """Wire the executor to device stubs.

    * ``emit_emulating`` reports the android "emulating" status through the
      protocol layer — never by poking ``executor._session``;
    * ``result_payload`` makes the POS report that CTEL2 OUT as soon as it is
      told `act`, i.e. it drives the real `_attempt` path (including the
      abort/timeout branches) instead of calling the handler directly;
    * ``pos=None`` means "no POS is connected"; the default gives it a stub.
    """
    executor = RunExecutor(StubLibrary(cases, kernel), None, RunStore(tmp_path), _sink)
    android_conn = (Conn("android@stub", reject=android_reject)
                    if android is DEFAULT_ANDROID else android)
    pos_conn = None if pos is None else (
        Conn("pos@stub", reject=pos_reject) if pos is _DEFAULT else pos)
    protocol = StubProtocol(android_conn, pos_conn)

    async def send_to(client_type, frame):
        conn = android_conn if client_type == "android" else pos_conn
        if conn is None:
            return False
        await conn.send(frame)
        if client_type == "android" and frame.get("type") == "stop_emulation" \
                and executor._session is not None:
            # the card answers stop_emulation with status:stopped
            await executor._on_android_status(android_conn, {
                "type": "status", "state": "stopped",
                "case_id": frame.get("case_id"),
                "attempt_id": frame.get("attempt_id")})
        return True

    async def send_to_and_ack(client_type, frame, timeout=15.0):
        conn = android_conn if client_type == "android" else pos_conn
        if conn is None:
            return None
        ack = await conn.send_and_wait_ack(frame, timeout=timeout)
        if client_type == "android" and frame.get("type") == "start_emulation" \
                and emit_emulating:
            session = executor._session
            if session is not None and session._emulation_waiter is not None:
                session.deliver_emulation_status("emulating")
        return ack

    async def send_to_and_ack_signal(client_type, frame, expect_type,
                                     timeout=15.0):
        conn = android_conn if client_type == "android" else pos_conn
        if conn is None:
            return None
        ack = await conn.send_and_wait_signal(frame, expect_type, timeout=timeout)
        if client_type == "pos" and frame.get("type") == "act" \
                and result_payload is not None:
            # Deliver as a real POS would: a short moment after the ACT ack, NOT
            # inline — _attempt creates the OUT waiter only after this ack
            # returns, so an inline OUT would be quarantined as unattributed.
            async def report(signal_data=result_payload,
                             msg_id=frame.get("msg_id")):
                await asyncio.sleep(0.02)
                await executor._on_out(pos_conn, {
                    "type": "out", "msg_id": f"out-{msg_id}",
                    "device_id": "pos-1", "app_version": "2.0.0",
                    "signalData": signal_data})
            asyncio.get_running_loop().create_task(report())
        return ack

    protocol.send_to = send_to  # type: ignore[assignment]
    protocol.send_to_and_ack = send_to_and_ack  # type: ignore[assignment]
    protocol.send_to_and_ack_signal = send_to_and_ack_signal  # type: ignore[assignment]
    executor.protocol = protocol
    return executor, android_conn, pos_conn


async def run_one(executor: RunExecutor, case: TestCase,
                  monkeypatch: pytest.MonkeyPatch, wait_seconds: float = 0.5) -> RunSession:
    """Run one case with the result wait shortened.

    ``TestCase.timeout_seconds`` has a 10s floor, so every ``asyncio.wait`` is
    capped: result-driven tests inject their CTEL2 OUT within ~20ms and
    resolve before the cap, while timeout-driven tests deliberately never
    resolve.  Shrinking the deadline (rather than reacting to which future
    completed) keeps the real ``_attempt`` control flow intact.
    """
    real_wait = asyncio.wait

    async def fast_wait(fs, *, timeout=None, return_when=None):
        return await real_wait(set(fs), timeout=wait_seconds,
                               return_when=return_when)

    monkeypatch.setattr(asyncio, "wait", fast_wait)
    run = executor.store.new_run("visa", "contactless", [case.case_id], "single")
    session = RunSession(run, "visa", "contactless", [case.case_id])
    # the real start() publishes the session before the loop runs; device frames
    # arrive through the protocol handler, which reads executor._session
    executor._session = session
    await executor._run_loop(session)
    return session


# -- F2 ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_f2_assertion_failure_abort_reason_carries_case_id(tmp_path, monkeypatch):
    """The assertion FAIL path never calls _fail() from _judge, yet must name
    the case — and must NOT be relabelled out_timeout by the wait loop."""
    case = TestCase(
        case_id=CASE_ID, title=CASE_ID, brand="visa",
        profile=CardProfile(id=CASE_ID),
        transaction=TransactionParams(operator_instructions=OperatorInstructions()),
        # DECLINED carries no ARC, so the derived resp_code is "05" and this rule
        # (resp_code == "00") fails -> assertion FAIL
        expected_results=[MatchRule(path="resp_code", op="eq", expect="00")],
        timeout_seconds=10)
    executor, _android, pos = build(tmp_path, {CASE_ID: case},
                                    result_payload=DECLINED)
    session = await run_one(executor, case, monkeypatch)
    run = executor.store.get_run(session.run["run_id"])
    record = executor.store.get_case_result(run["run_id"], CASE_ID)

    assert record["status"] == "FAIL"
    assert record["failure_reason"] == "assertion_failed", \
        "a failing assertion must not be relabelled by the wait loop"
    # before F2 this was the case-less "case_failed:assertion_failed"
    assert run["abort_reason"] == f"case_failed:{CASE_ID}:assertion_failed"
    # v2 (design section 10) removed session_abort, so the failing verdict is
    # no longer broadcast to the POS; the case record + run.abort_reason above
    # are the record of it.
    assert pos.frames("session_abort") == []
    assert run["status"] == "ABORTED"


@pytest.mark.asyncio
async def test_f2_passing_case_is_not_aborted(tmp_path, monkeypatch):
    """Control: the same wiring with an approved payload ends FINISHED."""
    case = TestCase(
        case_id=CASE_ID, title=CASE_ID, brand="visa",
        profile=CardProfile(id=CASE_ID),
        transaction=TransactionParams(operator_instructions=OperatorInstructions()),
        timeout_seconds=10)
    executor, _android, _pos = build(tmp_path, {CASE_ID: case},
                                     result_payload=APPROVED)
    session = await run_one(executor, case, monkeypatch)
    run = executor.store.get_run(session.run["run_id"])
    assert run["status"] == "FINISHED"
    assert run["abort_reason"] is None
    assert executor.store.get_case_result(run["run_id"], CASE_ID)["status"] == "PASS"


@pytest.mark.asyncio
async def test_f2_every_failure_path_names_the_case(tmp_path, monkeypatch):
    """All v2 failure reasons share one shape: case_failed:<case_id>:<reason>.

    The reason set follows design section 8: the POS signal ACKs
    (test_info/config/act), the missing-POS path, and the missing-OUT timeout.
    """
    reasons = {
        "android_not_connected": dict(android=None),
        "pos_not_connected": dict(pos=None, case_pos_params=True),
        "test_info_rejected": dict(pos_reject={"test_info": signal_ack("NO")}),
        "config_rejected": dict(
            kernel=KernelParams(aids=[AidKernelParam(aid="A0000000031010")],
                                term=TERM),
            pos_reject={"config": signal_ack("NO")}),
        "act_rejected": dict(pos_reject={"act": signal_ack("NO")}),
        "act_not_supported": dict(pos_reject={"act": signal_ack("NOTSUPPORTED")}),
        "out_timeout": dict(),
    }
    for expected, kw in reasons.items():
        workdir = tmp_path / expected
        workdir.mkdir(parents=True, exist_ok=True)
        case_pos_params = kw.pop("case_pos_params", False)
        case = make_case(pos_params=case_pos_params)
        executor, _android, _pos = build(workdir, {CASE_ID: case}, **kw)
        session = await run_one(executor, case, monkeypatch)
        run = executor.store.get_run(session.run["run_id"])
        assert run["abort_reason"] == f"case_failed:{CASE_ID}:{expected}", \
            f"{expected}: got {run['abort_reason']}"


# -- F3 ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_f3_all_writers_emit_attempt_and_attempts(tmp_path, monkeypatch):
    """PASS (via _judge) and ABORTED (via _finalize_case) share one schema."""
    # a) PASS path -> _judge, driven through the real attempt flow
    case = make_case()
    pass_dir = tmp_path / "pass"
    pass_dir.mkdir(parents=True, exist_ok=True)
    executor, _android, _pos = build(pass_dir, {CASE_ID: case},
                                     result_payload=APPROVED)
    session = await run_one(executor, case, monkeypatch)
    passed = executor.store.get_case_result(session.run["run_id"], CASE_ID)
    assert passed["status"] == "PASS"
    assert passed["attempt"] == 0 and passed["attempts"] == 1

    # b) ABORTED path -> _finalize_case
    case = make_case()
    abort_dir = tmp_path / "aborted"
    abort_dir.mkdir(parents=True, exist_ok=True)
    executor, _android, _pos = build(abort_dir, {CASE_ID: case})
    session = RunSession(executor.store.new_run("visa", "contactless", [CASE_ID], "single"),
                         "visa", "contactless", [CASE_ID])
    session.begin_attempt(CASE_ID, "att-1", 0)
    await executor._finalize_case(session, case, "aborted", 0)
    aborted = executor.store.get_case_result(session.run["run_id"], CASE_ID)
    assert aborted["status"] == "ABORTED"
    assert aborted["attempt"] == 0 and aborted["attempts"] == 1


def test_f3_crash_writer_emits_attempt_and_attempts(tmp_path):
    """_finalize_crash writes the same pair (third writer)."""
    executor = RunExecutor(StubLibrary({}), None, RunStore(tmp_path), _sink)
    run = executor.store.new_run("visa", "contactless", [CASE_ID], "single")
    session = RunSession(run, "visa", "contactless", [CASE_ID])
    session.begin_attempt(CASE_ID, "att-1", 2)
    asyncio.run(executor._finalize_crash(session, "boom"))
    record = executor.store.get_case_result(run["run_id"], CASE_ID)
    assert record["attempt"] == 2 and record["attempts"] == 3


# -- H1 ----------------------------------------------------------------------

def test_h1_error_is_terminal_run_status():
    assert "ERROR" in TERMINAL_RUN_STATUS


@pytest.mark.asyncio
async def test_h1_error_status_survives_a_later_crash(tmp_path):
    """An android error ends the run as ERROR; _finalize_crash must not lower it."""
    case = make_case()
    executor, android, _pos = build(tmp_path, {CASE_ID: case})
    executor._session = RunSession(
        executor.store.new_run("visa", "contactless", [CASE_ID], "single"),
        "visa", "contactless", [CASE_ID])
    session = executor._session
    session.begin_attempt(CASE_ID, "att-1", 0)
    run = session.run

    await executor._on_android_status(
        android, {"type": "status", "state": "error", "case_id": CASE_ID,
                  "attempt_id": "att-1",
                  "detail": {"code": "hce_failed", "message": "died"}})
    assert session.failure_reason == "android_error"
    run["status"] = "ERROR"
    executor.store.update_run(run)

    # a crash after the ERROR terminal state must not overwrite it (H1):
    # _finalize_crash is guarded by TERMINAL_RUN_STATUS, which now contains ERROR
    await executor._finalize_crash(session, "late crash")
    assert executor.store.get_run(run["run_id"])["status"] == "ERROR"


# -- H2 ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_h2_early_failure_still_parks_the_card(tmp_path, monkeypatch):
    """pos_not_connected after emulating must still send stop_emulation."""
    case = make_case(pos_params=True)
    executor, android, _pos = build(tmp_path, {CASE_ID: case}, pos=None)
    session = await run_one(executor, case, monkeypatch)

    record = executor.store.get_case_result(session.run["run_id"], CASE_ID)
    assert record["failure_reason"] == "pos_not_connected"
    stops = android.frames("stop_emulation")
    assert len(stops) == 1, "the card was emulating and must be parked"
    assert stops[0]["case_id"] == CASE_ID


@pytest.mark.asyncio
async def test_h2_kernel_rejection_still_parks_the_card(tmp_path, monkeypatch):
    case = make_case()
    kernel = KernelParams(aids=[AidKernelParam(aid="A0000000031010")], term=TERM)
    executor, android, _pos = build(
        tmp_path, {CASE_ID: case}, kernel=kernel,
        pos_reject={"config": signal_ack("NO")})
    session = await run_one(executor, case, monkeypatch)

    assert executor.store.get_case_result(
        session.run["run_id"], CASE_ID)["failure_reason"] == "config_rejected"
    assert len(android.frames("stop_emulation")) == 1


@pytest.mark.asyncio
async def test_h2_normal_pass_sends_exactly_one_stop(tmp_path, monkeypatch):
    """The moved stop must not be emitted twice on the happy path."""
    case = make_case()
    executor, android, _pos = build(tmp_path, {CASE_ID: case},
                                    result_payload=APPROVED)
    session = await run_one(executor, case, monkeypatch)
    record = executor.store.get_case_result(session.run["run_id"], CASE_ID)
    assert record["status"] == "PASS"
    assert len(android.frames("stop_emulation")) == 1


@pytest.mark.asyncio
async def test_h2_no_stop_when_card_never_emulated(tmp_path, monkeypatch):
    """A card-side failure before emulating must not produce stop_emulation."""
    case = make_case()
    executor, android, _pos = build(
        tmp_path, {CASE_ID: case},
        android_reject={"card_config": {"ok": False, "detail": "bad"}})
    session = await run_one(executor, case, monkeypatch)

    assert executor.store.get_case_result(
        session.run["run_id"], CASE_ID)["failure_reason"] == "card_config_rejected"
    assert android.frames("stop_emulation") == []


# -- H-LOG -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hlog_emulating_registered_aids_is_logged(tmp_path):
    """Ruling 3.1(c): emulating.registered_aids is the authoritative list."""
    from emv_host.runner.executor import RunExecutor as _Exec  # noqa: F401
    logged: list[str] = []

    executor = RunExecutor(StubLibrary({}), None, RunStore(tmp_path), _sink)

    async def capture(level, message):
        logged.append(message)

    executor.log = capture  # type: ignore[assignment]
    executor._session = RunSession(
        {"run_id": "r", "status": "RUNNING"}, "visa", "contactless", [])
    session = executor._session
    session.begin_attempt(CASE_ID, "att-1", 0)
    session._emulation_waiter = asyncio.get_running_loop().create_future()

    await executor._on_android_status(Conn("android@stub"), {
        "type": "status", "state": "emulating", "case_id": CASE_ID,
        "attempt_id": "att-1",
        "detail": {"registered_aids": ["A0000000031010"]}})

    assert any("registered_aids" in m and "A0000000031010" in m for m in logged), \
        logged


# -- C3 ----------------------------------------------------------------------

def _kernel_json(term: str = TERM) -> dict:
    return {"aids": [{"aid": "A0000000031010",
                      "fields": [{"tag": "DF01", "value": "01"}]}],
            "caps": [], "term": term}


def test_c3_external_dir_takes_precedence(tmp_path):
    from emv_host.cases import load_library

    cases_root = tmp_path / "cases"
    (cases_root / "visa").mkdir(parents=True)
    (cases_root / "visa" / "brand.json").write_text(json.dumps({
        "brand": "visa", "display_name": "Visa",
        "entry_modes": ["contactless"]}), encoding="utf-8")

    external = tmp_path / "secure"
    external.mkdir()
    (external / "visa.json").write_text(
        json.dumps(_kernel_json("9F6604360000809F1A0201569F7A0101")),
        encoding="utf-8")

    library = load_library(cases_root, kernel_params_dir=external)
    params = library.kernel_params_for("visa")
    assert params is not None and params.term == "9F6604360000809F1A0201569F7A0101"
    assert not [i for i in library.issues if i.severity == "error"]


def test_c3_missing_external_file_is_reported_not_silent(tmp_path):
    """A typo'd kernel params dir must be visible, not silently empty."""
    from emv_host.cases import load_library

    cases_root = tmp_path / "cases"
    (cases_root / "visa").mkdir(parents=True)
    (cases_root / "visa" / "brand.json").write_text(json.dumps({
        "brand": "visa", "display_name": "Visa",
        "entry_modes": ["contactless"]}), encoding="utf-8")
    external = tmp_path / "secure"
    external.mkdir()  # configured but empty

    library = load_library(cases_root, kernel_params_dir=external)
    assert library.kernel_params_for("visa") is None
    assert any("kernel params dir" in i.message and i.severity == "error"
               for i in library.issues), [i.message for i in library.issues]


def test_c3_without_external_dir_in_repo_file_is_no_longer_read(tmp_path):
    """No external dir configured -> kernel params are simply not loaded."""
    from emv_host.cases import load_library

    cases_root = tmp_path / "cases"
    (cases_root / "visa").mkdir(parents=True)
    (cases_root / "visa" / "brand.json").write_text(json.dumps({
        "brand": "visa", "display_name": "Visa",
        "entry_modes": ["contactless"]}), encoding="utf-8")
    (cases_root / "visa" / "kernel_params.json").write_text(
        json.dumps(_kernel_json()), encoding="utf-8")

    library = load_library(cases_root)  # no external dir
    assert library.kernel_params_for("visa") is None
