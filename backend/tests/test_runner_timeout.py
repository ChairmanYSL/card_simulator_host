"""D4/D5/Q9: the run loop's failure semantics, under protocol v2 (CTEL2).

Every host->device command needs an ack.  A timeout, a rejection, or a
disconnected peer FAILs the current case with a named ``failure_reason``; a FAIL
stops the whole run and every case still queued is persisted as SKIPPED with
``note=run_stopped_after_failure:<case>``.

v2 difference (design section 10): ``session_abort`` was REMOVED, so an aborted
run is no longer announced on the wire — the run-level ``abort_reason`` and the
per-case ``failure_reason`` are the whole record.  The POS command path is the
CTEL2 signal set (``test_info`` -> ``config`` -> ``act``, each answered by a
``*_ack`` carrying ``signalData[ResponseCode]``); the result wait is now
``out_timeout`` instead of ``result_timeout``.

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
from emv_host.protocol.connection import ConnectionClosed
from emv_host.protocol.messages import (
    CONFIG_ID_AID,
    CONFIG_ID_TERM,
    MSG_ACT,
    MSG_CONFIG,
    MSG_TEST_INFO,
)
from emv_host.runner.executor import RunExecutor
from emv_host.runner.session import RunSession
from emv_host.store.run_store import RunStore

#: concatenated TERM TLV used by the kernel-params tests
TERM = "9F6604360000809F1A0201569F7A0101"

#: v1.2 POS frames removed by protocol v2 (design section 10)
LEGACY_POS_FRAMES = frozenset({
    "session_abort", "case_started", "transaction_result",
    "pos_config", "pos_kernel_config", "device_config",
})


async def _sink(event, data):  # noqa: ARG001
    pass


def signal_ack(code: str = "YES") -> dict:
    """A CTEL2 ``*_ack`` as the POS sends it (design section 2.2)."""
    return {"signalData": [{"id": "ResponseCode", "value": code}]}


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
    """A device that acks everything (with an optional per-type override).

    ``reject`` is keyed by the frame's ``config_type`` (card link) or ``type``
    (CTEL2 signals); the override value is a full ack frame, so a v1.2-style
    ``{"ok": False}`` and a v2 ``signal_ack("NO")`` both work.
    """

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

    async def send_and_wait_signal(self, frame, expect_type, timeout=15.0,  # noqa: ARG002
                                   event=None):
        key = frame.get("type")
        self.seen.append(key)
        self.sent.append(frame)
        return self.reject.get(key, signal_ack("YES"))


class SilentConn:
    """A device that never acks (ack timeout)."""

    def __init__(self, label: str, exc: Exception | None = None):
        self.label = label
        self.exc = exc or TimeoutError("no ack")

    async def send(self, frame: dict) -> None:
        pass

    async def send_and_wait_ack(self, frame, timeout=15.0, event=None):  # noqa: ARG002
        raise self.exc

    async def send_and_wait_signal(self, frame, expect_type, timeout=15.0,  # noqa: ARG002
                                   event=None):
        # the real transport turns a *_ack timeout into ConnectionClosed
        # (protocol/connection.py:120), which is the branch under test
        raise ConnectionClosed(f"{self.label}: {expect_type} timeout")


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

    async def send_to_and_ack_signal(self, client_type, frame, expect_type,
                                     timeout=15.0):
        """Mirror protocol/server.py:148 — the v2 POS signal request."""
        conn = self.get_client(client_type)
        if conn is None:
            return None
        return await conn.send_and_wait_signal(frame, expect_type,
                                               timeout=timeout)


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
    # v2: the POS never produced an OUT -> out_timeout (was result_timeout)
    assert first["failure_reason"] == "out_timeout"
    assert run["status"] == "ABORTED"
    assert run["abort_reason"] == "case_failed:C-01:out_timeout"
    # the POS really was driven (test_info + act; no kernel params -> no config)
    assert pos.seen == [MSG_TEST_INFO, MSG_ACT]

    for cid in ("C-02", "C-03"):
        skipped = executor.store.get_case_result(run["run_id"], cid)
        assert skipped["status"] == "SKIPPED"
        assert skipped["note"] == "run_stopped_after_failure:C-01"

    # v2 (design section 10) removed session_abort: the abort is now only
    # recorded host-side, so no v1.2 frame may reach either device any more.
    assert not [f for f in pos.sent if f.get("type") in LEGACY_POS_FRAMES]
    assert not [f for f in android.sent if f.get("type") in LEGACY_POS_FRAMES]
    # the card was emulating, so it is still parked (H2 semantics under v2)
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
    """v2: the POS-side CONFIG (kernel AID/CAPK/TERM) rejection FAILs the case."""
    cases = {"C-01": make_case("C-01")}
    kernel = KernelParams(
        aids=[AidKernelParam(aid="A0000000031010")], term=TERM)
    pos = AckingConn("pos@stub", reject={MSG_CONFIG: signal_ack("NO")})
    executor = build(tmp_path, cases, pos=pos, kernel=kernel)
    session = await run_cases(executor, ["C-01"], monkeypatch)

    record = executor.store.get_case_result(session.run["run_id"], "C-01")
    assert record["status"] == "FAIL"
    assert record["failure_reason"] == "config_rejected"
    assert MSG_ACT not in pos.seen, "no point acting on a config we could not load"


@pytest.mark.asyncio
async def test_case_started_rejection_fails_case(tmp_path, monkeypatch):
    """v2: ``case_started`` is gone; the ACT that starts the transaction replaces
    it, so a POS rejection of that signal FAILs with ``act_rejected``."""
    cases = {"C-01": make_case("C-01")}
    pos = AckingConn("pos@stub", reject={MSG_ACT: signal_ack("NO")})
    executor = build(tmp_path, cases, pos=pos)
    session = await run_cases(executor, ["C-01"], monkeypatch)

    record = executor.store.get_case_result(session.run["run_id"], "C-01")
    assert record["failure_reason"] == "act_rejected"


@pytest.mark.asyncio
async def test_pos_case_started_ack_timeout_fails_case(tmp_path, monkeypatch):
    """v2: the POS acks test_info but never the ACT -> ``act_ack_timeout``."""
    cases = {"C-01": make_case("C-01")}

    class PosAcksEverythingButAct(AckingConn):
        async def send_and_wait_signal(self, frame, expect_type,  # noqa: ARG002
                                       timeout=15.0, event=None):
            if frame.get("type") == MSG_ACT:
                # the real transport raises ConnectionClosed on a signal timeout
                raise ConnectionClosed("pos@stub: act_ack timeout")
            return await super().send_and_wait_signal(
                frame, expect_type, timeout=timeout)

    executor = build(tmp_path, cases, pos=PosAcksEverythingButAct("pos@stub"))
    session = await run_cases(executor, ["C-01"], monkeypatch)

    record = executor.store.get_case_result(session.run["run_id"], "C-01")
    assert record["failure_reason"] == "act_ack_timeout"


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
    """R5 under v2: the kernel CONFIG precedes the ACT it configures, and its
    rejection fails the case."""
    kernel = KernelParams(
        aids=[AidKernelParam(aid="A0000000031010",
                             fields=[TlvField(tag="DF01", value="01")])],
        term="9F6604360000809F1A0201569F7A0101")
    cases = {"C-01": make_case("C-01", pos_params=True)}
    pos = AckingConn("pos@stub")
    executor = build(tmp_path, cases, pos=pos, kernel=kernel)
    session = await run_cases(executor, ["C-01"], monkeypatch)

    # v2 command order: test_info -> config (kernel params) -> act
    assert pos.seen == [MSG_TEST_INFO, MSG_CONFIG, MSG_ACT]
    assert pos.seen.index(MSG_CONFIG) < pos.seen.index(MSG_ACT)
    frame = next(f for f in pos.sent if f.get("type") == MSG_CONFIG)
    aid = next(i["value"] for i in frame["signalData"] if i["id"] == CONFIG_ID_AID)
    assert aid.startswith("9F06")
    assert next(i["value"] for i in frame["signalData"]
                if i["id"] == CONFIG_ID_TERM) == kernel.term
    record = executor.store.get_case_result(session.run["run_id"], "C-01")
    assert record["failure_reason"] == "out_timeout"  # reached the wait stage


@pytest.mark.asyncio
async def test_kernel_config_rejection_fails_case(tmp_path, monkeypatch):
    kernel = KernelParams(
        aids=[AidKernelParam(aid="A0000000031010")],
        term="9F6604360000809F1A0201569F7A0101")
    cases = {"C-01": make_case("C-01")}
    pos = AckingConn("pos@stub", reject={MSG_CONFIG: signal_ack("NO")})
    executor = build(tmp_path, cases, pos=pos, kernel=kernel)
    session = await run_cases(executor, ["C-01"], monkeypatch)

    record = executor.store.get_case_result(session.run["run_id"], "C-01")
    assert record["status"] == "FAIL"
    assert record["failure_reason"] == "config_rejected"
    assert MSG_ACT not in pos.seen, "no point starting a case we cannot run"


@pytest.mark.asyncio
async def test_no_kernel_params_means_no_push(tmp_path, monkeypatch):
    cases = {"C-01": make_case("C-01", pos_params=True)}
    pos = AckingConn("pos@stub")
    executor = build(tmp_path, cases, pos=pos, kernel=None)
    await run_cases(executor, ["C-01"], monkeypatch)

    assert MSG_CONFIG not in pos.seen


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

    frame = next(f for f in pos.sent if f.get("type") == MSG_CONFIG)
    aid = next(i["value"] for i in frame["signalData"] if i["id"] == CONFIG_ID_AID)
    assert "A0000000041010" in aid
