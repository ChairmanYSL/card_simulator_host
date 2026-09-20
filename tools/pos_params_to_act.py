"""Migrate legacy ``transaction.pos_params`` to protocol-v2 ``transaction.act``.

Background
----------
In v1.2 the host pushed ``pos_params`` to the POS **verbatim** as
``{tag, name, value}`` JSON with human-readable values (``"100.00"``, ``"156"``);
the POS interpreted them.  In v2 the wire carries the CTEL2 ``act`` signalData as
``{id, value}`` **hex** (docs/protocol-v2-design.md section 5), so the host must
now encode.  No such encoder ever existed, and the 31 case files still carry only
``pos_params`` -- which means the v2 ``ACT.signalData`` is an empty array.

Usage
-----
    python tools/pos_params_to_act.py --dry-run            # report only (default)
    python tools/pos_params_to_act.py --dry-run --extras keep --exponent no
    python tools/pos_params_to_act.py --apply              # rewrite the case files

Encoding rules (each one cites its source)
------------------------------------------
* ``9F02`` Amount, Authorized (Numeric) -> 6-byte BCD, i.e. the amount in minor
  units, 12 digits, zero padded.  The decimal position comes from the currency
  exponent (design section 5 R6: "金额小数点位由 5F36 货币指数决定").
* ``5F2A`` Transaction Currency Code (n3) -> 2-byte BCD (``"156"`` -> ``0156``).
* ``5F36`` Transaction Currency Exponent (1B) -> emitted only with
  ``--exponent yes`` (the tag was never part of ``pos_params``).
* ``9C`` / ``9F53`` / ``5F57`` (1B), ``9A`` (3B BCD date), ``9F21`` (3B BCD time):
  already hex -> passed through unchanged.
* ``9F1A`` Terminal Country Code and ``9F33`` Terminal Capabilities: design
  section 5 says ``9F1A`` does NOT belong in ACT (it is a terminal parameter that
  CONFIG.TERM should carry) and does not list ``9F33`` at all -- but neither tag
  is present in the cases' TERM today, so dropping them from the wire would lose
  data.  Hence ``--extras {drop,keep}`` (default ``drop`` per the design, see
  ``--help``).

The currency exponent table below is deliberately minimal: it covers only the
currencies actually used by the case library (CNY, exponent 2) and falls back to
2 with a warning, so a future currency cannot be silently mis-encoded.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: ISO-4217 minor-unit exponents for the currencies used in cases/.  Kept small
#: on purpose: an unknown currency warns instead of guessing silently.
CURRENCY_EXPONENT = {"156": 2}          # CNY
DEFAULT_EXPONENT = 2

#: tags that are already hex and pass through untouched
HEX_PASSTHROUGH = {"9C", "9F53", "5F57", "9A", "9F21"}

#: terminal parameters the design does not place in ACT (see module docstring)
EXTRA_TERMINAL_TAGS = {"9F1A", "9F33"}


def _bcd(value: str, digits: int, tag: str) -> str:
    """``value`` (decimal digits) -> packed BCD, left-padded to ``digits``."""
    value = value.strip()
    if not value.isdigit():
        raise ValueError(f"{tag}: 期望十进制数字，实际 {value!r}")
    if len(value) > digits:
        raise ValueError(f"{tag}: {value!r} 超出 {digits} 位")
    return value.rjust(digits, "0")


def amount_to_bcd(amount: str, exponent: int) -> str:
    """``"100.00"`` + exponent 2 -> ``"000000010000"`` (6-byte BCD)."""
    text = amount.strip()
    if "." in text:
        whole, frac = text.split(".", 1)
    else:
        whole, frac = text, ""
    if not whole.isdigit() or (frac and not frac.isdigit()):
        raise ValueError(f"9F02: 期望十进制金额，实际 {amount!r}")
    if len(frac) > exponent:
        # more precision than the currency can carry -> refuse rather than round
        raise ValueError(
            f"9F02: 金额 {amount!r} 的小数位多于货币指数 {exponent}")
    minor = whole + frac.ljust(exponent, "0")
    return _bcd(minor, 12, "9F02")


def currency_to_bcd(code: str) -> str:
    """``"156"`` -> ``"0156"`` (n3 packed into 2 bytes, so pad to 4 digits)."""
    return _bcd(code, 4, "5F2A")


def country_to_bcd(code: str) -> str:
    """``9F1A`` is n3 too: ``"156"`` -> ``"0156"``."""
    return _bcd(code, 4, "9F1A")


def hex_passthrough(tag: str, value: str) -> str:
    raw = value.strip().upper().replace(" ", "")
    if len(raw) % 2:
        raise ValueError(f"{tag}: 十六进制长度必须为偶数，实际 {value!r}")
    try:
        bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError(f"{tag}: 不是合法 hex（{value!r}）") from exc
    return raw


def convert(pos_params: list[dict], *, extras: str, exponent: str,
            warn) -> list[dict]:
    """One case's ``pos_params`` -> ``act`` items."""
    by_tag = {str(p.get("tag", "")).upper(): p.get("value") for p in pos_params}
    exp: int | None = None
    if "5F36" in by_tag:
        exp = int(str(by_tag["5F36"]), 10)
    elif "5F2A" in by_tag:
        code = str(by_tag["5F2A"]).strip()
        if code in CURRENCY_EXPONENT:
            exp = CURRENCY_EXPONENT[code]
        else:
            warn(f"币种 {code} 不在指数表内，按 {DEFAULT_EXPONENT} 处理")
            exp = DEFAULT_EXPONENT

    act: list[dict] = []
    for tag, value in by_tag.items():
        if tag in EXTRA_TERMINAL_TAGS:
            if extras == "keep":
                # 9F1A is n3 (decimal, like 5F2A); 9F33 is already raw hex bytes
                act.append({"id": tag, "value": country_to_bcd(str(value))
                            if tag == "9F1A" else hex_passthrough(tag, str(value))})
            continue
        if tag == "9F02":
            act.append({"id": tag, "value": amount_to_bcd(str(value), exp or 0)})
        elif tag == "5F2A":
            act.append({"id": tag, "value": currency_to_bcd(str(value))})
        elif tag in HEX_PASSTHROUGH:
            act.append({"id": tag, "value": hex_passthrough(tag, str(value))})
        else:
            warn(f"tag {tag}: 不在 ACT 编码规则内，按 hex 透传（请复核）")
            act.append({"id": tag, "value": hex_passthrough(tag, str(value))})
    if exponent == "yes" and exp is not None:
        act.append({"id": "5F36", "value": f"{exp:02X}"})
    return act


def case_paths(root: Path) -> list[Path]:
    """Case JSONs under ``<cases>/<brand>/<mode>/cases/`` (+ one group level)."""
    return sorted(root.glob("*/*/cases/*.json")) + \
        sorted(root.glob("*/*/cases/*/*.json"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", default=str(REPO_ROOT / "cases"))
    parser.add_argument("--apply", action="store_true",
                        help="rewrite the case files (default is --dry-run)")
    parser.add_argument("--extras", choices=["drop", "keep"], default="drop",
                        help="9F1A/9F33: 'drop' keeps the design's rule that they "
                             "are not ACT tags; 'keep' preserves today's wire "
                             "content by hex-passing them through ACT")
    parser.add_argument("--exponent", choices=["yes", "no"], default="yes",
                        help="emit 5F36 (currency exponent) as an ACT item")
    args = parser.parse_args(argv)

    root = Path(args.cases)
    warnings: list[str] = []

    def warn(msg: str) -> None:
        warnings.append(msg)

    migrated = skipped = failed = 0
    for path in case_paths(root):
        data = json.loads(path.read_text(encoding="utf-8"))
        txn = data.get("transaction")
        if not isinstance(txn, dict):
            continue
        params = txn.get("pos_params") or []
        if not params:
            skipped += 1
            continue
        act = None
        try:
            act = convert(params, extras=args.extras, exponent=args.exponent,
                          warn=warn)
        except ValueError as exc:
            print(f"{path.relative_to(root)}\n    ERROR {exc}")
            failed += 1
            continue
        print(f"{path.relative_to(root)}")
        print(f"    pos_params -> {json.dumps(params, ensure_ascii=False)}")
        print(f"    act        -> {json.dumps(act, ensure_ascii=False)}")
        if args.apply:
            txn["act"] = act
            txn.pop("pos_params", None)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        migrated += 1

    print(f"\n{'APPLIED' if args.apply else 'DRY-RUN'}: "
          f"{migrated} cases converted, {failed} failed, "
          f"{skipped} without pos_params, "
          f"extras={args.extras}, exponent={args.exponent}")
    for msg in sorted(set(warnings)):
        print(f"  WARN {msg}")
    if not args.apply:
        print("  (no file was written; re-run with --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
