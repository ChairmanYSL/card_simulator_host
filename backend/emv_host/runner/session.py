"""Run session: one active test run with its command signals."""

from __future__ import annotations

import asyncio
from typing import Any


class RunSession:
    """State for a single run.  One executor owns at most one session."""

    def __init__(self, run: dict[str, Any], brand: str, mode: str,
                 case_ids: list[str]):
        self.run = run
        self.brand = brand
        self.mode = mode
        self.queue = list(case_ids)

        # command signals (set by web/CLI; consumed by the run loop)
        self.abort_event = asyncio.Event()
        self.skip_event = asyncio.Event()
        self.stop_requested = False
        self.fatal_reason: str | None = None
        # v1.2 D4: why a case FAILed (ack timeout / device lost / assertion).
        # ``fatal_reason`` still drives the run-level stop; this one is the
        # case-level explanation persisted into the result record.
        self.failure_reason: str | None = None
        self.policy_override: dict[str, Any] = {}  # e.g. {"on_failure": "continue"}

        # current attempt
        self.current_case_id: str | None = None
        self.current_attempt_id: str | None = None
        self.current_attempt: int = 0
        self.current_state: str = "IDLE"  # CONFIGURING/WAITING_EMULATION/WAIT_RESULT/JUDGING
        # v1.2 D2: transaction_result msg_ids already judged in this attempt, so
        # a retransmission is acked as a duplicate instead of being re-judged
        # (or quarantined).
        self.judged_msg_ids: set[str] = set()
        # TS-5/H2: true once the android reported "emulating" for the current
        # attempt.  From that moment every exit path must send stop_emulation,
        # otherwise the HCE session can leak into the next case.
        self.card_emulating: bool = False

        # in-flight waits, keyed by attempt_id
        self._result_waiter: asyncio.Future | None = None
        self._emulation_waiter: asyncio.Future | None = None

    # -- commands -----------------------------------------------------------
    def request_stop(self) -> None:
        self.stop_requested = True

    def request_abort(self) -> None:
        self.abort_event.set()

    def request_skip(self) -> None:
        self.skip_event.set()

    # -- attempt bookkeeping --------------------------------------------------
    def begin_attempt(self, case_id: str, attempt_id: str, attempt: int) -> None:
        self.current_case_id = case_id
        self.current_attempt_id = attempt_id
        self.current_attempt = attempt
        self.current_state = "CONFIGURING"
        self.failure_reason = None
        self.judged_msg_ids = set()
        self.card_emulating = False
        self._result_waiter = None
        self._emulation_waiter = None

    def end_attempt(self) -> None:
        self.current_state = "IDLE"
        self.current_attempt_id = None
        self.card_emulating = False
        self._result_waiter = None
        self._emulation_waiter = None

    # -- result delivery ------------------------------------------------------
    def is_waiting_result(self, attempt_id: str) -> bool:
        return (self._result_waiter is not None
                and not self._result_waiter.done()
                and self.current_attempt_id == attempt_id)

    def deliver_result(self, outcome: str) -> bool:
        """Resolve the in-flight result waiter with a judged outcome.

        v1.2 D2: the transaction_result is judged (and persisted) by
        ``_on_transaction_result``; the run loop only reads the outcome, so the
        POS ack means "written to disk", not "arrived".
        """
        if self._result_waiter is not None and not self._result_waiter.done():
            self._result_waiter.set_result(outcome)
            return True
        return False

    def deliver_emulation_status(self, state: str) -> bool:
        if self._emulation_waiter is not None and not self._emulation_waiter.done():
            self._emulation_waiter.set_result(state)
            return True
        return False

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run.get("run_id"),
            "brand": self.brand,
            "mode": self.mode,
            "run_status": self.run.get("status"),
            "stop_requested": self.stop_requested,
            "fatal_reason": self.fatal_reason,
            "failure_reason": self.failure_reason,
            "queue": self.queue,
            "current": {
                "case_id": self.current_case_id,
                "attempt_id": self.current_attempt_id,
                "attempt": self.current_attempt,
                "state": self.current_state,
            },
            "results": self.run.get("results", {}),
        }
