"""Build temporary cases roots for the v1.2 round (never mutates cases/).

Used by:
* L3 case G  -> a copy of VISA-CL-001 with timeout_seconds=15 (plan section 4.9)
* T-D0 6.5 reverse -> cases root WITHOUT cases/visa/kernel_params.json
* T-D0 6.5 failure -> cases root with an odd-length `term`
* L4 5.5 -> a deliberately broken library root
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

REPO = Path(r"E:\Work\ZT\card_simulator_host")
BASE = REPO / "cases"
ROOT = REPO / "tmp" / "v12test" / "cases"


def fresh(name: str) -> Path:
    """Copy the pristine cases/ tree to tmp/v12test/cases/<name>."""
    dst = ROOT / name
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BASE, dst)
    return dst


def drop_kernel_params(root: Path, scheme: str = "visa") -> bool:
    p = root / scheme / "kernel_params.json"
    if p.is_file():
        p.unlink()
        return True
    return False


def patch_kernel_params(root: Path, scheme: str = "visa", **changes) -> dict:
    p = root / scheme / "kernel_params.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data.update(changes)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return data


VISA_001 = Path("visa/contactless/cases/VISA-CL-001.json")


def patch_case(root: Path, rel: Path, **changes) -> dict:
    p = root / rel
    data = json.loads(p.read_text(encoding="utf-8"))
    data.update(changes)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return data


if __name__ == "__main__":
    import sys
    what = sys.argv[1] if len(sys.argv) > 1 else "list"
    if what == "timeout15":
        r = fresh("timeout15")
        d = patch_case(r, VISA_001, timeout_seconds=15)
        print(f"cases root: {r}")
        print(f"VISA-CL-001 timeout_seconds -> {d['timeout_seconds']}")
    elif what == "nokp":
        r = fresh("nokp")
        print(f"cases root: {r}  dropped kernel_params="
              f"{drop_kernel_params(r)}")
    elif what == "badterm":
        r = fresh("badterm")
        d = patch_kernel_params(r, term="9F660436000080")
        print(f"cases root: {r}  term -> {d['term']!r} "
              f"(len={len(d['term'])}, odd={len(d['term']) % 2 == 1})")
    else:
        print("usage: cases_factory.py timeout15|nokp|badterm")
