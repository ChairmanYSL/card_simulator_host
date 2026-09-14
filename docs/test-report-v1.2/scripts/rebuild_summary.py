"""Rebuild EVID/L3_summary.json from the per-scenario evidence files.

The live l3_scenarios.py main() overwrites its summary with only the scenarios
named in that invocation, so after re-running individual scenarios the summary
no longer reflects the round.  This regenerates it from the evidence itself.
"""

from __future__ import annotations

import json
from pathlib import Path

EV = Path(r"E:\Work\ZT\card_simulator_host\docs\test-report-v1.2\evidence")

ORDER = [
    "A_single_pass", "B_declined_offline_pass", "C_approved_online_pass",
    "D_assertion_failed_stops_run", "E1_android_not_connected",
    "E2_card_config_ack_timeout", "F_pos_not_connected", "G_result_timeout",
    "H_fail_then_skipped", "I_kind_group", "J_manual_abort",
    "M_pos_ack_deadlock", "handshake_hard_switch",
    "TD0_reverse_no_kernel_params", "TD0_fail_plan_example_term",
    "TD0_fail_odd_length_term", "TD0_fail_reject_kernel",
]

summary = []
total_checks = 0
for name in ORDER:
    p = EV / f"L3_{name}.json"
    if not p.is_file():
        summary.append({"scenario": name, "overall_ok": None,
                        "error": "evidence file missing"})
        continue
    blob = json.loads(p.read_text(encoding="utf-8"))
    checks = blob.get("checks", [])
    failed = [c["name"] for c in checks if not c["ok"]]
    total_checks += len(checks)
    summary.append({"scenario": name, "overall_ok": blob.get("overall_ok"),
                    "checks": len(checks), "failed": failed,
                    "run_id": blob.get("run_id")})

L4 = [
    "L4_1_bad_frames", "L4_2_duplicate_result",
    "L4_2b_late_resend_is_quarantined",
    "L4_3a_persist_failed_then_timeout",
    "L4_3b_persist_failed_then_retransmit_passes",
    "L4_4a_android_disconnect", "L4_4b_pos_disconnect",
    "L4_5_loader_tolerance", "L4_6_atomic_no_tmp",
]
l4_summary = []
l4_checks = 0
for name in L4:
    p = EV / f"L3_{name}.json"
    blob = json.loads(p.read_text(encoding="utf-8"))
    checks = blob.get("checks", [])
    l4_checks += len(checks)
    l4_summary.append({"scenario": name, "overall_ok": blob.get("overall_ok"),
                       "checks": len(checks),
                       "failed": [c["name"] for c in checks if not c["ok"]]})

failed = [s["scenario"] for s in summary + l4_summary
          if not s.get("overall_ok")]
out = {
    "l3_l4_scenarios": summary,
    "l4_scenarios": l4_summary,
    "l3_check_count": total_checks,
    "l4_check_count": l4_checks,
    "total_check_count": total_checks + l4_checks,
    "failed_scenarios": failed,
    "all_passed": not failed,
}
(EV / "L3_summary.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"L3 scenarios: {len(summary)}  checks: {total_checks}")
print(f"L4 scenarios: {len(l4_summary)}  checks: {l4_checks}")
print(f"TOTAL checks: {total_checks + l4_checks}")
print(f"failed scenarios: {failed or 'none'}")
print(f"all_passed: {not failed}")
for s in summary + l4_summary:
    flag = "ok  " if s.get("overall_ok") else "FAIL"
    print(f"  [{flag}] {s['scenario']:<46} checks={s.get('checks')} "
          f"{s.get('failed') or ''}")
