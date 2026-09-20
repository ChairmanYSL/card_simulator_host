"""Does an all-zero OPS + empty DataRecord (D2 'kernel did not run') ever PASS?

Drives the real _on_out with the real judge.  Two case variants:
  A: expects outcome approved           (a normal approval case)
  B: expected_results == []             (no assertions at all)
"""
import asyncio, sys, tempfile

sys.path.insert(0, r"E:\Work\ZT\card_simulator_host\backend")
sys.path.insert(0, r"E:\Work\ZT\card_simulator_host\backend\tests")

import test_runner_result as T  # noqa: E402  reuse the v2 fakes
from emv_host.cases.models import (
    CardProfile, MatchRule, OperatorInstructions, SuitePolicy, TestCase,
    TransactionParams,
)
from emv_host.runner.executor import RunExecutor
from emv_host.runner.session import RunSession
from emv_host.store.run_store import RunStore

ZERO_OPS = "0000000000000000"
CASE_ID = "C-01"


class Lib:
    def __init__(self, case):
        self.case = case
        self.brands = {"visa": type("S", (), {
            "suite_policy": SuitePolicy(retry_count=0), "kernel_params": None})()}

    def get_case(self, brand, mode, case_id):
        return self.case if case_id == CASE_ID else None

    def kernel_params_for(self, brand):
        return None


def case(rules):
    return TestCase(
        case_id=CASE_ID, title="t", brand="visa",
        profile=CardProfile(id=CASE_ID),
        transaction=TransactionParams(operator_instructions=OperatorInstructions()),
        expected_results=rules)


async def sink(event, data):
    pass


async def run(label, rules):
    tmp = tempfile.mkdtemp(prefix="d2_")
    store = RunStore(tmp)
    ex = RunExecutor(Lib(case(rules)), T.FakeProtocol(), store, sink)
    run_d = store.new_run("visa", "contactless", [CASE_ID], "single")
    s = RunSession(run_d, "visa", "contactless", [CASE_ID])
    s.begin_attempt(CASE_ID, "att-1", 0)
    s._result_waiter = asyncio.get_running_loop().create_future()
    ex._session = s
    frame = T.out_frame(ops=ZERO_OPS)
    frame["signalData"] = [{"id": "OPS", "value": ZERO_OPS},
                           {"id": "DataRecord", "value": ""}]
    conn = T.FakeConn()
    await ex._on_out(conn, frame)
    rec = store.get_case_result(run_d["run_id"], CASE_ID)
    print(f"[{label}] ack={conn.last.get('detail')} status={rec and rec['status']} "
          f"record.failure_reason={rec and rec.get('failure_reason')!r}")
    print(f"[{label}] outcome={rec and rec['raw_result']['outcome']!r} "
          f"resp_code={rec and rec['raw_result']['resp_code']!r} "
          f"session.failure_reason={s.failure_reason!r}")


async def main():
    print("OPS all-zero + empty DataRecord, judged through the real _on_out:\n")
    await run("A expects approved",
              [MatchRule(path="outcome", op="eq", expect="approved")])
    print()
    await run("B no assertions", [])
    print()
    await run("C only warn-severity rules",
              [MatchRule(path="outcome", op="eq", expect="approved",
                         severity="warn")])


asyncio.run(main())
