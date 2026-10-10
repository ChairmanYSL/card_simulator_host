"""Protocol v2 dual tap: one ACT can answer with several OUTs.

CTEL2 (Appendix D) allows several OUT signals per ACT -- a dual tap, or Select
Next followed by the next application's transaction.  The host therefore:

* decodes and persists EVERY OUT of the attempt (``raw_result.outs`` in arrival
  order, ``raw_result.out`` = the transaction's final OUT);
* finishes the attempt only on a **final** OUT: ``judge.outcome.is_final_out``
  says Try Again / Select Next are intermediate, so the operator can re-tap and
  the reader answers on the same ACT;
* settles the case verdict on that final OUT only, and, when the final OUT never
  arrives, ends the attempt with ``out_timeout`` while the intermediate OUT stays
  in the record (``pending_final_out``).

The case under test is ``cases/mastercard/contactless/cases/3B04-0020.json``
(CTEL2 test 3B04-0020: the card stays silent on SELECT ADF), whose OUT values are
taken from the KSAPI log ``Log/3B04-0020.txt``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from emv_host.cases.models import (
    CardProfile,
    MatchRule,
    OperatorInstructions,
    TestCase,
    TransactionParams,
)
from emv_host.judge.asserter import assert_result
from emv_host.judge.outcome import (
    INTERMEDIATE_STATUSES,
    build_result,
    compose_result,
    is_final_out,
)
from emv_host.runner.executor import RunExecutor
from emv_host.runner.session import RunSession
from emv_host.store.run_store import RunStore

REPO = Path(__file__).resolve().parents[2]
CASE_PATH = REPO / "cases" / "mastercard" / "contactless" / "cases" / "3B04-0020.json"

CASE_ID = "3B04-0020"

#: KSAPI log 3B04-0020: first tap (card silent on SELECT ADF) -> Try Again.
OPS_TRY_AGAIN = "7010F0F010F0FF00"
DISC_TRY_AGAIN = "DF8115060100000000FF"
#: ... and the re-tap: GENERATE AC ARQC -> ONLINE REQUEST.
OPS_ONLINE = "30F0F010B8F0FF00"
DISC_ONLINE = "DF8115060000000000FF"
DATA_RECORD_ONLINE = "9F2608412F1812D37745799F270180"


def _case(rules: list[MatchRule]) -> TestCase:
    return TestCase(
        case_id=CASE_ID, title="dual tap", brand="mastercard",
        profile=CardProfile(id=CASE_ID),
        transaction=TransactionParams(
            operator_instructions=OperatorInstructions()),
        expected_results=rules)


def _executor(tmp_path) -> tuple[RunExecutor, RunStore, RunSession]:
    store = RunStore(tmp_path / "runs")
    executor = RunExecutor(None, None, store, _sink)  # type: ignore[arg-type]
    run = store.new_run("mastercard", "contactless", [CASE_ID], "single")
    session = RunSession(run, "mastercard", "contactless", [CASE_ID])
    session.begin_attempt(CASE_ID, "att-1", 0)
    executor._session = session
    return executor, store, session


async def _sink(event, data):  # noqa: ARG001
    pass


def _out(ops: str, datarecord: str = "", discdata: str = "") -> list[dict]:
    signal = [{"id": "OPS", "value": ops}]
    if datarecord:
        signal.append({"id": "DataRecord", "value": datarecord})
    if discdata:
        signal.append({"id": "DiscData", "value": discdata})
    return signal


# ---------------------------------------------------------------------------
# judge.outcome: the final/intermediate rule and the accumulated view
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("status", "final"), [
    ("TRY AGAIN", False), ("SELECT NEXT", False),
    ("APPROVED", True), ("DECLINED", True), ("ONLINE REQUEST", True),
    ("END APPLICATION", True), ("TRY ANOTHER INTERFACE", True),
    ("RFU(0000)", True), ("N/A", True),
])
def test_is_final_out_rule(status, final):
    assert is_final_out({"status": status}) is final
    assert (status in INTERMEDIATE_STATUSES) is not final


def test_compose_result_keeps_every_out_and_picks_the_final_one():
    first = build_result(_out(OPS_TRY_AGAIN, discdata=DISC_TRY_AGAIN))["out"]
    second = build_result(_out(OPS_ONLINE, DATA_RECORD_ONLINE))["out"]

    only_first = compose_result([first])
    assert only_first["out"]["ops"]["status"] == "TRY AGAIN"
    assert only_first["outcome"] == "try_again"
    assert len(only_first["outs"]) == 1

    both = compose_result([first, second])
    assert [o["ops"]["status"] for o in both["outs"]] == ["TRY AGAIN",
                                                         "ONLINE REQUEST"]
    assert both["out"]["ops"]["status"] == "ONLINE REQUEST"
    assert both["outcome"] == "online_request"
    assert both["online"] == "online"
    assert both["resp_code"] == "00"


def test_compose_result_requires_at_least_one_out():
    with pytest.raises(ValueError):
        compose_result([])


# ---------------------------------------------------------------------------
# executor._judge: intermediate OUTs do not settle the case
# ---------------------------------------------------------------------------

def test_intermediate_out_is_persisted_but_does_not_finish_the_case(tmp_path):
    executor, store, session = _executor(tmp_path)
    case = _case([MatchRule(path="outs.1.ops.status", op="eq",
                            expect="ONLINE REQUEST")])

    outcome, detail, final = executor._judge(
        session, case, _out(OPS_TRY_AGAIN, discdata=DISC_TRY_AGAIN),
        {"device_id": "pos-1"})

    assert (outcome, detail, final) == ("running", "intermediate", False)
    assert session.failure_reason is None, "an intermediate OUT must not fail it"

    record = store.get_case_result(session.run["run_id"], CASE_ID)
    assert record["status"] == "RUNNING"
    assert record["pending_final_out"] is True
    assert record["out_count"] == 1
    assert record["raw_result"]["out"]["ops"]["status"] == "TRY AGAIN"
    assert len(record["raw_result"]["outs"]) == 1
    # the run summary streams the live OPS of the OUT received so far
    live = session.snapshot()["results"][CASE_ID]
    assert live["status"] == "RUNNING"
    assert live["ops_status"] == "TRY AGAIN"
    assert live["ops_status_bits"] == "0111"
    assert live["verdict"] is None


def test_final_out_settles_the_case_and_judges_both_outs(tmp_path):
    executor, store, session = _executor(tmp_path)
    case = _case([
        MatchRule(path="outs.0.ops.status", op="eq", expect="TRY AGAIN"),
        MatchRule(path="outs.1.ops.status", op="eq", expect="ONLINE REQUEST"),
        MatchRule(path="outcome", op="eq", expect="online_request"),
    ])

    executor._judge(session, case, _out(OPS_TRY_AGAIN, discdata=DISC_TRY_AGAIN),
                    {"device_id": "pos-1"})
    outcome, detail, final = executor._judge(
        session, case, _out(OPS_ONLINE, DATA_RECORD_ONLINE, DISC_ONLINE),
        {"device_id": "pos-1"})

    assert (outcome, detail, final) == ("pass", "persisted", True)
    record = store.get_case_result(session.run["run_id"], CASE_ID)
    assert record["status"] == "PASS"
    assert record["out_count"] == 2
    assert "pending_final_out" not in record
    assert [o["ops"]["status"] for o in record["raw_result"]["outs"]] == \
        ["TRY AGAIN", "ONLINE REQUEST"]
    assert record["raw_result"]["out"]["ops"]["status"] == "ONLINE REQUEST"
    live = session.snapshot()["results"][CASE_ID]
    assert live["status"] == "PASS" and live["verdict"] == "PASS"
    assert live["ops_status"] == "ONLINE REQUEST"


def test_single_out_case_is_unchanged(tmp_path):
    """Non-vacuous contrast: one final OUT behaves exactly as before."""
    executor, store, session = _executor(tmp_path)
    case = _case([MatchRule(path="outcome", op="eq", expect="online_request")])

    outcome, detail, final = executor._judge(
        session, case, _out(OPS_ONLINE, DATA_RECORD_ONLINE), {"device_id": "pos"})

    assert (outcome, detail, final) == ("pass", "persisted", True)
    record = store.get_case_result(session.run["run_id"], CASE_ID)
    assert len(record["raw_result"]["outs"]) == 1
    assert record["out_count"] == 1


# ---------------------------------------------------------------------------
# executor._on_out: the waiter is released by the FINAL OUT only
# ---------------------------------------------------------------------------

class _StubLibrary:
    """``_on_out`` resolves the case through the library."""

    def __init__(self, case: TestCase) -> None:
        self._case = case

    def get_case(self, brand, mode, case_id):  # noqa: ARG002
        return self._case if case_id == CASE_ID else None

    def kernel_params_for(self, brand):  # noqa: ARG002
        return None


@pytest.mark.asyncio
async def test_on_out_acks_every_out_but_releases_the_waiter_on_the_final(tmp_path):
    import asyncio

    executor, store, session = _executor(tmp_path)
    case = _case([MatchRule(path="outs.1.ops.status", op="eq",
                            expect="ONLINE REQUEST")])
    executor.library = _StubLibrary(case)
    session._result_waiter = asyncio.get_running_loop().create_future()

    details: list[str] = []

    async def fake_ack(conn, msg_id, ok=True, detail=""):  # noqa: ARG001
        details.append(detail)

    executor._ack = fake_ack  # type: ignore[method-assign]

    await executor._on_out(None, {"msg_id": "m1",
                                  "signalData": _out(OPS_TRY_AGAIN,
                                                     discdata=DISC_TRY_AGAIN)})
    assert details == ["intermediate"]
    assert not session._result_waiter.done(), \
        "a Try Again OUT must keep the attempt alive for the re-tap"

    await executor._on_out(None, {"msg_id": "m2",
                                  "signalData": _out(OPS_ONLINE,
                                                     DATA_RECORD_ONLINE)})
    assert details[-1] == "persisted"
    assert session._result_waiter.done()
    assert session._result_waiter.result() == "pass"

    # a retransmission of the final OUT is acked as duplicate, never re-judged
    await executor._on_out(None, {"msg_id": "m2",
                                  "signalData": _out(OPS_ONLINE,
                                                     DATA_RECORD_ONLINE)})
    assert details[-1] == "duplicate"


@pytest.mark.asyncio
async def test_finalize_patches_an_unfinished_intermediate_record(tmp_path):
    """Final OUT never arrives -> the attempt end (out_timeout) wins."""
    executor, store, session = _executor(tmp_path)
    case = _case([MatchRule(path="outs.1.ops.status", op="eq",
                            expect="ONLINE REQUEST")])
    executor._judge(session, case, _out(OPS_TRY_AGAIN, discdata=DISC_TRY_AGAIN),
                    {"device_id": "pos-1"})

    session.failure_reason = "out_timeout"
    await executor._finalize_case(session, case, "fail", 0)

    record = store.get_case_result(session.run["run_id"], CASE_ID)
    assert record["status"] == "FAIL"
    assert record["failure_reason"] == "out_timeout"
    assert "pending_final_out" not in record
    # ... and the intermediate OUT stays in the record as evidence
    assert record["raw_result"]["out"]["ops"]["status"] == "TRY AGAIN"
    assert session.snapshot()["results"][CASE_ID]["status"] == "FAIL"


# ---------------------------------------------------------------------------
# the delivered case file, judged against log-shaped OUTs
# ---------------------------------------------------------------------------

def test_case_3b04_0020_passes_on_the_log_out_sequence():
    case = TestCase.model_validate(json.loads(CASE_PATH.read_text(encoding="utf-8")))
    assert case.case_id == "3B04-0020"
    assert case.kernel_params_ref == "PPS_MChip1"
    assert case.profile.noResponseCommands[0].command == "SELECT"
    assert case.profile.noResponseCommands[0].times == 1

    outs = [
        build_result(_out(OPS_TRY_AGAIN, discdata=DISC_TRY_AGAIN))["out"],
        build_result(_out(OPS_ONLINE, DATA_RECORD_ONLINE,
                          DISC_ONLINE))["out"],
    ]
    verdict = assert_result(compose_result(outs), case.expected_results)
    assert verdict.status == "PASS", [r.message for r in verdict.failed]


def test_case_3b04_0020_fails_when_the_reader_drops_the_retap_out():
    """A single Try Again OUT (no re-tap) must not pass the case."""
    case = TestCase.model_validate(json.loads(CASE_PATH.read_text(encoding="utf-8")))
    outs = [build_result(_out(OPS_TRY_AGAIN, discdata=DISC_TRY_AGAIN))["out"]]

    verdict = assert_result(compose_result(outs), case.expected_results)
    assert verdict.status == "FAIL"
    assert any(r.rule.path == "outs.1.ops.status" and not r.ok
               for r in verdict.rules)
