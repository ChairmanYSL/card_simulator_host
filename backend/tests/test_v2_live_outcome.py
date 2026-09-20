"""Protocol v2 Q8 (live OUT outcome in the run view).

``RunStore.write_case_result`` writes ``run["results"][case_id]``, which is
exactly what ``RunSession.snapshot()`` streams over the websocket and returns
from ``/api/run`` / ``/api/health``.  Before this change the run-level summary
carried only ``status / attempts / verdict / finished_at / failure_reason``, so
the OUT outcome (OPS status, resp_code) was invisible until the run was over
and the history page was opened.

Two halves have to hold, and either one alone is not enough:

* ``RunStore.write_case_result`` must copy ``raw_result.out.ops.status``,
  ``...["status_bits"]`` and ``raw_result["resp_code"]`` into that summary;
* ``RunExecutor._judge`` must MERGE into the summary it just wrote -- it used
  to replace the whole dict, which dropped the new keys on the only path that
  produces a ``raw_result``.

Only present keys are copied, so a v1.2-shaped record (no ``raw_result``) or a
partial one keeps its previous summary exactly.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from emv_host.cases.models import (
    CardProfile,
    MatchRule,
    OperatorInstructions,
    TestCase,
    TransactionParams,
)
from emv_host.runner.executor import RunExecutor
from emv_host.runner.session import RunSession
from emv_host.store.run_store import RunStore
from emv_host.web import AppContext, EventBus, create_app

CASE_ID = "VISA-CL-001"

#: OPS first nibble 1 -> APPROVED / status_bits "0001" (judge/outcome.py)
OPS_APPROVED = "1000F00030F0FF00"
#: all-zero OPS -> status "RFU(0000)" / status_bits "0000" (v2 D2)
OPS_ZERO = "0000000000000000"
#: DataRecord with 8A = "3030" -> resp_code "3030"
DATA_RECORD = "8A0230309F270140"

#: keys the pre-change summary always had (v1.2 shape)
BASE_KEYS = {"status", "attempts", "verdict", "finished_at"}


def _case() -> TestCase:
    return TestCase(
        case_id=CASE_ID, title="offline approve", brand="visa",
        profile=CardProfile(id=CASE_ID),
        transaction=TransactionParams(
            operator_instructions=OperatorInstructions()),
        expected_results=[MatchRule(path="outcome", op="eq",
                                    expect="approved")])


class StubLibrary:
    """``_judge`` never consults the library; the executor just needs one."""

    brands: list[str] = []

    def get_case(self, brand, mode, case_id):  # noqa: ARG002
        return _case() if case_id == CASE_ID else None

    def kernel_params_for(self, brand):  # noqa: ARG002
        return None


async def _sink(event, data):  # noqa: ARG001
    pass


def _v2_record(**overrides) -> dict:
    record = {
        "case_id": CASE_ID,
        "status": "PASS",
        "attempt": 0,
        "attempts": 1,
        "verdict": {"status": "PASS", "rules": []},
        "finished_at": 111.0,
        "raw_result": {
            "out": {"ops": {"status": "APPROVED", "status_bits": "0001"}},
            "resp_code": "3030",
        },
    }
    record.update(overrides)
    return record


# ---------------------------------------------------------------------------
# RunStore.write_case_result
# ---------------------------------------------------------------------------


def test_write_case_result_copies_v2_out_outcome(tmp_path):
    """A record with raw_result -> the summary carries the OUT outcome."""
    store = RunStore(tmp_path)
    run = store.new_run("visa", "contactless", [CASE_ID], "single")

    store.write_case_result(run, CASE_ID, _v2_record())

    # read back from disk: this is the payload the live snapshot streams
    summary = store.get_run(run["run_id"])["results"][CASE_ID]
    assert summary["ops_status"] == "APPROVED"
    assert summary["ops_status_bits"] == "0001"
    assert summary["resp_code"] == "3030"
    # ...and the pre-existing summary keys are untouched
    assert summary["status"] == "PASS"
    assert summary["attempts"] == 1
    assert summary["finished_at"] == 111.0
    assert "failure_reason" not in summary, "a PASS must not gain a reason"


def test_write_case_result_v12_shaped_record_keeps_its_exact_summary(tmp_path):
    """No raw_result (v1.2 shape) -> not a single new/empty key."""
    store = RunStore(tmp_path)
    run = store.new_run("visa", "contactless", [CASE_ID], "single")

    store.write_case_result(run, CASE_ID, {
        "case_id": CASE_ID, "status": "FAIL", "attempt": 0, "attempts": 1,
        "verdict": {"status": "FAIL", "rules": []}, "finished_at": 222.0,
        "failure_reason": "result_timeout",
    })

    summary = store.get_run(run["run_id"])["results"][CASE_ID]
    assert set(summary) == BASE_KEYS | {"failure_reason"}
    assert summary["failure_reason"] == "result_timeout"


def test_write_case_result_partial_raw_result_gains_no_empty_keys(tmp_path):
    """Partial raw_result (no ``out.ops`` / empty resp_code) stays small."""
    partials = [
        {},                                   # raw_result but nothing in it
        {"resp_code": ""},                    # no outcome -> empty code (v2 D2)
        {"out": {}},                          # out present, ops absent
        {"out": {"ops": {}}},                 # ops present but undecoded
        {"out": {"ops": {"status": ""}}},     # empty status is not a status
        {"out": {"ops": {"status": "APPROVED"}}},  # status without bits
    ]
    for index, raw_result in enumerate(partials):
        workdir = tmp_path / f"partial-{index}"
        workdir.mkdir()
        store = RunStore(workdir)
        run = store.new_run("visa", "contactless", [CASE_ID], "single")
        store.write_case_result(run, CASE_ID, _v2_record(raw_result=raw_result))

        summary = store.get_run(run["run_id"])["results"][CASE_ID]
        if raw_result.get("out", {}).get("ops", {}).get("status"):
            # the one non-empty partial: status only, no bits, no resp_code
            assert set(summary) == BASE_KEYS | {"ops_status"}
            assert summary["ops_status"] == "APPROVED"
        else:
            assert set(summary) == BASE_KEYS, raw_result


def test_write_case_result_failed_v2_record_keeps_reason_and_outcome(tmp_path):
    """v2 D2 (no outcome): status + reason + the decoded OPS status coexist."""
    store = RunStore(tmp_path)
    run = store.new_run("visa", "contactless", [CASE_ID], "single")

    store.write_case_result(run, CASE_ID, _v2_record(
        status="FAIL", failure_reason="out_no_outcome",
        raw_result={
            "out": {"ops": {"status": "RFU(0000)", "status_bits": "0000"},
                    "datarecord": {}},
            "resp_code": "",
        }))

    summary = store.get_run(run["run_id"])["results"][CASE_ID]
    assert summary["status"] == "FAIL"
    assert summary["failure_reason"] == "out_no_outcome"
    assert summary["ops_status"] == "RFU(0000)"
    assert summary["ops_status_bits"] == "0000"
    # an empty resp_code carries no information -> no empty key
    assert "resp_code" not in summary


# ---------------------------------------------------------------------------
# end to end: the LIVE session payload (what the run view renders)
# ---------------------------------------------------------------------------


def _judge_one(tmp_path, ops: str, datarecord: str, case: TestCase) -> tuple:
    store = RunStore(tmp_path / "runs")
    executor = RunExecutor(StubLibrary(), None, store, _sink)
    run = store.new_run("visa", "contactless", [CASE_ID], "single")
    session = RunSession(run, "visa", "contactless", [CASE_ID])
    session.begin_attempt(CASE_ID, "att-1", 0)
    executor._session = session

    signal_data = [{"id": "OPS", "value": ops},
                   {"id": "DataRecord", "value": datarecord}]
    outcome, detail = executor._judge(session, case, signal_data,
                                      {"device_id": "pos-1"})
    return executor, store, session, outcome, detail


def test_judge_publishes_out_outcome_in_live_session_payload(tmp_path):
    """_judge must not clobber the summary write_case_result just wrote."""
    executor, store, session, outcome, detail = _judge_one(
        tmp_path, OPS_APPROVED, DATA_RECORD, _case())

    assert (outcome, detail) == ("pass", "persisted")

    # 1) the in-memory run-level summary (RunSession.snapshot source)
    live = session.snapshot()["results"][CASE_ID]
    assert live["ops_status"] == "APPROVED"
    assert live["ops_status_bits"] == "0001"
    assert live["resp_code"] == "3030"
    assert live["status"] == "PASS"
    # the merge must keep the run-level verdict a plain status string
    assert live["verdict"] == "PASS"

    # 2) the persisted run.json agrees
    assert store.get_run(session.run["run_id"])["results"][CASE_ID] == live

    # 3) the real HTTP payload the run view polls (GET /api/run, /api/health)
    ctx = AppContext(library=StubLibrary(), executor=executor, store=store,
                     bus=EventBus(), static_dir="")
    api = TestClient(create_app(ctx))
    body = api.get("/api/run").json()
    assert body["session"]["results"][CASE_ID]["ops_status"] == "APPROVED"
    assert body["session"]["results"][CASE_ID]["resp_code"] == "3030"
    health = api.get("/api/health").json()
    assert health["session"]["results"][CASE_ID]["ops_status_bits"] == "0001"


def test_judge_out_no_outcome_is_visible_live(tmp_path):
    """The D2 ruling reaches the live view as RFU(0000), with no resp_code."""
    _, _, session, outcome, _ = _judge_one(tmp_path, OPS_ZERO, "", _case())

    assert outcome == "fail"
    live = session.snapshot()["results"][CASE_ID]
    assert live["status"] == "FAIL"
    assert live["failure_reason"] == "out_no_outcome"
    assert live["ops_status"] == "RFU(0000)"
    assert live["ops_status_bits"] == "0000"
    assert "resp_code" not in live


def test_summary_of_a_record_without_raw_result_gains_nothing(tmp_path):
    """Non-vacuous contrast: a record written without raw_result (the
    ``_finalize_case`` shape) keeps the pre-change key set exactly."""
    store = RunStore(tmp_path / "runs")
    run = store.new_run("visa", "contactless", [CASE_ID], "single")
    store.write_case_result(run, CASE_ID, {
        "case_id": CASE_ID, "status": "ABORTED", "attempt": 0, "attempts": 1,
        "finished_at": 333.0,
    })

    summary = store.get_run(run["run_id"])["results"][CASE_ID]
    assert set(summary) == BASE_KEYS
    assert summary["status"] == "ABORTED"
    assert summary["verdict"] is None
