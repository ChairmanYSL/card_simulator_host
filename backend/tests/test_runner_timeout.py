"""v1.2 D4/D5/Q9: the run loop's failure semantics.

Every host->device command needs an ack.  A timeout, a rejection, or a
disconnected peer FAILs the current case with a named ``failure_reason``; a FAIL
stops the whole run and every case still queued is persisted as SKIPPED with
``note=run_stopped_after_failure:<case>``; both sides then receive
``session_abort``.

The stubs here are deliberately tiny: they replace the devices, not the logic
under test (``_run_loop_body``, ``_attempt``, ``_fail``, ``_mark_remaining_skipped``).
"""

from __future__ import annotations

import asyncio

import pytest

from emv_host.cases.models import (
    AidKernelParam,
    CardProfile,
    KernelParams,
    OperatorInstructions,
    PosParam,
    SuitePolicy,
    TestCase,
    TlvField,
    TransactionParams,
)
from emv_host.runner.executor import RunExecutor
from emv_host.runner.session import RunSession
from emv_host.store.run_store import RunStore


async def _sink(event, data):  # noqa: ARG001
    pass


def make_case(case_id: str, *, timeout: int = 10,
              pos_params: bool = False) -> TestCase:
    params = [PosParam(tag="9F02", name="amount", value="100.00")] \
        if pos_params else []
    return TestCase(
        case_id=case_id, title=case_id, brand="visa",
        profile=CardProfile(id=case_id),
        transaction=TransactionParams(
            operator_instructions=OperatorInstructions(), pos_params=params),
        timeout_seconds=timeout)


class StubLibrary:
    def __init__(self, cases: dict[str, TestCase],
                 kernel: KernelParams | None = None):
        self.cases = cases
        self._kernel = kernel
        self.brands = {"visa": type("S", (), {
            "suite_policy": SuitePolicy(retry_count=0),
            "kernel_params": kernel})()}

    def get_case(self, brand, mode, case_id):  # noqa: ARG002
        return self.cases.get(case_id)

    def kernel_params_for(self, brand):  # noqa: ARG002
        """Mirror CaseLibrary.kernel_params_for (the executor's lookup point)."""
        return self._kernel


class AckingConn:
    """A device that acks everything (with an optional per-type override)."""

    def __init__(self, label: str, *, reject: dict[str, dict] | None = None,
                 seen: list[str] | None = None):
        self.label = label
        self.reject = reject or {}
        self.seen = seen if seen is not None else []
        self.sent: list[dict] = []

    async def send(self, frame: dict) -> None:
        self.sent.append(frame)

    async def send_and_wait_ack(self, frame, timeout=15.0, event=None):  # noqa: ARG002
        key = frame.get("config_type") or frame.get("type")
        self.seen.append(key)
        self.sent.append(frame)
        return self.reject.get(key, {"ok": True, "detail": ""})


class SilentConn:
    """A device that never acks (ack timeout)."""

    def __init__(self, label: str, exc: Exception | None = None):
        self.label = label
        self.exc = exc or TimeoutError("no ack")

    async def send(self, frame: dict) -> None:
        pass

    async def send_and_wait_ack(self, frame, timeout=15.0, event=None):  # noqa: ARG002
        raise self.exc


class StubProtocol:
    heartbeat_interval = 10.0
    heartbeat_warn = 30.0

    def __init__(self, android=None, pos=None):
        self.android = android
        self.pos = pos

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


_DEFAULT = object()


def build(tmp_path, cases, *, android=_DEFAULT, pos=_DEFAULT,
          kernel=None, neutralise_emulation=True) -> RunExecutor:
    """Wire the run loop to stubs.

    Passing ``android=None`` / ``pos=None`` means "that device is not
    connected" — the default (``_DEFAULT``) gives it a fake that acks
    everything.
    """
    protocol = StubProtocol(
        android=AckingConn("android@stub") if android is _DEFAULT else android,
        pos=AckingConn("pos@stub") if pos is _DEFAULT else pos)
    executor = RunExecutor(StubLibrary(cases, kernel), protocol,
                           RunStore(tmp_path), _sink)
    if neutralise_emulation:
        # the stubs answer start_emulation but do not emit a status frame;
        # readiness itself is covered by the protocol tests
        async def instant(session):  # noqa: ARG001
            return None
        executor._wait_emulation = instant
    return executor


async def run_cases(executor: RunExecutor, case_ids: list[str],
                    monkeypatch: pytest.MonkeyPatch) -> RunSession:
    """Start the run loop with the per-case result wait shortened.

    ``TestCase.timeout_seconds`` has a 10s floor; the real wait is patched down
    so the timeout branch is exercised quickly while every other code path runs
    unchanged.
    """
    real_wait = asyncio.wait

    async def fast_wait(fs, *, timeout=None, return_when=None):
        return await real_wait(fs, timeout=0.15, return_when=return_when)

    monkeypatch.setattr(asyncio, "wait", fast_wait)
    run = executor.store.new_run("visa", "contactless", case_ids, "full")
    session = RunSession(run, "visa", "contactless", case_ids)
    await executor._run_loop(session)
    return session


@pytest.mark.asyncio
async def test_result_timeout_fails_case_stops_run_and_skips_remainder(
        tmp_path, monkeypatch):
    cases = {cid: make_case(cid) for cid in ("C-01", "C-02", "C-03")}
    android, pos = AckingConn("android@stub"), AckingConn("pos@stub")
    executor = build(tmp_path, cases, android=android, pos=pos)
    session = await run_cases(executor, list(cases), monkeypatch)

    run = executor.store.get_run(session.run["run_id"])
    first = executor.store.get_case_result(run["run_id"], "C-01")
    assert first["status"] == "FAIL"
    assert first["failure_reason"] == "result_timeout"
    assert run["status"] == "ABORTED"
    assert run["abort_reason"] == "case_failed:C-01:result_timeout"

    for cid in ("C-02", "C-03"):
        skipped = executor.store.get_case_result(run["run_id"], cid)
        assert skipped["status"] == "SKIPPED"
        assert skipped["note"] == "run_stopped_after_failure:C-01"

    abort_frames = [f for f in pos.sent if f.get("type") == "session_abort"]
    assert abort_frames, "the POS must be told the session was aborted"
    assert abort_frames[-1]["reason"] == "result_timeout"
    assert any(f.get("type") == "session_abort" for f in android.sent)
    assert any(f.get("type") == "stop_emulation" for f in android.sent)


@pytest.mark.asyncio
async def test_card_config_rejection_fails_case(tmp_path, monkeypatch):
    cases = {"C-01": make_case("C-01")}
    android = AckingConn("android@stub",
                         reject={"card_config": {"ok": False,
                                                 "detail": "bad profile"}})
    executor = build(tmp_path, cases, android=android)
    session = await run_cases(executor, ["C-01"], monkeypatch)

    record = executor.store.get_case_result(session.run["run_id"], "C-01")
    assert record["status"] == "FAIL"
    assert record["failure_reason"] == "card_config_rejected"


@pytest.mark.asyncio
async def test_card_ack_timeout_fails_case(tmp_path, monkeypatch):
    cases = {"C-01": make_case("C-01")}
    executor = build(tmp_path, cases, android=SilentConn("android@stub"))
    session = await run_cases(executor, ["C-01"], monkeypatch)

    record = executor.store.get_case_result(session.run["run_id"], "C-01")
    assert record["status"] == "FAIL"
    assert record["failure_reason"] == "card_config_ack_timeout"


@pytest.mark.asyncio
async def test_android_disconnected_fails_case(tmp_path, monkeypatch):
    cases = {"C-01": make_case("C-01")}
    executor = build(tmp_path, cases, android=None)
    session = await run_cases(executor, ["C-01"], monkeypatch)

    run = executor.store.get_run(session.run["run_id"])
    record = executor.store.get_case_result(run["run_id"], "C-01")
    assert record["status"] == "FAIL"
    assert record["failure_reason"] == "android_not_connected"
    assert run["status"] == "ABORTED"


@pytest.mark.asyncio
async def test_pos_offline_fails_case_instead_of_warning(tmp_path, monkeypatch):
    """Q8: an offline POS used to be warn-only; it is a FAIL in v1.2."""
    cases = {"C-01": make_case("C-01", pos_params=True)}
    executor = build(tmp_path, cases, pos=None)
    session = await run_cases(executor, ["C-01"], monkeypatch)

    run = executor.store.get_run(session.run["run_id"])
    record = executor.store.get_case_result(run["run_id"], "C-01")
    assert record["status"] == "FAIL"
    assert record["failure_reason"] == "pos_not_connected"
    assert run["status"] == "ABORTED"
    assert run["abort_reason"] == "case_failed:C-01:pos_not_connected"


@pytest.mark.asyncio
async def test_pos_config_rejection_fails_case(tmp_path, monkeypatch):
    cases = {"C-01": make_case("C-01", pos_params=True)}
    pos = AckingConn("pos@stub",
                     reject={"pos_config": {"ok": False, "detail": "no"}})
    executor = build(tmp_path, cases, pos=pos)
    session = await run_cases(executor, ["C-01"], monkeypatch)

    record = executor.store.get_case_result(session.run["run_id"], "C-01")
    assert record["failure_reason"] == "pos_config_rejected"


@pytest.mark.asyncio
async def test_case_started_rejection_fails_case(tmp_path, monkeypatch):
    cases = {"C-01": make_case("C-01")}
    pos = AckingConn("pos@stub",
                     reject={"case_started": {"ok": False, "detail": "busy"}})
    executor = build(tmp_path, cases, pos=pos)
    session = await run_cases(executor, ["C-01"], monkeypatch)

    record = executor.store.get_case_result(session.run["run_id"], "C-01")
    assert record["failure_reason"] == "pos_case_started_rejected"


@pytest.mark.asyncio
async def test_pos_case_started_ack_timeout_fails_case(tmp_path, monkeypatch):
    cases = {"C-01": make_case("C-01")}

    class PosAcksConfigOnly(AckingConn):
        async def send_and_wait_ack(self, frame, timeout=15.0, event=None):  # noqa: ARG002
            if frame.get("type") == "case_started":
                raise TimeoutError("no ack")
            return await super().send_and_wait_ack(frame, timeout=timeout)

    executor = build(tmp_path, cases, pos=PosAcksConfigOnly("pos@stub"))
    session = await run_cases(executor, ["C-01"], monkeypatch)

    record = executor.store.get_case_result(session.run["run_id"], "C-01")
    assert record["failure_reason"] == "pos_case_started_ack_timeout"


@pytest.mark.asyncio
async def test_android_error_keeps_status_error_and_stops_run(tmp_path, monkeypatch):
    """D5: an android status:error is an ERROR case with its structured error."""
    cases = {"C-01": make_case("C-01"), "C-02": make_case("C-02")}
    executor = build(tmp_path, cases)
    run = executor.store.new_run("visa", "contactless", ["C-01", "C-02"], "full")
    session = RunSession(run, "visa", "contactless", ["C-01", "C-02"])
    session.begin_attempt("C-01", "att-1", 0)
    executor._session = session

    class Conn:
        label = "android@stub"
        async def send(self, frame):  # noqa: ARG002
            pass

    session.failure_reason = None
    await executor._on_android_status(Conn(), {
        "type": "status", "state": "error", "case_id": "C-01",
        "attempt_id": "att-1",
        "detail": {"code": "hce_failed", "message": "HCE service died"},
    })

    assert session.failure_reason == "android_error"
    assert session.abort_event.is_set()
    stored = run["results"]["C-01"]["android_error"]
    assert stored == {"code": "hce_failed", "message": "HCE service died"}


@pytest.mark.asyncio
async def test_pos_disconnect_during_attempt_fails_case():
    """Q7: losing the POS mid-case fails the case (was warn-only)."""
    executor = RunExecutor(StubLibrary({}), StubProtocol(), None, _sink)
    run = {"run_id": "r1", "status": "RUNNING", "results": {}}
    session = RunSession(run, "visa", "contactless", ["C-01"])
    session.begin_attempt("C-01", "att-1", 0)
    executor._session = session
    executor._task = None

    class BusyTask:
        def done(self):
            return False

    executor._task = BusyTask()
    executor._devices = {"pos": {"connected": True, "session_id": "s1"}}

    class Conn:
        client_type = "pos"
        label = "pos@stub"
        session_id = "s1"
        device_id = "pos-1"

    await executor.on_device_event("disconnected", Conn(), None)
    assert session.failure_reason == "pos_disconnected"
    assert session.fatal_reason == "case_failed:C-01:pos_disconnected"


@pytest.mark.asyncio
async def test_kernel_config_is_pushed_before_pos_config(tmp_path, monkeypatch):
    """R5: pos_kernel_config precedes pos_config and its rejection fails the case."""
    kernel = KernelParams(
        aids=[AidKernelParam(aid="A0000000031010",
                             fields=[TlvField(tag="DF01", value="01")])],
        term="9F6604360000809F1A0201569F7A0101")
    cases = {"C-01": make_case("C-01", pos_params=True)}
    pos = AckingConn("pos@stub")
    executor = build(tmp_path, cases, pos=pos, kernel=kernel)
    session = await run_cases(executor, ["C-01"], monkeypatch)

    assert pos.seen[0] == "pos_kernel_config"
    assert "pos_config" in pos.seen
    assert pos.seen.index("pos_kernel_config") < pos.seen.index("pos_config")
    frame = next(f for f in pos.sent
                 if f.get("config_type") == "pos_kernel_config")
    assert frame["kernel_params"]["aid"][0].startswith("9F06")
    assert frame["kernel_params"]["term"] == kernel.term
    record = executor.store.get_case_result(session.run["run_id"], "C-01")
    assert record["failure_reason"] == "result_timeout"  # reached the wait stage


@pytest.mark.asyncio
async def test_kernel_config_rejection_fails_case(tmp_path, monkeypatch):
    kernel = KernelParams(
        aids=[AidKernelParam(aid="A0000000031010")],
        term="9F6604360000809F1A0201569F7A0101")
    cases = {"C-01": make_case("C-01")}
    pos = AckingConn("pos@stub", reject={
        "pos_kernel_config": {"ok": False, "detail": "kernel busy"}})
    executor = build(tmp_path, cases, pos=pos, kernel=kernel)
    session = await run_cases(executor, ["C-01"], monkeypatch)

    record = executor.store.get_case_result(session.run["run_id"], "C-01")
    assert record["status"] == "FAIL"
    assert record["failure_reason"] == "pos_kernel_config_apply_failed"
    assert "case_started" not in pos.seen, "no point starting a case we cannot run"


@pytest.mark.asyncio
async def test_no_kernel_params_means_no_push(tmp_path, monkeypatch):
    cases = {"C-01": make_case("C-01", pos_params=True)}
    pos = AckingConn("pos@stub")
    executor = build(tmp_path, cases, pos=pos, kernel=None)
    await run_cases(executor, ["C-01"], monkeypatch)

    assert "pos_kernel_config" not in pos.seen


@pytest.mark.asyncio
async def test_case_level_kernel_params_override_brand(tmp_path, monkeypatch):
    brand_kernel = KernelParams(
        aids=[AidKernelParam(aid="A0000000031010")],
        term="9F6604360000809F1A0201569F7A0101")
    case = make_case("C-01")
    case.kernel_params = KernelParams(
        aids=[AidKernelParam(aid="A0000000041010")],
        term="9F6604360000809F1A0201569F7A0101")
    pos = AckingConn("pos@stub")
    executor = build(tmp_path, {"C-01": case}, pos=pos, kernel=brand_kernel)
    await run_cases(executor, ["C-01"], monkeypatch)

    frame = next(f for f in pos.sent
                 if f.get("config_type") == "pos_kernel_config")
    assert "A0000000041010" in frame["kernel_params"]["aid"][0]
