"""L2 section 3.4 boundary assertions U-1..U-7 (test-plan-v1.2.md).

Each assertion prints one PASS/FAIL line and the script writes a JSON summary so
the report can quote machine-readable results.  Run with backend on PYTHONPATH.
"""

import json
import sys

from emv_host.cases.models import AidKernelParam, CapkParam, KernelParams
from emv_host.cases.validation import validate_kernel_params
from emv_host.protocol.kernel_tlv import (
    ber_length,
    compute_capk_checksum,
    decode_tlv_map,
    encode_capk,
)

results: list[dict] = []


def check(uid: str, name: str, ok: bool, detail: str) -> None:
    results.append({"id": uid, "name": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {uid} {name}")
    print(f"        {detail}")


# -- U-1: BER length encoding -------------------------------------------------
u1_ok = (ber_length(0x7F) == b"\x7f"
         and ber_length(0x80) == b"\x81\x80"
         and ber_length(248) == b"\x81\xf8")
check("U-1", "ber_length short/long form",
      u1_ok,
      f"0x7F={ber_length(0x7F)!r} 0x80={ber_length(0x80)!r} 248={ber_length(248)!r}")

# -- U-2: 248-byte modulus CAPK round-trips through decode_tlv_map ------------
modulus_248 = "AB" * 248
cap = CapkParam(rid="A000000003", key_index="01", expiry="20441231",
                modulus=modulus_248, exponent="03")
encoded = encode_capk(cap)
decoded = decode_tlv_map(encoded)
roundtrip = decoded.get("DF02") == modulus_248
check("U-2", "248B modulus encode->decode round-trip",
      roundtrip and len(decoded.get("DF02", "")) // 2 == 248,
      f"DF02 len={len(decoded.get('DF02', '')) // 2}B identical={roundtrip}; "
      f"tags={sorted(decoded)}")

# -- U-3: missing checksum is auto-computed and equals SHA1(rid||idx||mod||exp)
cap_no_cs = CapkParam(rid="A000000003", key_index="01", expiry="20441231",
                      modulus=modulus_248, exponent="03")  # checksum defaults ""
enc_no_cs = encode_capk(cap_no_cs)
dec_no_cs = decode_tlv_map(enc_no_cs)
expected_cs = compute_capk_checksum("A000000003", "01", modulus_248, "03")
check("U-3", "auto DF03 == SHA1(9F06||9F22||DF02||DF04)",
      dec_no_cs.get("DF03") == expected_cs,
      f"DF03={dec_no_cs.get('DF03')} expected={expected_cs}")

# -- U-4: DF05="PHWWIPQI" rejected at the MODEL layer -------------------------
u4_raised = False
u4_msg = ""
try:
    CapkParam(rid="A000000333", key_index="08", expiry="PHWWIPQI",
              modulus=modulus_248, exponent="03")
except ValueError as exc:
    u4_raised = True
    u4_msg = str(exc).splitlines()[0]
check("U-4", "DF05=PHWWIPQI rejected at model layer (ValueError)",
      u4_raised, f"raised={u4_raised} msg={u4_msg!r}")

# -- U-5: 9F06 9 bytes rejected; 7 and 8 bytes accepted -----------------------
u5_rej9 = False
try:
    AidKernelParam(aid="A0" * 9)
except ValueError:
    u5_rej9 = True
u5_acc7 = AidKernelParam(aid="A0" * 7).aid == "A0" * 7
u5_acc8 = AidKernelParam(aid="A0" * 8).aid == "A0" * 8
check("U-5", "9F06: 9B rejected, 7B/8B accepted",
      u5_rej9 and u5_acc7 and u5_acc8,
      f"9B rejected={u5_rej9} 7B accepted={u5_acc7} 8B accepted={u5_acc8}")

# -- U-6: validate_kernel_params(None, x) produces no issue at all -----------
rep_none = validate_kernel_params(None, "visa")
check("U-6", "validate_kernel_params(None, x) -> no issues",
      len(rep_none.issues) == 0,
      f"issues={[i.message for i in rep_none.issues]}")

# -- U-7: empty KernelParams().empty is True ---------------------------------
kp = KernelParams()
check("U-7", "KernelParams().empty is True",
      kp.empty is True,
      f"empty={kp.empty} aids={kp.aids} caps={kp.caps} term={kp.term!r}")

failed = [r["id"] for r in results if not r["ok"]]
print()
print(f"=== U-1..U-7: {len(results) - len(failed)}/{len(results)} passed; "
      f"failed={failed} ===")

if len(sys.argv) > 1:
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump({"results": results, "failed": failed}, fh,
                  ensure_ascii=False, indent=2)
    print(f"wrote {sys.argv[1]}")

sys.exit(1 if failed else 0)
