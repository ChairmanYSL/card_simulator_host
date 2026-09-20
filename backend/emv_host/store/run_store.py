"""Run persistence: JSON files under runs/<date>/<run_id>/.

Layout::

    runs/
      2025-09-01/
        20250901T120000_abc123/
          run.json          summary + status + per-case records
          checkpoint.json   write-ahead: which case starts next (resume source)
          results/<case_id>.json   full result per case
          quarantine.jsonl  late/unattributed transaction results
          host.log          run-scoped log excerpt

The global history index is built by scanning run.json files.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("emv_host.store")


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` as indented JSON without ever leaving a partial file.

    A crash or a full disk during ``write_text`` used to truncate run.json /
    results/*.json, which then failed to parse on the next boot (the run became
    unrecoverable).  Write to a sibling ``.tmp`` and ``os.replace`` it into
    place — ``os.replace`` is atomic on the same filesystem, so a reader sees
    either the old file or the complete new one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        # never leave a stray .tmp behind (including on CancelledError)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort cleanup
            pass
        raise


class RunStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- run lifecycle ------------------------------------------------------
    def new_run(self, brand: str, mode: str, case_ids: list[str],
                kind: str) -> dict[str, Any]:
        run_id = f"{time.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
        run_dir = self.root / time.strftime("%Y-%m-%d") / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "results").mkdir(exist_ok=True)
        run = {
            "run_id": run_id,
            "kind": kind,  # full | selected | single | resume
            "brand": brand,
            "mode": mode,
            "status": "PENDING",
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "queue": list(case_ids),
            "results": {},   # case_id -> {status, attempt, verdict, ...}
            "abort_reason": None,
            "resumed_from": None,
        }
        self._write_run(run)
        return run

    @staticmethod
    def run_dir(root: Path, run_id: str) -> Path:
        for day_dir in sorted(root.iterdir()) if root.is_dir() else []:
            candidate = day_dir / run_id
            if candidate.is_dir():
                return candidate
        raise FileNotFoundError(f"run {run_id} not found")

    def _run_path(self, run: dict[str, Any]) -> Path:
        return self.root / time.strftime("%Y-%m-%d", time.localtime(run["created_at"])) \
            / run["run_id"] / "run.json"

    def _write_run(self, run: dict[str, Any]) -> None:
        path = self._run_path(run)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, run)

    def update_run(self, run: dict[str, Any]) -> None:
        self._write_run(run)

    # -- checkpoint (write-ahead) -------------------------------------------
    def write_checkpoint(self, run: dict[str, Any], next_case_id: str | None,
                         attempt_id: str, state: str) -> None:
        checkpoint = {
            "run_id": run["run_id"],
            "next_case_id": next_case_id,
            "attempt_id": attempt_id,
            "state": state,  # upcoming | waiting_result | done
            "written_at": time.time(),
        }
        path = self._run_path(run).parent / "checkpoint.json"
        _atomic_write_json(path, checkpoint)

    def read_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        try:
            path = self.run_dir(self.root, run_id) / "checkpoint.json"
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    # -- per-case results ---------------------------------------------------
    def write_case_result(self, run: dict[str, Any], case_id: str,
                          record: dict[str, Any]) -> None:
        path = self._run_path(run).parent / "results" / f"{case_id}.json"
        _atomic_write_json(path, record)
        # v1.2: the run-level index also carries failure_reason so the history
        # list and the web UI can explain a stop without opening results/*.json
        summary = {
            "status": record.get("status"),
            "attempts": record.get("attempts"),
            "verdict": record.get("verdict"),
            "finished_at": record.get("finished_at"),
        }
        if record.get("failure_reason"):
            summary["failure_reason"] = record["failure_reason"]
        # v2 (design section 11 Q8, minimal scope): the run-level index is what
        # the LIVE session snapshot streams, so the OPS outcome has to travel in
        # it -- otherwise the run view cannot show it until the run is over.
        # Only present keys are copied (a v1.2-shaped record has no raw_result at
        # all), so partial records keep exactly their current summary.
        raw_result = record.get("raw_result")
        if isinstance(raw_result, dict):
            out = raw_result.get("out")
            ops = out.get("ops") if isinstance(out, dict) else None
            if isinstance(ops, dict):
                if ops.get("status"):
                    summary["ops_status"] = ops["status"]
                if ops.get("status_bits"):
                    summary["ops_status_bits"] = ops["status_bits"]
            if raw_result.get("resp_code"):
                summary["resp_code"] = raw_result["resp_code"]
        run.setdefault("results", {})[case_id] = summary
        self._write_run(run)

    def write_quarantine(self, run: dict[str, Any], entry: dict[str, Any]) -> None:
        path = self._run_path(run).parent / "quarantine.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- APDU trace (v1.1 H2, Phase A; diagnostic only, never judged) --------
    def write_trace(self, run: dict[str, Any], case_id: str, attempt_id: str,
                    entry: dict[str, Any]) -> None:
        path = self._run_path(run).parent / "trace" / f"{case_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**entry, "attempt_id": attempt_id},
                                ensure_ascii=False) + "\n")

    def read_trace(self, run_id: str, case_id: str) -> list[dict[str, Any]]:
        try:
            path = self.run_dir(self.root, run_id) / "trace" / f"{case_id}.jsonl"
            return [json.loads(line) for line in
                    path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    # -- history ------------------------------------------------------------
    def history(self, limit: int = 200) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if not self.root.is_dir():
            return items
        for run_json in self.root.glob("*/*/run.json"):
            try:
                data = json.loads(run_json.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            items.append(self._summary(data))
        items.sort(key=lambda r: r.get("created_at", 0), reverse=True)
        return items[:limit]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        try:
            path = self.run_dir(self.root, run_id) / "run.json"
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def get_case_result(self, run_id: str, case_id: str) -> dict[str, Any] | None:
        try:
            path = self.run_dir(self.root, run_id) / "results" / f"{case_id}.json"
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    @staticmethod
    def _summary(run: dict[str, Any]) -> dict[str, Any]:
        results: dict[str, Any] = run.get("results", {})
        statuses = [r.get("status") for r in results.values() if r.get("status")]
        return {
            "run_id": run.get("run_id"),
            "kind": run.get("kind"),
            "brand": run.get("brand"),
            "mode": run.get("mode"),
            "status": run.get("status"),
            "created_at": run.get("created_at"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "abort_reason": run.get("abort_reason"),
            "resumed_from": run.get("resumed_from"),
            "total": len(run.get("queue", [])),
            "passed": statuses.count("PASS"),
            "failed": statuses.count("FAIL"),
            "warned": statuses.count("WARN"),
            "aborted": statuses.count("ABORTED"),
            "skipped": statuses.count("SKIPPED"),
            "inconclusive": statuses.count("INCONCLUSIVE"),
            "errored": statuses.count("ERROR"),
        }

    # -- boot repair --------------------------------------------------------
    def recover_interrupted_runs(self) -> list[str]:
        """Mark any run left RUNNING by a previous process as interrupted and
        return their run ids (their checkpoint is the resume source)."""
        recovered: list[str] = []
        if not self.root.is_dir():
            return recovered
        for run_json in self.root.glob("*/*/run.json"):
            try:
                run = json.loads(run_json.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if run.get("status") not in ("RUNNING", "PENDING"):
                continue
            checkpoint = self.read_checkpoint(run["run_id"])
            current = checkpoint.get("next_case_id") if checkpoint else None
            if current:
                run.setdefault("results", {})[current] = {
                    "status": "INCONCLUSIVE",
                    "note": "server restarted mid-case (checkpoint recovery)",
                }
                logger.warning("recovered run %s: case %s marked INCONCLUSIVE",
                               run["run_id"], current)
            run["status"] = "INTERRUPTED"
            run["finished_at"] = time.time()
            run["abort_reason"] = "server restart"
            self._write_run(run)
            recovered.append(run["run_id"])
        return recovered
