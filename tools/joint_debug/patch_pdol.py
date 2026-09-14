"""Add an explicit standard PDOL (9F38 template) to case profiles that lack one.

Why: the KS8226 VISAEMVCORE kernel rejects a PDOL without the standard tags
(minimal `9F3704` -> ERR(-1202)).  The card simulator defaults per network, but
until every network's default is correct we can make the case data explicit.
UnionPay profiles already get a 24B default; this script is idempotent.

    python tools/joint_debug/patch_pdol.py --dry-run      # list changes
    python tools/joint_debug/patch_pdol.py --apply        # write files
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STANDARD_PDOL = "9F66049F02069F03069F1A0295055F2A029A039C019F3704"
SKIP_PREFIXES = ("VISA-PDOL-PROBE", "VISA-IAD-PROBE")


def case_files() -> list[Path]:
    paths = []
    for pattern in ("cases/*/contactless/cases/*.json",
                    "cases/*/contactless/cases/*/*.json"):
        paths += [Path(p) for p in glob.glob(str(REPO / pattern))]
    return sorted(p for p in paths if p.name != "group.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-probes", action="store_true")
    args = parser.parse_args()
    if not args.apply and not args.dry_run:
        args.dry_run = True

    changed = 0
    for path in case_files():
        data = json.loads(path.read_text(encoding="utf-8"))
        case_id = data.get("case_id", path.stem)
        if not args.include_probes and case_id.startswith(SKIP_PREFIXES):
            continue
        profile = data.get("profile")
        if not isinstance(profile, dict):
            continue
        if (profile.get("pdol") or "").strip():
            continue
        profile["pdol"] = STANDARD_PDOL
        changed += 1
        print(f"{'WRITE' if args.apply else 'WOULD'} {case_id}: pdol={STANDARD_PDOL}")
        if args.apply:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    print(f"\n{changed} case profile(s) {'updated' if args.apply else 'need update'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
