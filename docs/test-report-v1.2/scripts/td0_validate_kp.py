"""T-D0 section 6.3: validate the generated cases/visa/kernel_params.json.

Uses the host's own model + validation so the verdict is produced by the code
under test, not by the generator.
"""

import json
import sys

from emv_host.cases.models import KernelParams
from emv_host.cases.validation import validate_kernel_params

path = sys.argv[1] if len(sys.argv) > 1 else \
    r"E:\Work\ZT\card_simulator_host\cases\visa\kernel_params.json"

raw = json.load(open(path, encoding="utf-8"))
kp = KernelParams.model_validate(raw)
rep = validate_kernel_params(kp, "visa")

print("file    :", path)
print("errors  :", [i.message for i in rep.errors])
print("warnings:", [i.message for i in rep.warnings])
print("AID", len(kp.aids), "CAPK", len(kp.caps), "term", bool(kp.term))
print("empty   :", kp.empty)

# wire-form sanity: aid[0] must start with 9F06 (section 3.2 red line)
payload = kp.payload()
print("payload aid[0] :", payload["aid"][0][:24], "...")
print("payload capk[0]:", payload["capk"][0][:24], "...")
print("payload term   :", payload["term"])
print("aid[0] starts with 9F06:", payload["aid"][0].startswith("9F06"))
print("error_count:", len(rep.errors))
sys.exit(1 if rep.errors else 0)
