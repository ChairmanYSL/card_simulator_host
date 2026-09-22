"""Run executor: single active run, full orchestration, abort/skip/retry/resume.

Command semantics (three verbs):

* ``stop``   — let the current case finish, then stop (remaining cases untouched);
* ``abort``  — interrupt the current case immediately (status ABORTED);
* ``skip``   — mark the current WAIT_RESULT case SKIPPED and move on.

v1.2 failure semantics (D4/D5/Q7/Q9):

* every host->device command requires an ack; a missing/rejected ack (or a
  disconnected peer) fails the CURRENT case with a structured ``failure_reason``;
* a FAIL stops the whole run immediately and every case still queued is
  persisted as SKIPPED (``note=run_stopped_after_failure:<case>``);
* device loss during a run fails the case — for EITHER side (POS loss used to
  be warn-only);
* the POS ack for ``transaction_result`` means "verdict persisted" (D2): the
  result is judged and written to disk inside ``_on_transaction_result`` before
  the ack is sent, so a retransmission can only ever be a duplicate;
* an android-side error keeps case status ERROR with its structured
  ``error{code,message}``;
* both sides receive ``session_abort`` (best effort, warn-only acks) whenever a
  run ends abnormally.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

from ..cases import CaseLibrary, TestCase
from ..cases.models import KernelParams
from ..judge.asserter import assert_result
from ..judge.card_apdu import build_card_view
from ..judge.outcome import build_result
from ..protocol.connection import ConnectionClosed
from ..protocol.messages import (
    CLIENT_ANDROID,
    CLIENT_POS,
    CONFIG_ID_AID,
    CONFIG_ID_CAPK,
    CONFIG_ID_DRL,
    CONFIG_ID_EXCEPTION_FILE,
    CONFIG_ID_NAME,
    CONFIG_ID_TERM,
    CONFIG_ID_VERSION,
    CONFIG_TYPE_CARD,
    MSG_ACT,
    MSG_ACT_ACK,
    MSG_ACK,
    MSG_CONFIG,
    MSG_CONFIG_ACK,
    MSG_CONFIG_PUSH,
    MSG_LOG,
    MSG_MSG,
    MSG_OUT,
    MSG_START_EMULATION,
    MSG_STATUS,
    MSG_STOP_EMULATION,
    MSG_TEST_INFO,
    MSG_TEST_INFO_ACK,
    RESP_NOTSUPPORTED,
    make_frame,
    new_msg_id,
)
from ..protocol.server import ProtocolServer
from ..store.run_store import RunStore
from .session import RunSession

logger = logging.getLogger("emv_host.runner")

# async callback: publish(event: str, data: dict) -> None
Sink = Callable[[str, dict[str, Any]], Awaitable[None]]

# v1.2: both sides default to these; the executor's per-instance values come
# from AppConfig (--card-ack-timeout / --pos-ack-timeout).
CONFIG_ACK_TIMEOUT = 15.0
EMULATION_READY_TIMEOUT = 15.0
STOP_ACK_TIMEOUT = 3.0
ABORT_ACK_TIMEOUT = 3.0

PASSING = {"PASS", "WARN"}
FINAL_CASE_STATUS = {"PASS", "WARN", "FAIL", "SKIPPED", "ABORTED", "INCONCLUSIVE", "ERROR"}
# terminal run-level statuses: once persisted, the run loop has fully finished.
# ERROR belongs here (H1): an android-side error ends the run with status ERROR,
# and without it a later crash/cancel in _run_loop would overwrite ERROR with
# ABORTED via _finalize_crash.
TERMINAL_RUN_STATUS = {"FINISHED", "STOPPED", "ABORTED", "INTERRUPTED", "ERROR"}


class RunExecutor:
    def __init__(self, library: CaseLibrary, protocol: ProtocolServer,
                 store: RunStore, sink: Sink, trace_enabled: bool = False,
                 logger_host: logging.Logger | None = None,
                 card_ack_timeout: float = CONFIG_ACK_TIMEOUT,
                 pos_ack_timeout: float = CONFIG_ACK_TIMEOUT,
                 stop_ack_timeout: float = STOP_ACK_TIMEOUT,
                 abort_ack_timeout: float = ABORT_ACK_TIMEOUT):
        self.library = library
        self.protocol = protocol
        self.store = store
        self.sink = sink
        self.trace_enabled = trace_enabled
        self.card_ack_timeout = card_ack_timeout
        self.pos_ack_timeout = pos_ack_timeout
        self.stop_ack_timeout = stop_ack_timeout
        self.abort_ack_timeout = abort_ack_timeout
        self._session: RunSession | None = None
        self._task: asyncio.Task | None = None
        self._session_abort_sent: set[str] = set()
        self._devices: dict[str, dict[str, Any]] = {
            CLIENT_ANDROID: {"connected": False, "device_id": None, "app_version": None,
                             "connected_at": None, "warn": False, "session_id": None},
            CLIENT_POS: {"connected": False, "device_id": None, "app_version": None,
                         "connected_at": None, "warn": False, "session_id": None},
        }

    # -- status ---------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._session is not None and self._task is not None and not self._task.done()

    def snapshot(self) -> dict[str, Any]:
        return {
            "devices": self._devices,
            "session": self._session.snapshot() if self._session else None,
        }

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        try:
            await self.sink(event, data)
        except Exception:  # noqa: BLE001
            logger.exception("event sink failed for %s", event)

    async def log(self, level: str, message: str) -> None:
        logger.log(getattr(logging, level.upper(), logging.INFO), "%s", message)
        await self.publish("log", {"level": level, "message": message, "ts": time.time()})

    # -- diagnostics -----------------------------------------------------------
    #: 全场统一的贴卡操作提示（联调 run #14 复盘：HCE 会话窗口 <350ms 会让
    #: 内核在下一条命令上拿到 SW=0000 → -103/-1102/-1133；窗口 ≥800ms 时同用例
    #: 全 PASS）。追加到每个用例的操作员备注末尾，避免逐用例改文案。
    _TAP_HOLD_NOTE = ("贴卡后请保持手机贴在读卡器上 ≥1 秒（直到 POS 出结果），"
                      "避免 HCE 会话在 APDU 序列中途被拆链")

    def _operator_instructions(self, case: TestCase) -> dict[str, Any]:
        instructions = case.transaction.operator_instructions.model_dump()
        note = (instructions.get("notes") or "").strip()
        if self._TAP_HOLD_NOTE not in note:
            instructions["notes"] = (f"{note}；{self._TAP_HOLD_NOTE}" if note
                                     else self._TAP_HOLD_NOTE)
        return instructions

    def _state_diagnostic(self) -> str:
        """One-line state summary used when a control command is rejected, so a
        409 in the UI is fully explainable from the log (and the API detail)."""
        session = self._session
        if session is None:
            return "session=none (no run has been started since startup)"
        run = session.run
        parts = [f"run_id={run.get('run_id')!r}", f"status={run.get('status')!r}"]
        if session.current_case_id:
            parts.append(f"current_case={session.current_case_id!r}")
            parts.append(f"state={session.current_state!r}")
        if self._task is None:
            parts.append("task=none")
        elif self._task.done():
            if self._task.cancelled():
                parts.append("task=cancelled")
            else:
                exc = self._task.exception()
                parts.append("task=done" if exc is None
                             else f"task=raised {type(exc).__name__}: {exc}")
        else:
            parts.append("task=running")
        return ", ".join(parts)

    def _reject_command(self, verb: str) -> tuple[bool, str]:
        """Log and build the message for a rejected stop/abort/skip command."""
        detail = self._state_diagnostic()
        logger.warning("command '%s' rejected: no run in progress (%s)",
                       verb, detail)
        return False, f"no run in progress ({detail})"

    def _fail(self, session: RunSession, reason: str) -> None:
        """Mark the current case FAIL and stop the whole run (v1.2 D4/Q9).

        Every abnormal path funnels through here so the case record, the
        run-level abort_reason and the session_abort notice always agree.

        The FIRST reason wins (``reason`` only applies when none was recorded):
        a failure signalled while waiting must not overwrite the assertion
        verdict that _judge already persisted and acked.
        """
        if session.failure_reason is not None:
            return
        session.failure_reason = reason
        session.run.setdefault("results", {}).setdefault(
            session.current_case_id or "", {})["failure_reason"] = reason
        if session.fatal_reason is None:
            case_part = f":{session.current_case_id}" if session.current_case_id else ""
            session.fatal_reason = f"case_failed{case_part}:{reason}"
        session.request_abort()

    # -- commands ---------------------------------------------------------------
    async def start(self, brand: str, mode: str, case_ids: list[str],
                    kind: str, resume_from: str | None = None,
                    overrides: dict[str, Any] | None = None) -> tuple[bool, str]:
        if self.busy:
            detail = self._state_diagnostic()
            logger.warning("start rejected: a run is already in progress (%s)",
                           detail)
            return False, f"a run is already in progress ({detail})"
        if brand not in self.library.brands:
            return False, f"unknown brand {brand!r}"
        if mode not in self.library.entry_modes_for(brand):
            return False, f"unknown entry mode {mode!r} for brand {brand!r}"
        available = self.library.all_case_ids(brand, mode)
        unknown = [c for c in case_ids if c not in available]
        if unknown:
            return False, f"unknown case ids: {unknown}"
        if not case_ids:
            return False, "no cases to run"

        run = self.store.new_run(brand, mode, case_ids, kind)
        if resume_from:
            run["resumed_from"] = resume_from
            self.store.update_run(run)
        session = RunSession(run, brand, mode, case_ids)
        session.policy_override = {k: v for k, v in (overrides or {}).items()
                                   if v is not None}
        self._session = session
        self._task = asyncio.create_task(self._run_loop(session))
        await self.log("info", f"run {run['run_id']} queued: {brand}/{mode} "
                               f"({len(case_ids)} cases, kind={kind})")
        return True, run["run_id"]

    async def stop(self) -> tuple[bool, str]:
        if not self.busy:
            return self._reject_command("stop")
        self._session.request_stop()
        await self.log("info", "stop requested: finishing current case, then stopping")
        return True, "stop requested"

    async def abort(self) -> tuple[bool, str]:
        if not self.busy:
            return self._reject_command("abort")
        self._session.request_abort()
        await self.log("warn", "abort requested: interrupting current case")
        return True, "abort requested"

    async def skip(self) -> tuple[bool, str]:
        if not self.busy:
            return self._reject_command("skip")
        if self._session.current_state != "WAIT_RESULT":
            logger.warning("command 'skip' rejected: current state is %s "
                           "(run_id=%s)", self._session.current_state,
                           self._session.run.get("run_id"))
            return False, "skip only applies while waiting for a POS result"
        self._session.request_skip()
        await self.log("warn", "skip requested for current case")
        return True, "skip requested"

    async def shutdown(self) -> None:
        """Best-effort shutdown of an in-flight run: abort it so its final
        status is persisted (ABORTED / server_shutdown) before the host exits.

        Called before the protocol server stops, so the abort flow can still
        send ``stop_emulation`` to the Android client."""
        session = self._session
        if self.busy and session is not None:
            session.fatal_reason = "server_shutdown"
            session.request_abort()
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=2.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                logger.warning("run %s did not finalize in time; it will be "
                               "recovered on next start", session.run["run_id"])
        elif self._task is not None and not self._task.done():
            self._task.cancel()

    # -- device events (called by the app's protocol handler) -------------------
    async def on_device_event(self, event: str, conn: Any, frame: dict[str, Any] | None) -> None:
        if event == "connected":
            self._devices[conn.client_type] = {
                "connected": True, "device_id": conn.device_id,
                "app_version": conn.app_version, "connected_at": conn.connected_at,
                "warn": False, "session_id": conn.session_id,
            }
            await self.publish("device_status", {"devices": self._devices})
            await self.log("info", f"device connected: {conn.label}")
            # v1.2: nothing is pushed on connect any more — device_config is
            # gone (both sides use hardcoded heartbeat/ack defaults) and
            # card_config/pos_kernel_config are per-case.
            return

        if event == "disconnected":
            known = self._devices.get(conn.client_type)
            # a replaced session (same-device reconnect) must not flip the
            # registry back to disconnected nor abort a running case
            if known and known.get("session_id") != conn.session_id:
                await self.log("info", f"stale session dropped (replaced): {conn.label}")
                return
            if known:
                known["connected"] = False
                known["warn"] = False
                known["session_id"] = None
            await self.publish("device_status", {"devices": self._devices})
            await self.log("warn", f"device disconnected: {conn.label}")
            session = self._session
            if session and self.busy:
                # v1.2 D4/Q7: losing EITHER device during a run fails the case
                # and stops the run.  POS loss used to be warn-only, but the
                # run cannot continue meaningfully without its result channel.
                if conn.client_type == CLIENT_ANDROID:
                    self._fail(session, "android_disconnected")
                elif conn.client_type == CLIENT_POS:
                    self._fail(session, "pos_disconnected")
            return

        if event == "heartbeat_warn":
            if conn.client_type in self._devices:
                self._devices[conn.client_type]["warn"] = True
            await self.publish("device_status", {"devices": self._devices})
            return

        if event != "message" or frame is None:
            return
        msg_type = frame.get("type")
        msg_id = frame.get("msg_id")

        if msg_type == MSG_STATUS:
            await self._ack(conn, msg_id)
            await self._on_android_status(conn, frame)
            return

        if msg_type == MSG_LOG:
            level = frame.get("level", "info")
            if level == "trace":
                await self._on_trace(conn, frame)
            else:
                await self.log(level, f"[{conn.client_type}] {frame.get('message', '')}")
            return

        if msg_type == MSG_MSG:
            await self._ack(conn, msg_id)
            await self._on_pos_msg(conn, frame)
            return

        if msg_type == MSG_OUT:
            # v2: judge + persist HERE, then ack (the ack means "on disk", so a
            # retransmission cannot slip through the delivery/judgment window).
            await self._on_out(conn, frame)
            return

    async def _ack(self, conn: Any, ack_msg_id: str, ok: bool = True,
                  detail: str = "") -> None:
        if not ack_msg_id:
            return
        try:
            await conn.send(make_frame(MSG_ACK, ack_msg_id=ack_msg_id,
                                       ok=ok, detail=detail))
        except Exception as exc:  # noqa: BLE001 - best effort
            await self.log("warn", f"ack to {conn.label} failed: {exc}")

    async def _on_android_status(self, conn: Any, frame: dict[str, Any]) -> None:
        state = frame.get("state", "")
        detail = frame.get("detail") or {}
        attempt_id = frame.get("attempt_id")
        session = self._session

        if state == "error":
            code = (detail or {}).get("code", "internal")
            message = (detail or {}).get("message", "")
            await self.log("error", f"[android] status error code={code} "
                                    f"message={message} case={frame.get('case_id')}")
            if session and session.current_attempt_id == attempt_id:
                # v1.1 N7 / v1.2 D5: in-case error -> case status ERROR with the
                # structured error kept, and the run stops
                session.run.setdefault("results", {}).setdefault(
                    session.current_case_id or "", {})["android_error"] = \
                    {"code": code, "message": message}
                self._fail(session, "android_error")
            return

        if state in ("ready", "emulating"):
            if state == "ready":
                if detail.get("default_payment_app") is False:
                    await self.log("warn", "[android] 未设为默认支付应用，"
                                           "请操作员在系统设置中启用后再上电")
                ac_crypto = detail.get("ac_crypto", "")
                if ac_crypto:
                    await self.log("info", f"[android] ready ac_crypto={ac_crypto} "
                                           f"profile={detail.get('profile_id')}")
                # ruling 3.1(c): ready.registered_aids is a PLAN list and is not
                # a判据; the authoritative registration result arrives with
                # emulating and is logged below.
            else:  # emulating
                registered = detail.get("registered_aids")
                if registered is not None:
                    await self.log("info", f"[android] emulating "
                                           f"registered_aids={registered}")
            in_case = (session and session.current_attempt_id
                       and attempt_id == session.current_attempt_id)
            if in_case and session.deliver_emulation_status(state):
                await self.log("info", f"android status: {state} "
                                       f"(case={frame.get('case_id')})")
            return

        await self.log("info", f"android status: {state} "
                               f"(case={frame.get('case_id')})")

    async def _on_trace(self, conn: Any, frame: dict[str, Any]) -> None:
        """Phase-A APDU trace channel: log{level:'trace', message:{...}}.

        Stored per attempt under runs/<run_id>/trace/<case_id>.jsonl and only
        when the device was configured with trace_enabled; never judged."""
        message = frame.get("message")
        case_id = frame.get("case_id")
        attempt_id = frame.get("attempt_id")
        session = self._session
        await self.log("trace", f"[android] trace case={case_id} "
                                f"attempt={attempt_id}: {message}")
        if not session:
            return
        if not case_id or not attempt_id:
            return
        # v1.2: the card side enables the trace channel from
        # card_config.trace_enabled and answers in_case=True; this gate keeps
        # unconditional traces out when the host was started with tracing off.
        if not frame.get("in_case", True):
            return
        if attempt_id != session.current_attempt_id:
            return
        # v2 (2026-09-20): the APDU trace is a *judging source*, so the collected
        # stream is kept for `card.*` assertions regardless of --trace-enabled;
        # that switch now only gates persistence to trace/<case_id>.jsonl.
        if isinstance(message, dict) and message.get("event") == "apdu":
            session.card_apdus.setdefault("apdus", []).append(message)
        if not self.trace_enabled:
            return
        self.store.write_trace(session.run, case_id, attempt_id, {
            "at": time.time(), "message": message,
        })

    async def _on_pos_msg(self, conn: Any, frame: dict[str, Any]) -> None:
        """CTEL2 MSG (UIRD): the POS display request; kept for the report."""
        uird = None
        for item in frame.get("signalData") or []:
            if isinstance(item, dict) and str(item.get("id", "")).upper() == "UIRD":
                uird = item.get("value")
        session = self._session
        if session is not None and session.current_attempt_id:
            session.pos_msgs.append({"at": time.time(), "uird": uird})
        await self.log("info", f"[pos] MSG UIRD={uird}")

    async def _on_out(self, conn: Any, frame: dict[str, Any]) -> None:
        """CTEL2 OUT: build the judgeable result, judge + persist it, then ack.

        OUT carries no case/attempt id (CTEL2 answers an ACT on the same
        connection), so it is attributed to the current in-flight attempt.
        """
        msg_id = frame.get("msg_id")
        session = self._session
        if session is not None and msg_id and msg_id in session.judged_msg_ids:
            await self._ack(conn, msg_id, ok=True, detail="duplicate")
            await self.log("warn", f"duplicate out ignored (msg_id={msg_id})")
            return
        attributed = (session is not None
                      and session.current_attempt_id is not None
                      and session.is_waiting_result(session.current_attempt_id))
        if not attributed:
            await self._quarantine(session, frame)
            await self._ack(conn, msg_id, ok=True, detail="quarantined")
            return
        case = self.library.get_case(session.brand, session.mode,
                                     session.current_case_id)
        if case is None:
            await self._quarantine(session, frame)
            await self._ack(conn, msg_id, ok=True, detail="quarantined")
            return
        try:
            outcome, detail = self._judge(session, case, frame.get("signalData"),
                                          frame)
        except Exception as exc:  # noqa: BLE001 - persistence must be loud
            logger.exception("failed to persist OUT for %s", case.case_id)
            await self.log("error", f"[{case.case_id}] OUT 落盘失败，未 ack 成功：{exc}")
            await self._ack(conn, msg_id, ok=False, detail="persist_failed")
            return
        if msg_id:
            session.judged_msg_ids.add(msg_id)
        session.deliver_result(outcome)
        await self.log("info", f"OUT received for {case.case_id} -> {outcome}")
        await self._ack(conn, msg_id, ok=True, detail=detail)

    async def _quarantine(self, session: RunSession | None,
                          frame: dict[str, Any]) -> None:
        attempt_id = frame.get("attempt_id")
        reason = "no active run" if session is None else \
            "stale attempt_id" if attempt_id else "missing attempt_id"
        if session is not None and session.current_case_id != frame.get("case_id"):
            reason = "case_id mismatch"
        entry = {"at": time.time(), "reason": reason, "frame": frame}
        if session is not None:
            self.store.write_quarantine(session.run, entry)
        await self.log("warn", f"quarantined transaction result ({reason}): "
                               f"case={frame.get('case_id')} attempt={attempt_id}")
        await self.publish("quarantine", entry)

    # -- run loop ---------------------------------------------------------------
    async def _run_loop(self, session: RunSession) -> None:
        """Task entry point.  Guarantees a crashed/cancelled run is never left
        as a silent zombie (store still RUNNING while busy is False, so every
        later abort/stop/skip answers 409 "no run in progress")."""
        try:
            await self._run_loop_body(session)
        except asyncio.CancelledError:
            if session.run.get("status") not in TERMINAL_RUN_STATUS:
                await self._finalize_crash(session, "run task cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - a crash must be loud + persisted
            logger.exception("run %s crashed unexpectedly: %s",
                             session.run.get("run_id"), exc)
            if session.run.get("status") not in TERMINAL_RUN_STATUS:
                await self._finalize_crash(
                    session, f"internal error: {type(exc).__name__}: {exc}")

    async def _finalize_crash(self, session: RunSession, reason: str) -> None:
        """Persist a terminal ABORTED state for a run that died outside the
        normal finish path (task exception or cancellation).

        H1: a run that already reached a terminal status (notably ERROR from an
        android-side error) keeps it.  ``_run_loop`` checks this before calling,
        but the guard is repeated here so no other caller can downgrade a
        finished run.
        """
        if session.run.get("status") in TERMINAL_RUN_STATUS:
            logger.debug("run %s already terminal (%s); crash finalizer skipped",
                         session.run.get("run_id"), session.run.get("status"))
            return
        run = session.run
        current_case = session.current_case_id
        if current_case and self.store.get_case_result(
                run["run_id"], current_case) is None:
            record = {
                "case_id": current_case,
                # F3: keep the same attempt/attempts pair as the other writers
                "attempt": session.current_attempt,
                "attempts": session.current_attempt + 1,
                "attempt_id": session.current_attempt_id,
                "status": "ERROR",
                "finished_at": time.time(),
                "note": f"run task crashed: {reason}",
            }
            self.store.write_case_result(run, current_case, record)
        run["status"] = "ABORTED"
        run["abort_reason"] = reason
        run["finished_at"] = time.time()
        self.store.update_run(run)
        self.store.write_checkpoint(run, None, "", "done")
        await self.publish("run_status", session.snapshot())
        await self.log("error", f"run {run['run_id']} ended: ABORTED ({reason})")
        # v2: session_abort was removed (CTEL2 has no equivalent signal).

    async def _run_loop_body(self, session: RunSession) -> None:
        run = session.run
        run["status"] = "RUNNING"
        run["started_at"] = time.time()
        self.store.update_run(run)
        await self.publish("run_status", self.snapshot()["session"])
        await self.log("info", f"run {run['run_id']} started: {run['brand']}/{run['mode']}")

        stop_reason = ""
        failing_case_id = ""
        stopped_by_failure = False
        for index, case_id in enumerate(session.queue):
            if session.fatal_reason:
                break
            if session.stop_requested:
                break
            case = self.library.get_case(session.brand, session.mode, case_id)
            if case is None:  # shouldn't happen (validated at start)
                await self.log("error", f"case vanished from library: {case_id}")
                continue
            outcome, stop_after = await self._run_case(session, case)
            await self.publish("run_status", session.snapshot())
            if stop_after or session.fatal_reason or outcome in ("fail", "aborted"):
                # per-case: a manual abort must not inherit the previous case's
                # failure_reason as the session_abort notice
                stopped_by_failure = outcome in ("fail", "error")
                stop_reason = session.failure_reason or ""
                failing_case_id = case_id
                # Q9: a FAIL stops the run immediately — every case still queued
                # is persisted as SKIPPED so the report shows what never ran.
                await self._mark_remaining_skipped(session, index, case_id)
                break

        if session.fatal_reason:
            run["status"] = "ERROR" if stop_reason == "android_error" else "ABORTED"
            run["abort_reason"] = session.fatal_reason
        elif stopped_by_failure:
            # F2: always carry the case id, whichever path failed.  An assertion
            # FAIL comes from _judge (never through _fail), so relying on
            # session.fatal_reason left this branch writing a case-less reason.
            run["status"] = "ABORTED"
            run["abort_reason"] = (f"case_failed:{failing_case_id}:{stop_reason}"
                                   if stop_reason
                                   else f"case_failed:{failing_case_id}")
        elif session.abort_event.is_set() and not session.stop_requested:
            run["status"] = "ABORTED"
            run["abort_reason"] = "manual abort"
        elif session.stop_requested:
            run["status"] = "STOPPED"
            run["abort_reason"] = "manual stop"
        else:
            run["status"] = "FINISHED"
        run["finished_at"] = time.time()
        self.store.update_run(run)
        self.store.write_checkpoint(run, None, "", "done")
        await self.publish("run_status", session.snapshot())
        await self.log("info", f"run {run['run_id']} ended: {run['status']} "
                               f"({run.get('abort_reason') or 'completed'})")


    async def _mark_remaining_skipped(self, session: RunSession, index: int,
                                      failed_case_id: str) -> None:
        """Persist every not-yet-run queued case as SKIPPED (v1.2 Q9)."""
        run = session.run
        for pending in session.queue[index + 1:]:
            if self.store.get_case_result(run["run_id"], pending) is not None:
                continue
            record = {
                "case_id": pending,
                "status": "SKIPPED",
                "finished_at": time.time(),
                "note": f"run_stopped_after_failure:{failed_case_id}",
            }
            self.store.write_case_result(run, pending, record)
            await self.publish("case_status", {"case_id": pending,
                                               "status": "SKIPPED"})
        session.queue = session.queue[:index + 1]

    async def _run_case(self, session: RunSession, case: TestCase) -> tuple[str, bool]:
        """Execute one case (with retries).  Returns (final_outcome, stop_after).

        v1.2 D4/Q9: a FAIL is terminal for the whole run, so a failed case is
        never retried into a second attempt that would paper over it.
        """
        brand_def = self.library.brands[session.brand]
        policy = brand_def.suite_policy
        override = session.policy_override
        max_attempts = override.get("retry_count")
        if max_attempts is None:
            max_attempts = policy.retry_count
        max_attempts = max(max_attempts, case.retry_on_fail)

        outcome = "error"
        for attempt in range(max_attempts + 1):
            attempt_id = new_msg_id()
            session.begin_attempt(case.case_id, attempt_id, attempt)
            self.store.write_checkpoint(session.run, case.case_id, attempt_id, "upcoming")
            await self.publish("case_status", {
                "case_id": case.case_id, "status": "RUNNING",
                "attempt": attempt, "attempt_id": attempt_id,
                "state": "CONFIGURING",
            })
            await self.log("info", f"[{case.case_id}] attempt {attempt} starting")
            try:
                outcome = await self._attempt(session, case, attempt, attempt_id)
            finally:
                # TS-5/H2: once the card reports emulating, EVERY exit path must
                # park it.  The stop used to live only in _attempt's final wait,
                # so a failure in the pos_kernel_config/pos_config/case_started
                # steps left the card emulating until the next case (the L6
                # cross-case contamination risk).
                await self._stop_card_if_started(session, case.case_id, attempt_id)
            await self.log("info", f"[{case.case_id}] attempt {attempt} -> {outcome}")

            finalize = outcome in ("pass", "warn", "skipped", "aborted", "fail",
                                   "inconclusive", "error")
            if finalize:
                await self._finalize_case(session, case, outcome, attempt)
                break
            # fail with retries left -> loop
        else:  # pragma: no cover - loop always breaks or finalizes
            await self._finalize_case(session, case, outcome, max_attempts)

        if session.fatal_reason or session.abort_event.is_set() or outcome == "aborted":
            return outcome, True
        return outcome, False

    def _kernel_params_for(self, session: RunSession, case: TestCase) -> KernelParams | None:
        """Case-level kernel params override the brand default entirely (R2)."""
        if case.kernel_params is not None:
            return case.kernel_params
        # single lookup point shared with the loader/API (C3 external dir aware)
        return self.library.kernel_params_for(session.brand)

    async def _attempt(self, session: RunSession, case: TestCase,
                       attempt: int, attempt_id: str) -> str:
        # 1. push card config (v1.2: trace_enabled travels here; device_config
        #    is gone, so the card side reads the trace switch from this frame)
        android = self.protocol.get_client(CLIENT_ANDROID)
        if android is None:
            await self.log("error", f"[{case.case_id}] android not connected, cannot run")
            self._fail(session, "android_not_connected")
            return "fail"

        profile = case.profile  # embedded & bound to the case (v2)
        keys = case.keys  # optional embedded keys bundle
        if not profile.id:
            profile.id = case.case_id  # defensive; loader already normalizes

        config_frame = make_frame(
            MSG_CONFIG_PUSH,
            config_type=CONFIG_TYPE_CARD,
            case_id=case.case_id,
            attempt_id=attempt_id,
            profile=profile.model_dump(mode="json"),  # canonical camelCase schema
            keys=None if keys is None else keys.model_dump(mode="json"),  # v1.1 N2
            timeout_seconds=case.timeout_seconds,
            trace_enabled=self.trace_enabled,  # v1.2 R1
        )
        try:
            ack = await android.send_and_wait_ack(config_frame,
                                                  timeout=self.card_ack_timeout)
        except Exception as exc:  # ConnectionClosed / ack timeout
            await self.log("error", f"[{case.case_id}] card config push failed: {exc}")
            self._fail(session, "card_config_ack_timeout")
            return "fail"
        if ack is not None and ack.get("ok") is False:
            await self.log("error", f"[{case.case_id}] card rejected card_config: "
                                    f"{ack.get('detail', '')}")
            self._fail(session, "card_config_rejected")
            return "fail"

        # 2. start emulation + wait for android readiness
        session.current_state = "WAITING_EMULATION"
        session._emulation_waiter = asyncio.get_running_loop().create_future()
        start_frame = make_frame(MSG_START_EMULATION, case_id=case.case_id,
                                 attempt_id=attempt_id)
        try:
            start_ack = await android.send_and_wait_ack(
                start_frame, timeout=self.card_ack_timeout)
        except Exception as exc:
            session._emulation_waiter = None
            await self.log("error", f"[{case.case_id}] start_emulation failed: {exc}")
            self._fail(session, "card_emulation_ack_timeout")
            return "fail"
        if start_ack is not None and start_ack.get("ok") is False:
            session._emulation_waiter = None
            await self.log("error", f"[{case.case_id}] card rejected start_emulation: "
                                    f"{start_ack.get('detail', '')}")
            self._fail(session, "card_emulation_rejected")
            return "fail"
        timed_out = False
        try:
            await self._wait_emulation(session)
        except asyncio.TimeoutError:
            timed_out = True
            session._emulation_waiter = None
            await self.log("error", f"[{case.case_id}] android did not reach "
                                    f"emulating state in "
                                    f"{EMULATION_READY_TIMEOUT:.0f}s")
            self._fail(session, "card_ready_timeout")
            return "fail"
        finally:
            session._emulation_waiter = None
            # H2: the wait only returns when the card reported "emulating" (the
            # abort event short-circuits it too — see _wait_emulation), so from
            # here on every exit path must park the card.
            if not timed_out:
                session.card_emulating = True
        if session.abort_event.is_set():
            # abort during CONFIGURING/WAITING_EMULATION, or android reported
            # status:error while we waited (that path sets failure_reason)
            return "fail" if session.failure_reason else "aborted"

        # 3. CTEL2 (protocol v2): TEST_INFO -> CONFIG -> ACT, each acked, then
        #    wait for the POS OUT.  The POS link no longer uses pos_config /
        #    pos_kernel_config / case_started / transaction_result.
        session.current_state = "POS_CONFIG"
        self.store.write_checkpoint(session.run, case.case_id, attempt_id,
                                    "pos_config")
        if not await self._pos_signal(session, case, make_frame(
                MSG_TEST_INFO,
                signalData=[
                    {"id": "TestId", "value": case.case_id},
                    {"id": "InterfaceVersion", "value": "20250617"},
                    {"id": "SessionId", "value": session.run.get("run_id")},
                    {"id": "DekDetId", "value": None},
                ]), "test_info", MSG_TEST_INFO_ACK):
            return "fail"

        kernel = self._kernel_params_for(session, case)
        if kernel is not None and not kernel.empty:
            if not await self._pos_signal(session, case,
                                          self._config_frame(case, kernel),
                                          "config", MSG_CONFIG_ACK):
                return "fail"

        act = make_frame(MSG_ACT, signalData=[
            p.model_dump() for p in case.transaction.act])
        if not await self._pos_signal(session, case, act, "act", MSG_ACT_ACK):
            return "fail"

        # 4. wait for OUT FIRST, then let the operator tap: the waiter must
        #    exist before the POS can produce the outcome.
        session.current_state = "WAIT_OUT"
        self.store.write_checkpoint(session.run, case.case_id, attempt_id,
                                    "waiting_out")
        session._result_waiter = asyncio.get_running_loop().create_future()
        instructions = self._operator_instructions(case)
        await self.publish("case_status", {
            "case_id": case.case_id, "status": "RUNNING",
            "attempt": attempt, "attempt_id": attempt_id,
            "state": "WAIT_OUT",
            "operator_instructions": instructions,
        })

        # 5. wait for OUT / abort / skip / timeout
        abort_wait = asyncio.create_task(session.abort_event.wait())
        skip_wait = asyncio.create_task(session.skip_event.wait())
        try:
            done, _ = await asyncio.wait(
                {session._result_waiter, abort_wait, skip_wait},
                timeout=case.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                task.cancel()
            if session._result_waiter in done and session._result_waiter.done() \
                    and not session._result_waiter.cancelled():
                outcome = session._result_waiter.result()
                if outcome == "fail":
                    self._fail(session, session.failure_reason or "assertion_failed")
                return outcome
            if session.abort_event.is_set():
                if session.failure_reason:
                    return "fail"
                return "aborted"
            if session.skip_event.is_set():
                return "skipped"
            await self.log("error", f"[{case.case_id}] no OUT within "
                                    f"{case.timeout_seconds}s")
            session.failure_reason = session.failure_reason or "out_timeout"
            self._fail(session, "out_timeout")
            return "fail"
        finally:
            session._result_waiter = None
            abort_wait.cancel()
            skip_wait.cancel()
            session.skip_event.clear()
            # NOTE: the card is parked by _run_case's finally
            # (_stop_card_if_started), which also covers the earlier failure
            # paths (TS-5/H2).  Do not stop here as well.

    async def _pos_signal(self, session: RunSession, case: TestCase,
                          frame: dict[str, Any], name: str,
                          expect_type: str) -> bool:
        """Send one CTEL2 signal and require a YES ack.  False on failure."""
        try:
            ack = await self.protocol.send_to_and_ack_signal(
                CLIENT_POS, frame, expect_type, timeout=self.pos_ack_timeout)
        except ConnectionClosed as exc:
            # peer did not answer within the budget (or the link dropped)
            await self.log("error", f"[{case.case_id}] {name} ack timeout: {exc}")
            self._fail(session, f"{name}_ack_timeout")
            return False
        except Exception as exc:  # local/programming/transport error
            await self.log("error", f"[{case.case_id}] {name} signal error: {exc}")
            self._fail(session, f"{name}_error")
            return False
        if ack is None:
            await self.log("error", f"[{case.case_id}] POS not connected; "
                                    f"{name} 未确认")
            self._fail(session, "pos_not_connected")
            return False
        code = self._response_code(ack)
        if code != "YES":
            await self.log("error", f"[{case.case_id}] POS rejected {name}: {code}")
            self._fail(session, "act_not_supported"
                       if name == "act" and code == RESP_NOTSUPPORTED
                       else f"{name}_rejected")
            return False
        return True

    @staticmethod
    def _response_code(ack: dict[str, Any]) -> str:
        for item in ack.get("signalData") or []:
            if isinstance(item, dict) \
                    and str(item.get("id", "")).upper() == "RESPONSECODE":
                return str(item.get("value", "")).upper()
        return ""

    @staticmethod
    def _config_frame(case: TestCase, kernel: KernelParams) -> dict[str, Any]:
        """CTEL2 CONFIG with the full config embedded (R2=b, design section 4)."""
        payload = kernel.payload()
        signal: list[dict[str, Any]] = [
            {"id": CONFIG_ID_NAME,
             "value": case.kernel_params_ref or case.case_id},
        ]
        for tlv in payload["aid"]:
            signal.append({"id": CONFIG_ID_AID, "value": tlv})
        for tlv in payload["capk"]:
            signal.append({"id": CONFIG_ID_CAPK, "value": tlv})
        # D1: DRL / EXCEPTION_FILE are ALWAYS present.  An empty list is sent as
        # one item with value="" = "clear the table and add nothing" (needed for
        # CONF_01_RemoveCRL); a missing id would mean "do not touch".
        if payload["drl"]:
            for tlv in payload["drl"]:
                signal.append({"id": CONFIG_ID_DRL, "value": tlv})
        else:
            signal.append({"id": CONFIG_ID_DRL, "value": ""})
        if payload["exception_file"]:
            for tlv in payload["exception_file"]:
                signal.append({"id": CONFIG_ID_EXCEPTION_FILE, "value": tlv})
        else:
            signal.append({"id": CONFIG_ID_EXCEPTION_FILE, "value": ""})
        if kernel.term:
            signal.append({"id": CONFIG_ID_TERM, "value": kernel.term})
        if kernel.version:
            signal.append({"id": CONFIG_ID_VERSION, "value": kernel.version})
        return make_frame(MSG_CONFIG, signalData=signal)

    async def _stop_card_if_started(self, session: RunSession, case_id: str,
                                    attempt_id: str) -> None:
        """Park the card once per attempt, but only if it ever started (TS-5).

        ``card_emulating`` is set as soon as the android reports ``emulating``;
        from that moment any exit path (fail, timeout, abort, exception) must
        send ``stop_emulation``, otherwise the HCE session can outlive the case
        and leak into the next one.  Cases that never reached ``emulating``
        (card not connected, ack timeout, profile rejected) must NOT send it.
        """
        if not session.card_emulating:
            return
        session.card_emulating = False
        await self._send_stop(CLIENT_ANDROID, case_id, attempt_id)

    async def _wait_emulation(self, session: RunSession) -> None:
        """Wait for the android "emulating" status, abortable (v1.2).

        The abort event is part of the wait so an operator abort (or a device
        loss) does not have to sit out the full readiness timeout.
        """
        if session._emulation_waiter is None:
            return
        abort_wait = asyncio.create_task(session.abort_event.wait())
        try:
            await asyncio.wait_for(
                asyncio.wait({session._emulation_waiter, abort_wait},
                             return_when=asyncio.FIRST_COMPLETED),
                EMULATION_READY_TIMEOUT)
        finally:
            abort_wait.cancel()

    async def _send_stop(self, client_type: str, case_id: str,
                         attempt_id: str) -> None:
        frame = make_frame(MSG_STOP_EMULATION, case_id=case_id, attempt_id=attempt_id)
        try:
            await self.protocol.send_to_and_ack(client_type, frame,
                                                timeout=self.stop_ack_timeout)
        except Exception as exc:  # noqa: BLE001 - best effort on abort paths
            await self.log("debug", f"stop_emulation ack not received: {exc}")

    def _judge(self, session: RunSession, case: TestCase,
               signal_data: Any, frame: dict[str, Any]) -> tuple[str, str]:
        """Build the v2 result from one OUT and judge + PERSIST it.

        The result carries ``out.ops`` / ``out.datarecord`` / ``out.discdata``
        plus the derived ``outcome`` / ``online`` / ``resp_code``; ``card`` is
        filled by the APDU judge (P2).
        """
        try:
            result = build_result(signal_data if isinstance(signal_data, list)
                                  else [],
                                  card=build_card_view(
                                      session.card_apdus.get("apdus")))
        except ValueError as exc:
            self.store.write_quarantine(session.run, {
                "at": time.time(), "reason": str(exc), "frame": frame})
            return "fail", "out_invalid"
        record = {
            "case_id": case.case_id,
            # F3: both write paths emit BOTH keys  attempt is 0-based (which try
            # it was), attempts is the 1-based count.
            "attempt": session.current_attempt,
            "attempts": session.current_attempt + 1,
            "attempt_id": session.current_attempt_id,
            "received_at": time.time(),
            "raw_result": result,
            "pos_meta": {k: frame.get(k) for k in
                         ("device_id", "app_version", "ts", "msg_id")},
        }
        # D2 (owner choice a): the kernel did not produce an outcome
        # (OPS status bits = 0000 + empty DataRecord) -> the case FAILs with an
        # explicit reason instead of a fabricated decline code.
        no_outcome = (result["out"]["ops"].get("status_bits") == "0000"
                      and not result["out"]["datarecord"])
        verdict = assert_result(result, case.expected_results)
        record["verdict"] = verdict.public()
        record["status"] = verdict.status
        record["finished_at"] = time.time()
        if no_outcome and verdict.status != "PASS":
            record["failure_reason"] = "out_no_outcome"
        elif verdict.status == "FAIL":
            record["failure_reason"] = "assertion_failed"
        self.store.write_case_result(session.run, case.case_id, record)
        # v2 Q8: MERGE into the summary write_case_result just wrote instead of
        # replacing it -- that summary also carries the OUT outcome
        # (ops_status / resp_code) which the live session snapshot streams, plus
        # the failure_reason the run view shows while the run is still going.
        # ``verdict`` stays the plain status string here (the full rule list is
        # in results/<case_id>.json).
        session.run.setdefault("results", {}).setdefault(
            case.case_id, {}).update({
                "status": verdict.status,
                "attempts": session.current_attempt + 1,
                "verdict": verdict.status,
                "finished_at": record["finished_at"],
            })
        self.store.update_run(session.run)
        if verdict.status == "FAIL":
            session.failure_reason = session.failure_reason or (
                "out_no_outcome" if no_outcome else "assertion_failed")
        elif no_outcome:
            session.failure_reason = session.failure_reason or "out_no_outcome"
        return {"PASS": "pass", "WARN": "warn", "FAIL": "fail"}[verdict.status], \
            "persisted"

    async def _finalize_case(self, session: RunSession, case: TestCase,
                             outcome: str, attempt: int) -> None:
        if outcome == "fail" and session.failure_reason == "android_error":
            # v1.2 D5: an android-side error keeps status ERROR and the
            # structured error{code,message} recorded by _on_android_status
            outcome = "error"
        status = {
            "pass": "PASS", "warn": "WARN", "fail": "FAIL",
            "skipped": "SKIPPED", "aborted": "ABORTED",
            "inconclusive": "INCONCLUSIVE", "error": "ERROR",
        }[outcome]
        session.run.setdefault("results", {}).setdefault(case.case_id, {}).update({
            "status": status, "attempts": attempt + 1,
            "finished_at": time.time(),
        })
        if self.store.get_case_result(session.run["run_id"], case.case_id) is None:
            # PASS/WARN/FAIL already persisted by _on_transaction_result with
            # full assertion detail (v1.2 D2); only persist the outcomes that
            # never reached a result here.
            record = {
                "case_id": case.case_id, "status": status,
                # F3: same schema as _judge — attempt (0-based) + attempts (count)
                "attempt": attempt, "attempts": attempt + 1,
                "finished_at": time.time(),
                "note": {
                    "inconclusive": "no transaction result within timeout",
                    "aborted": "run aborted",
                    "skipped": "operator skipped",
                    "error": "execution error (see log)",
                }.get(outcome, ""),
            }
            if outcome in ("fail", "error") and session.failure_reason:
                record["failure_reason"] = session.failure_reason
            if outcome == "error":
                android_error = session.run.get("results", {}).get(
                    case.case_id, {}).get("android_error")
                if android_error:
                    record["error"] = android_error
            self.store.write_case_result(session.run, case.case_id, record)
        else:
            # keep the pre-existing record's failure_reason visible in the index
            existing = self.store.get_case_result(
                session.run["run_id"], case.case_id) or {}
            if existing.get("failure_reason"):
                session.run["results"][case.case_id]["failure_reason"] = \
                    existing["failure_reason"]
            self.store.update_run(session.run)
        session.end_attempt()
        await self.publish("case_status", {
            "case_id": case.case_id, "status": status,
            "failure_reason": session.failure_reason if status in ("FAIL", "ERROR") else None,
        })
        await self.log("info" if status in ("PASS", "WARN") else "warn",
                       f"[{case.case_id}] final: {status}")
