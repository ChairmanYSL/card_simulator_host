"""T-D0 section 6.2: build cases/visa/kernel_params.json from the POS asset file.

This is THROWAWAY test-data preparation for the v1.2 test round, deliberately
kept out of tools/ so it cannot be mistaken for the P2 deliverable
(tools/export_kernel_params.py, which the test plan says is NOT implemented and
must NOT be tested).

Read-only on the POS repo.  Writes only the gitignored
cases/visa/kernel_params.json.

Usage:
    python build_kernel_params.py <asset-file> <out-json> [--brand visa]

Parsing note: this script uses its OWN ordered BER-TLV walker rather than
emv_host's decode_tlv_map, so the generated data is not produced by the same
code that is under test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# The one asset entry the test plan (section 6.2) explicitly forbids copying:
# RID A000000333 / key index 08 has DF05 = "PHWWIPQI" and is rejected at the
# model layer (asserted by U-4).
FORBIDDEN = ("A000000333", "08")
MODULUS_LENGTHS = (128, 144, 176, 248)


def walk_tlv(hexstr: str) -> list[tuple[str, str]]:
    """Ordered parse of concatenated BER-TLV: [(TAG, VALUE_HEX), ...]."""
    raw = bytes.fromhex(hexstr.strip().upper().replace(" ", ""))
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(raw):
        first = raw[pos]
        if first & 0x1F == 0x1F:
            if pos + 1 >= len(raw):
                raise ValueError("truncated two-byte tag")
            tag = raw[pos:pos + 2].hex().upper()
            pos += 2
        else:
            tag = raw[pos:pos + 1].hex().upper()
            pos += 1
        if pos >= len(raw):
            raise ValueError(f"truncated length for tag {tag}")
        l0 = raw[pos]
        pos += 1
        if l0 & 0x80:
            n = l0 & 0x7F
            if n == 0 or n > 2 or pos + n > len(raw):
                raise ValueError(f"unsupported/truncated length for tag {tag}")
            length = int.from_bytes(raw[pos:pos + n], "big")
            pos += n
        else:
            length = l0
        if pos + length > len(raw):
            raise ValueError(f"tag {tag} length {length} overruns")
        out.append((tag, raw[pos:pos + length].hex().upper()))
        pos += length
    return out


def read_sections(path: Path) -> tuple[list[str], list[str], list[str]]:
    aids: list[str] = []
    caps: list[str] = []
    terms: list[str] = []
    section = None
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        if s == "AID=":
            section = aids
        elif s == "CAPK=":
            section = caps
        elif s == "TERM=":
            section = terms
        elif section is None:
            raise ValueError(f"line {lineno}: data before any section header")
        else:
            section.append(s)
    return aids, caps, terms


def to_aid(line: str) -> dict:
    tlvs = walk_tlv(line)
    if tlvs[0][0] != "9F06":
        raise ValueError(f"AID entry does not start with 9F06: {line[:20]}...")
    aid_value = tlvs[0][1]
    fields = [{"tag": t, "value": v} for t, v in tlvs[1:]]
    if any(f["tag"] == "9F06" for f in fields):
        raise ValueError(f"AID {aid_value}: 9F06 appears again in fields")
    return {"aid": aid_value, "fields": fields}


def to_capk(line: str) -> dict:
    tlvs = walk_tlv(line)
    m = {}
    for tag, value in tlvs:
        if tag in m:
            raise ValueError(f"duplicate tag {tag} in CAPK entry")
        m[tag] = value
    for required in ("9F06", "9F22", "DF05", "DF06", "DF07", "DF02", "DF04", "DF03"):
        if required not in m:
            raise ValueError(f"CAPK entry missing {required}")
    rid = m["9F06"]
    idx = m["9F22"]
    expiry = bytes.fromhex(m["DF05"]).decode("ascii")
    modulus = m["DF02"]
    exponent = m["DF04"]
    mb = len(modulus) // 2
    computed = hashlib.sha1(
        bytes.fromhex(rid) + bytes.fromhex(idx)
        + bytes.fromhex(modulus) + bytes.fromhex(exponent)
    ).hexdigest().upper()
    return {
        "rid": rid, "key_index": idx, "expiry": expiry,
        "hash_alg": int(m["DF06"], 16), "key_alg": int(m["DF07"], 16),
        "modulus": modulus, "exponent": exponent, "checksum": m["DF03"],
        "_asset_checksum_ok": computed == m["DF03"],
        "_modulus_bytes": mb,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("asset")
    ap.add_argument("out")
    ap.add_argument("--brand", default="visa")
    args = ap.parse_args()

    asset = Path(args.asset)
    aid_lines, capk_lines, term_lines = read_sections(asset)

    aids = [to_aid(x) for x in aid_lines]
    parsed_caps = [to_capk(x) for x in capk_lines]

    checksums_ok = sum(1 for c in parsed_caps if c["_asset_checksum_ok"])
    mod_lens = sorted({c["_modulus_bytes"] for c in parsed_caps})

    kept, skipped = [], []
    for c in parsed_caps:
        if (c["rid"], c["key_index"]) == FORBIDDEN:
            skipped.append(f"{c['rid']}/{c['key_index']} DF05={c['expiry']!r}")
            continue
        kept.append({k: v for k, v in c.items() if not k.startswith("_")})

    data = {"aids": aids, "caps": kept, "term": term_lines[0] if term_lines else ""}

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")

    print(f"asset               : {asset}")
    print(f"asset size          : {asset.stat().st_size} bytes")
    print(f"AID lines           : {len(aid_lines)} -> written {len(aids)}")
    print(f"CAPK lines          : {len(capk_lines)} -> written {len(kept)}"
          f" (skipped {len(skipped)}: {skipped})")
    print(f"TERM lines          : {len(term_lines)}")
    print(f"asset DF03 verify   : {checksums_ok}/{len(parsed_caps)} match "
          f"SHA1(9F06||9F22||DF02||DF04)")
    print(f"modulus byte lengths: {mod_lens} (allowed {list(MODULUS_LENGTHS)})")
    print(f"aid lengths (bytes) : {sorted({len(a['aid']) // 2 for a in aids})}")
    print(f"bare aids (no field): "
          f"{[a['aid'] for a in aids if not a['fields']]}")
    print(f"term                : {data['term']}")
    print(f"wrote               : {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
