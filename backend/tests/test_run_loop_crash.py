"""Run-loop resilience: a crashed/cancelled run task must never leave a
silent zombie (store status RUNNING while busy is already False, which made
every later abort/stop/skip answer 409 "no run in progress")."""

import pytest

from emv_host.runner.executor import RunExecutor
from emv_host.runner.session import RunSession
from emv_host.store.run_store import RunStore


class _ExplodingLibrary:
    def get_case(self, brand, mode, case_id):  # noqa: ARG002
        raise RuntimeError("boom in library")


async def _sink(event, data):  # noqa: ARG001
    pass


@pytest.mark.asyncio
async def test_run_loop_crash_is_logged_and_finalized_as_aborted(tmp_path):
    store = RunStore(tmp_path)
    executor = RunExecutor(_ExplodingLibrary(), protocol=None, store=store,
                           sink=_sink)
    run = store.new_run("visa", "contactless", ["VIS-CL-001"], "single")
    session = RunSession(run, "visa", "contactless", ["VIS-CL-001"])

    await executor._run_loop(session)  # must not raise out of the task entry

    saved = store.get_run(run["run_id"])
    assert saved is not None
    assert saved["status"] == "ABORTED"
    assert saved["abort_reason"] is not None
    assert "internal error" in saved["abort_reason"]
    assert saved["finished_at"] is not None
    # checkpoint marks the run done so boot recovery won't re-interpret it
    checkpoint = store.read_checkpoint(run["run_id"])
    assert checkpoint is not None and checkpoint["state"] == "done"
