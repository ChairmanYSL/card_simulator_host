"""L1-4 supplement: dump every brand's case ids and all issues (not just errors).

Extra evidence beyond the plan's minimal assertion, so the report can show the
whole library loaded and that no brand silently came back empty.
"""

from emv_host.cases import load_library

lib = load_library(r"E:\Work\ZT\card_simulator_host\cases")
print("brand count:", len(lib.brands))
for brand in sorted(lib.brands):
    modes = sorted(lib.entry_modes_for(brand))
    print(f"--- {brand} ---")
    for mode in modes:
        try:
            ids = lib.all_case_ids(brand, mode)
        except Exception as exc:  # noqa: BLE001
            print(f"  {mode}: ERROR {exc}")
            continue
        print(f"  {mode} ({len(ids)}): {ids}")
print("=== issues ===")
for issue in lib.issues:
    print(f"[{issue.severity}] {issue.path}: {issue.message}")
print("=== end ===")
