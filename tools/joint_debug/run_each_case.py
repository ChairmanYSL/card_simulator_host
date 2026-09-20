"""Run every case as its own run (one run per case) and summarise.

Why this exists
---------------
The v2 runner hard-stops a run on the first FAIL (``executor.py`` Q9:
``if stop_after or ... or outcome in ("fail", "aborted"): break``) and marks
every still-queued case SKIPPED.  A brand-wide run therefore never reaches its
later cases while any case fails, which makes "collect the full failure
inventory" impossible in a single brand run.

Driving one run per case gives a complete inventory in one pass: each case is
independent (its own run_id), so a FAIL cannot hide the cases after it.

Usage
-----
    python tools/joint_debug/run_each_case.py --brand visa
    python tools/joint_debug/run_each_case.py --all --settle 3
    python tools/joint_debug/run_each_case.py --all --out logs/joint_debug/each.json

Only the REST API is used (no backend imports).
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TERMINAL = {"FINISHED", "STOPPED", "ABORTED", "INTERRUPTED"}


def api(base: str, path: str, method: str = "GET", payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def row_of(detail: dict, case_id: str) -> dict:
    record = (detail.get("results") or {}).get(case_id) or {}
    run = detail.get("run") or {}
    raw = record.get("raw_result") or {}
    ops = ((raw.get("out") or {}).get("ops")) or {}
    return {
        "case_id": case_id,
        "status": record.get("status") or run.get("status") or "?",
        "failure_reason": record.get("failure_reason") or "",
        "ops_status": ops.get("status") or "",
        "resp_code": raw.get("resp_code") if raw.get("resp_code") is not None else "",
    }


def run_one(base: str, brand: str, mode: str, case_id: str, timeout: float) -> dict:
    body = {"brand": brand, "mode": mode, "kind": "single", "case_ids": [case_id],
            "on_failure": "stop", "retry_count": 0}
    run_id = None
    for attempt in range(20):
        try:
            run_id = api(base, "/api/run", "POST", body)["run_id"]
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 409 and attempt < 19:
                time.sleep(4)
                continue
            return {"case_id": case_id, "status": "START-FAILED",
                    "failure_reason": f"http {exc.code}"}
    if run_id is None:
        return {"case_id": case_id, "status": "START-FAILED", "failure_reason": "busy"}

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            session = (api(base, "/api/run") or {}).get("session")
        except Exception:
            time.sleep(1.5)
            continue
        if session is None or session.get("run_id") != run_id:
            detail = api(base, f"/api/history/{run_id}")
            if ((detail.get("run") or {}).get("status")) in TERMINAL:
                return row_of(detail, case_id)
            time.sleep(1.0)
            continue
        if session.get("run_status") in TERMINAL:
            return row_of(api(base, f"/api/history/{run_id}"), case_id)
        time.sleep(1.0)
    return {"case_id": case_id, "status": "TIMEOUT", "failure_reason": f">{timeout:.0f}s"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--brand")
    parser.add_argument("--mode", default="contactless")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--settle", type=float, default=3.0,
                        help="seconds between cases (let the POS finish the previous tap)")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    tree = api(args.base, "/api/tree")
    targets: list[tuple[str, str, str]] = []
    for brand_node in tree["brands"]:
        for mode_node in brand_node["entry_modes"]:
            take = args.all or (args.brand and brand_node["brand"] == args.brand
                                and mode_node["mode"] == args.mode)
            if not take:
                continue
            ids = [c["case_id"] for c in mode_node["cases"]]
            if args.case:
                ids = [c for c in ids if c in args.case]
            targets.extend((brand_node["brand"], mode_node["mode"], c) for c in ids)

    print(f"== {len(targets)} cases, one run each ==", flush=True)
    rows: list[dict] = []
    try:
        for index, (brand, mode, case_id) in enumerate(targets, 1):
            row = run_one(args.base, brand, mode, case_id, args.timeout)
            rows.append(row)
            print(f"  [{index:2d}/{len(targets)}] {case_id:<16} {row['status']:<12} "
                  f"{row['failure_reason']:<30} ops={row.get('ops_status','')} "
                  f"resp={row.get('resp_code','')!r}", flush=True)
            if args.settle:
                time.sleep(args.settle)
    finally:
        passed = sum(1 for r in rows if r["status"] == "PASS")
        print(f"\n== PASS {passed}/{len(rows)} ==")
        for r in rows:
            if r["status"] != "PASS":
                print(f"  NOT-PASS {r['case_id']:<16} {r['status']:<12} {r['failure_reason']}")
        out = Path(args.out) if args.out else (
            REPO / "logs" / "joint_debug" / f"each_{time.strftime('%Y%m%dT%H%M%S')}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"summary: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
