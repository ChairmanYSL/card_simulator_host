"""v2 (CTEL2): an OUT is judged and PERSISTED inside ``_on_out``, and only then acked.

The POS ack therefore means "the verdict is on disk".  These tests pin:

* an already-judged ``msg_id`` is acked ``duplicate`` and never re-judged;
* a persistence failure acks ``ok=false``/``persist_failed`` and leaves the run
  loop waiting (the waiter is NOT resolved);
* an unattributed OUT is quarantined and acked ``quarantined``;
* an OUT without OPS is judged invalid (fail) and never persisted.
"""

from __future__ import annotations

import pytest

from emv_host.cases.models import (
    CardProfile,
    MatchRule,
    OperatorInstructions,
    SuitePolicy,
    TestCase,
    TransactionParams,
)
from emv_host.runner.executor import RunExecutor
from emv_host.runner.session import RunSession
from emv_host.store.run_store import RunStore

CASE_ID = "VISA-CL-001"

OPS_APPROVED = "1000F00030F0FF00"
OPS_DECLINED = "20F0F00030F0FF00"
DATA_RECORD = "8A0230309F270140"


class FakeConn:
    label = "pos@fake"

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, frame: dict) -> None:
        self.sent.append(frame)

    @property
    def last(self) -> dict:
        assert self.sent, "no frame was sent"
        return self.sent[-1]


class FakeProtocol:
    heartbeat_interval = 10.0
    heartbeat_warn = 30.0

    def get_client(self, client_type):  # noqa: ARG002
        return None

    async def send_to(self, client_type, frame):  # noqa: ARG002
        return True

    async def send_to_and_ack(self, client_type, frame, timeout=15.0):  # noqa: ARG002
        return {"ok": True, "detail": ""}

    async def send_to_and_ack_signal(self, client_type, frame, expect_type,
                                     timeout=15.0):  # noqa: ARG002
        return {"signalData": [{"id": "ResponseCode", "value": "YES"}]}


class FakeLibrary:
    def __init__(self, case: TestCase) -> None:
        self.case = case
        self.brands = {"visa": type("S", (), {
            "suite_policy": SuitePolicy(retry_count=0),
            "kernel_params": None})()}

    def get_case(self, brand, mode, case_id):  # noqa: ARG002
        return self.case if case_id == self.case.case_id else None

    def kernel_params_for(self, brand):  # noqa: ARG002
        return None


class FailingStore(RunStore):
    """RunStore whose case-result write always fails (disk full / permissions)."""

    def write_case_result(self, run, case_id, record):  # noqa: ARG002
        raise OSError("disk full")


def make_case() -> TestCase:
    return TestCase(
        case_id=CASE_ID, title="offline approve", brand="visa",
        profile=CardProfile(id=CASE_ID),
        transaction=TransactionParams(
            operator_instructions=OperatorInstructions()),
        expected_results=[MatchRule(path="outcome", op="eq",
                                    expect="approved")])


async def _sink(event, data):  # noqa: ARG001
    pass


def _executor(tmp_path, store=None) -> RunExecutor:
    store = store if store is not None else RunStore(tmp_path)
    return RunExecutor(FakeLibrary(make_case()), FakeProtocol(), store, _sink)


def _session(run: dict, *, attempt_id: str = "att-1",
             waiting: bool = True) -> RunSession:
    session = RunSession(run, "visa", "contactless", [CASE_ID])
    session.begin_attempt(CASE_ID, attempt_id, 0)
    if waiting:
        import asyncio
        session._result_waiter = asyncio.get_running_loop().create_future()
    return session


def out_frame(msg_id="m-1", ops=OPS_APPROVED) -> dict:
    return {
        "type": "out", "msg_id": msg_id,
        "device_id": "pos-1", "app_version": "2.0.0",
        "signalData": [
            {"id": "OPS", "value": ops},
            {"id": "DataRecord", "value": DATA_RECORD},
        ],
    }


@pytest.mark.asyncio
async def test_out_is_persisted_before_ack(tmp_path):
    executor = _executor(tmp_path)
    run = executor.store.new_run("visa", "contactless", [CASE_ID], "single")
    session = _session(run)
    executor._session = session

    conn = FakeConn()
    await executor._on_out(conn, out_frame())

    assert conn.last["type"] == "ack"
    assert conn.last["ok"] is True
    assert conn.last["detail"] == "persisted"
    record = executor.store.get_case_result(run["run_id"], CASE_ID)
    assert record is not None, "the OUT must be on disk before the ack"
    assert record["status"] == "PASS"
    assert record["raw_result"]["outcome"] == "approved"
    assert session._result_waiter.done()
    assert session._result_waiter.result() == "pass"
    assert "m-1" in session.judged_msg_ids


@pytest.mark.asyncio
async def test_failed_assertion_is_persisted_and_fails_run(tmp_path):
    executor = _executor(tmp_path)
    run = executor.store.new_run("visa", "contactless", [CASE_ID], "single")
    session = _session(run)
    executor._session = session

    conn = FakeConn()
    await executor._on_out(conn, out_frame(ops=OPS_DECLINED))

    assert conn.last["detail"] == "persisted"
    record = executor.store.get_case_result(run["run_id"], CASE_ID)
    assert record["status"] == "FAIL"
    assert record["failure_reason"] == "assertion_failed"
    assert session._result_waiter.result() == "fail"


@pytest.mark.asyncio
async def test_persist_failure_acks_ok_false_and_does_not_resolve_waiter(tmp_path):
    executor = _executor(tmp_path, store=FailingStore(tmp_path))
    run = executor.store.new_run("visa", "contactless", [CASE_ID], "single")
    session = _session(run)
    executor._session = session

    conn = FakeConn()
    await executor._on_out(conn, out_frame())

    assert conn.last["ok"] is False
    assert conn.last["detail"] == "persist_failed"
    assert not session._result_waiter.done(), \
        "the run loop must keep waiting for a usable OUT"
    assert "m-1" not in session.judged_msg_ids


@pytest.mark.asyncio
async def test_duplicate_msg_id_is_acked_duplicate(tmp_path):
    executor = _executor(tmp_path)
    run = executor.store.new_run("visa", "contactless", [CASE_ID], "single")
    session = _session(run)
    session.judged_msg_ids.add("m-1")  # already judged in this attempt
    executor._session = session

    conn = FakeConn()
    await executor._on_out(conn, out_frame(msg_id="m-1"))

    assert conn.last["ok"] is True
    assert conn.last["detail"] == "duplicate"
    assert executor.store.get_case_result(run["run_id"], CASE_ID) is None, \
        "a duplicate must not overwrite or re-quarantine the verdict"
    entry = executor.store.run_dir(
        executor.store.root, run["run_id"]) / "quarantine.jsonl"
    assert not entry.exists(), "a duplicate is not quarantine material"


@pytest.mark.asyncio
async def test_unattributed_out_is_quarantined(tmp_path):
    executor = _executor(tmp_path)
    run = executor.store.new_run("visa", "contactless", [CASE_ID], "single")

    # no session at all
    executor._session = None
    conn = FakeConn()
    await executor._on_out(conn, out_frame(msg_id="q-1"))
    assert conn.last["detail"] == "quarantined"
    assert conn.last["ok"] is True

    # session exists but no in-flight attempt waiter
    session = _session(run, waiting=False)
    executor._session = session
    conn = FakeConn()
    await executor._on_out(conn, out_frame(msg_id="q-2"))
    assert conn.last["detail"] == "quarantined"
    assert not session._result_waiter

    qpath = executor.store.run_dir(
        executor.store.root, run["run_id"]) / "quarantine.jsonl"
    assert qpath.is_file()
    assert len(qpath.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.asyncio
async def test_out_without_ops_is_not_persisted(tmp_path):
    executor = _executor(tmp_path)
    run = executor.store.new_run("visa", "contactless", [CASE_ID], "single")
    session = _session(run)
    executor._session = session

    frame = out_frame()
    frame["signalData"] = [{"id": "DataRecord", "value": DATA_RECORD}]
    conn = FakeConn()
    await executor._on_out(conn, frame)

    assert conn.last["detail"] == "out_invalid"
    assert session._result_waiter.result() == "fail"
    assert executor.store.get_case_result(
        run["run_id"], CASE_ID) is None, "an invalid OUT is never persisted"
    qpath = executor.store.run_dir(
        executor.store.root, run["run_id"]) / "quarantine.jsonl"
    assert qpath.is_file()
