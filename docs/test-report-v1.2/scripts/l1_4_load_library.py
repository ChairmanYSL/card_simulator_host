"""L1-4 case-library cold load (test-plan-v1.2.md 2/L1-4).

Run from <repo>/backend so that `emv_host` resolves without PYTHONPATH.
"""

from emv_host.cases import load_library

lib = load_library(r"E:\Work\ZT\card_simulator_host\cases")
print("brands:", sorted(lib.brands))
print("errors:", [i.message for i in lib.issues if i.severity == "error"])
print("visa cases:", lib.all_case_ids("visa", "contactless"))
