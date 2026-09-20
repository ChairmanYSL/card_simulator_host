"""Regression tests pinning three recently-added protocol-v2 host rulings.

These three behaviours were implemented in the working tree without test
coverage; every test here is written so that reverting the behaviour makes it
fail (see the module report / commit message for the temporary-break evidence).

* **D1 empty-table semantics** (``runner/executor.py::RunExecutor._config_frame``
  + ``docs/protocol-v2-design.md`` section 4): ``DRL`` and ``EXCEPTION_FILE``
  are ALWAYS present in ``config.signalData``.  A non-empty list is emitted as
  one item per TLV with the TLV as ``value``; an empty list is emitted as
  exactly one ``{"id": ..., "value": ""}`` item, meaning "clear that table and
  add nothing" (``CONF_01_RemoveCRL``).  A *missing* id would instead mean "do
  not touch that table", so presence of the empty item is the whole point.
  ``AID``/``CAPK`` keep their one-item-per-entry shape and are never replaced
  by a clear item.

* **D2 ``out_no_outcome``** (``executor.py::RunExecutor._judge``): an OUT whose
  OPS status bits are ``0000`` AND whose ``DataRecord`` is empty means "the
  kernel never produced an outcome".  The case is recorded FAIL with
  ``failure_reason == "out_no_outcome"`` and ``resp_code == ""`` -- the host
  must NOT fabricate ``05``/``91``/``80``.  Both halves of the condition
  matter: zero OPS *with* a DataRecord is an ordinary failed assertion.

* **Inbound envelope ``v``** (``protocol/connection.py``): the handshake must
  carry ``v == PROTOCOL_VERSION`` (else ``HandshakeError("bad_frame")`` and the
  server answers an ``error`` frame), and any frame received after the
  handshake whose ``v`` differs warns and drops the connection
  (``ConnectionClosed``) instead of being dispatched to the message sink.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from emv_host.cases.models import (
    AidKernelParam,
    CardProfile,
    DrlParam,
    ExceptionFileParam,
    KernelParams,
    MatchRule,
    OperatorInstructions,
    TestCase,
    TransactionParams,
)
from emv_host.protocol.frame import encode_frame, read_frame
from emv_host.protocol.messages import (
    CONFIG_ID_AID,
    CONFIG_ID_DRL,
    CONFIG_ID_EXCEPTION_FILE,
    MSG_CONFIG,
    MSG_HANDSHAKE,
    MSG_HANDSHAKE_ACK,
    PROTOCOL_VERSION,
    PROTOCOL_VERSION_STRING,
    make_frame,
)
from emv_host.protocol.server import ProtocolServer
from emv_host.runner.executor import RunExecutor
from emv_host.runner.session import RunSession
from emv_host.store.run_store import RunStore

CASE_ID = "VISA-CL-001"

#: concatenated TERM TLV (same shape the kernel-params tests use)
TERM = "9F6604360000809F1A0201569F7A0101"

OPS_APPROVED = "1000F00030F0FF00"
OPS_ZERO = "0000000000000000"  # all-zero OPS -> status_bits == "0000"
DATA_RECORD = "8A0230309F270140"

TOKENS = {"android": "secret-a", "pos": "secret-p"}


# ---------------------------------------------------------------------------
# D1: config.signalData empty-table semantics
# ---------------------------------------------------------------------------


def _config_case() -> TestCase:
    return TestCase(
        case_id=CASE_ID, title="config frame", brand="visa",
        profile=CardProfile(id=CASE_ID),
        transaction=TransactionParams(
            operator_instructions=OperatorInstructions()),
    )


def _values(frame: dict, item_id: str) -> list:
    """Every ``value`` carried by the config items with ``item_id``."""
    return [i.get("value") for i in frame["signalData"] if i.get("id") == item_id]


def test_config_frame_always_emits_drl_and_exception_file_when_empty():
    """D1: empty lists still produce the one ``value=""`` clear item."""
    kernel = KernelParams(aids=[AidKernelParam(aid="A0000000031010")])

    frame = RunExecutor._config_frame(_config_case(), kernel)

    assert frame["type"] == MSG_CONFIG
    # present AND empty -> "clear the table and add nothing"; a missing id
    # would mean "do not touch that table" instead.
    assert CONFIG_ID_DRL in {i["id"] for i in frame["signalData"]}
    assert CONFIG_ID_EXCEPTION_FILE in {i["id"] for i in frame["signalData"]}
    assert _values(frame, CONFIG_ID_DRL) == [""]
    assert _values(frame, CONFIG_ID_EXCEPTION_FILE) == [""]
    # AID keeps its one-item-per-entry shape (never a clear item)
    assert _values(frame, CONFIG_ID_AID) == kernel.payload()["aid"]


def test_config_frame_emits_one_item_per_non_empty_drl_and_exception_entry():
    """D1: non-empty tables -> one item per TLV, the TLV is the ``value``."""
    kernel = KernelParams(
        aids=[AidKernelParam(aid="A0000000031010"),
              AidKernelParam(aid="A0000000041010")],
        drl=[DrlParam(rid="A000000003", index="01", serial="AABBCCDD"),
             DrlParam(rid="A000000003", index="02", serial="11223344")],
        exception_file=[ExceptionFileParam(pan="4761739001010010",
                                          pan_sequence="01")],
        term=TERM)

    frame = RunExecutor._config_frame(_config_case(), kernel)

    drl = _values(frame, CONFIG_ID_DRL)
    exception_file = _values(frame, CONFIG_ID_EXCEPTION_FILE)
    # one item per entry, each carrying that entry's concatenated BER-TLV
    assert len(drl) == 2
    assert drl == kernel.payload()["drl"]
    assert drl[0].startswith("9F0605A0000000038F0101")
    assert "DF810504AABBCCDD" in drl[0]
    assert exception_file == kernel.payload()["exception_file"]
    assert exception_file[0] == "5A0847617390010100105F340101"
    # no clear item leaks in when the table is non-empty
    assert "" not in drl
    assert "" not in exception_file
    # AID stays one item per entry as well
    assert _values(frame, CONFIG_ID_AID) == kernel.payload()["aid"]


def test_config_frame_mixed_empty_table_still_emits_its_clear_item():
    """D1: the two tables are independent -- one may be cleared while the
    other is replaced with real entries."""
    kernel = KernelParams(
        exception_file=[ExceptionFileParam(pan="4761739001010010",
                                          pan_sequence="01")],
        term=TERM)

    frame = RunExecutor._config_frame(_config_case(), kernel)

    assert _values(frame, CONFIG_ID_DRL) == [""]
    assert _values(frame, CONFIG_ID_EXCEPTION_FILE) == \
        kernel.payload()["exception_file"]
    assert _values(frame, CONFIG_ID_EXCEPTION_FILE)[0] != ""
    assert _values(frame, CONFIG_ID_AID) == []


# ---------------------------------------------------------------------------
# D2: an OUT from a kernel that produced no outcome
# ---------------------------------------------------------------------------


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

    def get_case(self, brand, mode, case_id):  # noqa: ARG002
        return self.case if case_id == self.case.case_id else None

    def kernel_params_for(self, brand):  # noqa: ARG002
        return None


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


def _executor(tmp_path) -> RunExecutor:
    return RunExecutor(FakeLibrary(make_case()), FakeProtocol(),
                       RunStore(tmp_path), _sink)


def _session(run: dict) -> RunSession:
    session = RunSession(run, "visa", "contactless", [CASE_ID])
    session.begin_attempt(CASE_ID, "att-1", 0)
    session._result_waiter = asyncio.get_running_loop().create_future()
    return session


def out_frame(msg_id="m-1", ops=OPS_APPROVED, datarecord=DATA_RECORD) -> dict:
    """One CTEL2 OUT; ``datarecord=None`` omits the item entirely."""
    signal = [{"id": "OPS", "value": ops}]
    if datarecord is not None:
        signal.append({"id": "DataRecord", "value": datarecord})
    return {
        "type": "out", "msg_id": msg_id,
        "device_id": "pos-1", "app_version": "2.0.0",
        "signalData": signal,
    }


async def _judge_one(tmp_path, ops: str, datarecord):
    executor = _executor(tmp_path)
    run = executor.store.new_run("visa", "contactless", [CASE_ID], "single")
    session = _session(run)
    executor._session = session

    conn = FakeConn()
    await executor._on_out(conn, out_frame(ops=ops, datarecord=datarecord))

    record = executor.store.get_case_result(run["run_id"], CASE_ID)
    assert record is not None, "the OUT must have been persisted"
    return session, record, conn


@pytest.mark.asyncio
async def test_out_with_zero_ops_and_empty_datarecord_fails_out_no_outcome(tmp_path):
    """D2 main path: OPS 0000 + empty DataRecord -> FAIL/out_no_outcome, no
    fabricated response code."""
    for empty_datarecord in ("", None):  # present-but-empty and omitted
        session, record, conn = await _judge_one(tmp_path, OPS_ZERO,
                                                 empty_datarecord)

        assert conn.last["ok"] is True
        assert conn.last["detail"] == "persisted"
        assert record["status"] == "FAIL"
        assert record["failure_reason"] == "out_no_outcome"
        # the decoding premise of the ruling (see judge/outcome.py)
        assert record["raw_result"]["out"]["ops"]["status_bits"] == "0000"
        assert record["raw_result"]["out"]["ops"]["status"] == "RFU(0000)"
        assert record["raw_result"]["outcome"] == "rfu(0000)"
        assert record["raw_result"]["out"]["datarecord"] == {}
        # the host must NOT fabricate 05 / 91 / 80
        assert record["raw_result"]["resp_code"] == ""
        assert record["raw_result"]["resp_code"] not in {"05", "91", "80"}
        # ...and the run records why the case failed
        assert session.failure_reason == "out_no_outcome"
        assert session._result_waiter.result() == "fail"


@pytest.mark.asyncio
async def test_zero_ops_with_datarecord_is_not_out_no_outcome(tmp_path):
    """D2 boundary: zero OPS *plus* a DataRecord is an ordinary failed
    assertion -- only the empty DataRecord makes it ``out_no_outcome``."""
    session, record, _ = await _judge_one(tmp_path, OPS_ZERO, DATA_RECORD)

    assert record["status"] == "FAIL"
    assert record["failure_reason"] == "assertion_failed"
    assert record["raw_result"]["outcome"] == "rfu(0000)"
    assert record["raw_result"]["resp_code"] == "3030"
    assert session.failure_reason == "assertion_failed"


@pytest.mark.asyncio
async def test_normal_approved_out_still_passes(tmp_path):
    """Contrast: a real approval OUT still PASSes (so the D2 tests above
    cannot pass vacuously by failing everything)."""
    session, record, conn = await _judge_one(tmp_path, OPS_APPROVED, DATA_RECORD)

    assert conn.last["detail"] == "persisted"
    assert record["status"] == "PASS"
    assert "failure_reason" not in record
    assert record["raw_result"]["outcome"] == "approved"
    assert session.failure_reason is None
    assert session._result_waiter.result() == "pass"


# ---------------------------------------------------------------------------
# Inbound envelope "v" validation
# ---------------------------------------------------------------------------


class Sink:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.messages: list[dict] = []

    async def __call__(self, event, conn, frame):  # noqa: ARG002
        self.events.append(event)
        if event == "message":
            self.messages.append(frame)


async def _server(port: int, sink: Sink) -> ProtocolServer:
    server = ProtocolServer(TOKENS, sink, heartbeat_interval=0.5,
                            heartbeat_warn=2.0, disconnect_timeout=3.0)
    await server.start("127.0.0.1", port)
    return server


async def connect_client(port: int, client_type: str, token: str,
                         device_id: str = "t") -> tuple:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    handshake = make_frame(MSG_HANDSHAKE, client_type=client_type, token=token,
                           device_id=device_id,
                           protocol_version=PROTOCOL_VERSION_STRING)
    writer.write(encode_frame(handshake))
    await writer.drain()
    ack = await read_frame(reader)
    return reader, writer, ack


@pytest.mark.asyncio
async def test_handshake_with_wrong_envelope_v_is_rejected(unused_tcp_port):
    """A handshake whose envelope ``v`` is not PROTOCOL_VERSION is refused with
    ``bad_frame``; the same handshake with the right ``v`` is accepted."""
    sink = Sink()
    server = await _server(unused_tcp_port, sink)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1",
                                                       unused_tcp_port)
        handshake = make_frame(
            MSG_HANDSHAKE, client_type="pos", token="secret-p", device_id="t",
            protocol_version=PROTOCOL_VERSION_STRING)
        handshake["v"] = PROTOCOL_VERSION - 1
        writer.write(encode_frame(handshake))
        await writer.drain()

        ack = await asyncio.wait_for(read_frame(reader), timeout=3)
        assert ack["type"] == "error"
        assert ack["code"] == "bad_frame"
        assert "envelope v" in ack["message"]
        writer.close()
        await asyncio.sleep(0.2)
        assert server.get_client("pos") is None

        # non-vacuous: the identical handshake with v == 2 is accepted
        _, writer2, ack2 = await connect_client(unused_tcp_port, "pos",
                                                "secret-p", device_id="t")
        assert ack2["type"] == MSG_HANDSHAKE_ACK
        writer2.close()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_post_handshake_wrong_envelope_v_drops_connection(
        unused_tcp_port, caplog):
    """After a good handshake, a frame with the wrong ``v`` warns and drops the
    connection instead of reaching the message sink."""
    sink = Sink()
    server = await _server(unused_tcp_port, sink)
    try:
        _, writer, ack = await connect_client(unused_tcp_port, "pos", "secret-p")
        assert ack["type"] == MSG_HANDSHAKE_ACK

        # control: a correctly-enveloped frame IS dispatched to the sink
        writer.write(encode_frame(make_frame("log", level="info",
                                             message="good-envelope")))
        await writer.drain()
        await asyncio.sleep(0.2)
        assert any(m.get("message") == "good-envelope" for m in sink.messages)
        assert server.get_client("pos") is not None

        bad = make_frame("log", level="info", message="bad-envelope")
        bad["v"] = PROTOCOL_VERSION + 1
        caplog.set_level(logging.WARNING, logger="emv_host.protocol")
        writer.write(encode_frame(bad))
        await writer.drain()
        await asyncio.sleep(0.3)

        assert server.get_client("pos") is None, \
            "a bad-envelope frame must drop the connection"
        assert not any(m.get("message") == "bad-envelope" for m in sink.messages)
        assert any("bad envelope v=3" in r.getMessage() for r in caplog.records), \
            "the drop must be logged with the offending v"
        writer.close()
    finally:
        await server.stop()
