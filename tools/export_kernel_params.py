"""Export POS kernel parameters (AID / CAPK / TERM) for the host.

Reads the POS asset file **read-only** and writes one ``<scheme>.json`` per
scheme into an out-of-repo directory that the host loads with
``serve --kernel-params-dir``.  The output contains CAPK moduli (key material),
so this tool refuses to write anywhere inside a repository working tree.

Usage::

    python tools/export_kernel_params.py \\
        --asset "E:\\Work\\ZT\\KS8226\\L2-SELFTEST-APP\\app\\src\\main\\assets\\emv\\params" \\
        --out-dir "E:\\Work\\ZT\\_secure\\card_simulator_host\\kernel_params" \\
        --scheme visa

The POS repo is never modified.  The asset format is the line-based
``AID=`` / ``CAPK=`` / ``TERM=`` text where every non-header line is
concatenated BER-TLV hex — the same shape the host puts on the wire, so an
exported entry is byte-identical to a hand-written one.

Two policies are applied by default and can be overridden deliberately:

* the 银联 ``A000000333``/``08`` entry (``DF05=PHWWIPQI``, not a calendar date)
  is **excluded** — ``--include-raw-expiry`` keeps it as
  ``expiry_encoding=raw`` with a warning from the host validator;
* ``DF05`` is re-encoded as ``ascii8`` (the format every current asset uses) —
  ``--df05 {ascii8,bcd4,bcd3}`` changes it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

#: The one asset entry that is not a calendar date: RID/index = DF05 "PHWWIPQI".
NON_DATE_ENTRY = ("A000000333", "08")

MODULUS_LENGTHS = (128, 144, 176, 248)
REQUIRED_CAPK_TAGS = ("9F06", "9F22", "DF05", "DF06", "DF07", "DF02", "DF04", "DF03")


class ExportError(RuntimeError):
    """Bad asset, bad destination, or an entry the kernel would reject."""


# -- BER-TLV walking ---------------------------------------------------------

def walk_tlv(hexstr: str) -> list[tuple[str, str]]:
    """Ordered parse of concatenated BER-TLV into ``[(tag, value_hex), ...]``.

    Deliberately an independent walker (not ``emv_host.protocol.kernel_tlv``) so
    a bug in the code under test cannot silently produce its own test data.
    """
    raw = bytes.fromhex(hexstr.strip().upper().replace(" ", ""))
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(raw):
        first = raw[pos]
        if first & 0x1F == 0x1F:
            if pos + 1 >= len(raw):
                raise ExportError("truncated two-byte tag")
            tag = raw[pos:pos + 2].hex().upper()
            pos += 2
        else:
            tag = raw[pos:pos + 1].hex().upper()
            pos += 1
        if pos >= len(raw):
            raise ExportError(f"truncated length for tag {tag}")
        first_len = raw[pos]
        pos += 1
        if first_len & 0x80:
            count = first_len & 0x7F
            if count == 0 or count > 2 or pos + count > len(raw):
                raise ExportError(f"unsupported/truncated length for tag {tag}")
            length = int.from_bytes(raw[pos:pos + count], "big")
            pos += count
        else:
            length = first_len
        if pos + length > len(raw):
            raise ExportError(f"tag {tag} declares {length} bytes but overruns")
        out.append((tag, raw[pos:pos + length].hex().upper()))
        pos += length
    return out


def read_sections(path: Path) -> tuple[list[str], list[str], str]:
    """Split the asset into (aid lines, capk lines, term hex)."""
    aids: list[str] = []
    caps: list[str] = []
    terms: list[str] = []
    section: list[str] | None = None
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if not text:
            continue
        if text == "AID=":
            section = aids
        elif text == "CAPK=":
            section = caps
        elif text == "TERM=":
            section = terms
        elif section is None:
            raise ExportError(f"{path}:{lineno}: data before any section header")
        else:
            section.append(text)
    if not aids:
        raise ExportError(f"{path}: no AID section")
    if not caps:
        raise ExportError(f"{path}: no CAPK section")
    if len(terms) > 1:
        raise ExportError(f"{path}: {len(terms)} TERM lines, expected at most 1")
    return aids, caps, terms[0] if terms else ""


# -- entry conversion --------------------------------------------------------

def to_aid(line: str) -> dict:
    """One AID entry: 9F06 first, then the remaining elements in asset order."""
    tlvs = walk_tlv(line)
    if not tlvs or tlvs[0][0] != "9F06":
        raise ExportError(f"AID entry does not start with 9F06: {line[:24]}...")
    aid_value = tlvs[0][1]
    if not 5 <= len(aid_value) // 2 <= 8:
        raise ExportError(f"AID {aid_value}: 9F06 must be 5-8 bytes")
    fields = [{"tag": tag, "value": value} for tag, value in tlvs[1:]]
    if any(f["tag"] == "9F06" for f in fields):
        raise ExportError(f"AID {aid_value}: 9F06 appears again in fields")
    return {"aid": aid_value, "fields": fields}


def to_capk(line: str, *, df05: str = "ascii8",
            allow_raw_expiry: bool = False) -> tuple[dict | None, str]:
    """One CAPK entry.

    Returns ``(entry, "")`` or ``(None, reason)`` when the entry is skipped.
    ``df05`` selects the re-encoded DF05 shape; a value that is not a calendar
    date is skipped unless ``allow_raw_expiry`` (then it is emitted as ``raw``).
    """
    tlvs = walk_tlv(line)
    values: dict[str, str] = {}
    for tag, value in tlvs:
        if tag in values:
            raise ExportError(f"duplicate tag {tag} in CAPK entry")
        values[tag] = value
    for required in REQUIRED_CAPK_TAGS:
        if required not in values:
            raise ExportError(f"CAPK entry missing {required}")

    rid, index = values["9F06"], values["9F22"]
    modulus, exponent = values["DF02"], values["DF04"]
    if len(rid) != 10:
        raise ExportError(f"CAPK {rid}: 9F06 must be 5 bytes")
    if len(index) != 2:
        raise ExportError(f"CAPK {rid}/{index}: 9F22 must be 1 byte")
    modulus_bytes = len(modulus) // 2
    if modulus_bytes not in MODULUS_LENGTHS:
        raise ExportError(f"CAPK {rid}/{index}: modulus {modulus_bytes} bytes is "
                          f"not one of {list(MODULUS_LENGTHS)}")

    # The asset's own DF03 must verify before we trust the entry at all.
    computed = hashlib.sha1(
        bytes.fromhex(rid) + bytes.fromhex(index)
        + bytes.fromhex(modulus) + bytes.fromhex(exponent)).hexdigest().upper()
    if computed != values["DF03"]:
        raise ExportError(f"CAPK {rid}/{index}: DF03 mismatch (asset says "
                          f"{values['DF03']}, computed {computed})")

    raw_df05 = values["DF05"]
    try:
        expiry = bytes.fromhex(raw_df05).decode("ascii")
        is_date = len(expiry) == 8 and expiry.isdigit()
    except (ValueError, UnicodeDecodeError):
        expiry, is_date = "", False

    entry: dict = {
        "rid": rid, "key_index": index,
        "hash_alg": int(values["DF06"], 16), "key_alg": int(values["DF07"], 16),
        "modulus": modulus, "exponent": exponent, "checksum": values["DF03"],
    }
    if is_date:
        entry["expiry"] = expiry
        entry["expiry_encoding"] = df05
    else:
        if not allow_raw_expiry:
            return None, (f"{rid}/{index} DF05 is not an 8-digit date "
                          f"(raw {raw_df05}); skipped")
        entry["expiry_encoding"] = "raw"
        entry["expiry_raw"] = raw_df05
    return entry, ""


# -- destination guard -------------------------------------------------------

def _looks_like_repo(path: Path) -> bool:
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists() or (candidate / ".gitignore").is_file():
            return True
    return False


def ensure_outside_repo(out_dir: Path, repo_root: Path) -> None:
    """Refuse to write key material into a repository working tree."""
    try:
        out_dir.resolve().relative_to(repo_root.resolve())
    except ValueError:
        pass
    else:
        raise ExportError(
            f"refusing to write kernel params inside the repository "
            f"({out_dir} is under {repo_root}); pass an out-of-repo --out-dir")
    if _looks_like_repo(out_dir.resolve()):
        raise ExportError(
            f"refusing to write kernel params into a repository working tree: "
            f"{out_dir}")


# -- main --------------------------------------------------------------------

def build(asset: Path, *, df05: str, allow_raw_expiry: bool) -> tuple[dict, list[str]]:
    aid_lines, capk_lines, term = read_sections(asset)
    aids = [to_aid(line) for line in aid_lines]
    caps: list[dict] = []
    skipped: list[str] = []
    for line in capk_lines:
        entry, reason = to_capk(line, df05=df05,
                                allow_raw_expiry=allow_raw_expiry)
        if entry is None:
            skipped.append(reason)
        else:
            caps.append(entry)
    return {"aids": aids, "caps": caps, "term": term}, skipped


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Export POS kernel params (AID/CAPK/TERM) to an out-of-repo "
                    "directory for `emv_host serve --kernel-params-dir`.")
    parser.add_argument("--asset", required=True,
                        help="POS asset file (read-only): "
                             "app/src/main/assets/emv/params")
    parser.add_argument("--out-dir", required=True,
                        help="out-of-repo destination directory")
    parser.add_argument("--scheme", default="visa",
                        help="scheme name -> <out-dir>/<scheme>.json")
    parser.add_argument("--df05", default="ascii8",
                        choices=["ascii8", "bcd4", "bcd3"],
                        help="DF05 encoding for date entries (default ascii8)")
    parser.add_argument("--include-raw-expiry", action="store_true",
                        help=f"also export the non-date entry "
                             f"{NON_DATE_ENTRY[0]}/{NON_DATE_ENTRY[1]} as "
                             f"expiry_encoding=raw (host will warn)")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing <scheme>.json")
    args = parser.parse_args(argv)

    asset = Path(args.asset)
    if not asset.is_file():
        print(f"error: asset not found: {asset}", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir)
    out_path = out_dir / f"{args.scheme}.json"

    try:
        ensure_outside_repo(out_dir, repo_root)
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if out_path.exists() and not args.force:
        print(f"error: {out_path} exists; pass --force to overwrite",
              file=sys.stderr)
        return 2

    try:
        data, skipped = build(asset, df05=args.df05,
                              allow_raw_expiry=args.include_raw_expiry)
    except (ExportError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # only now touch the filesystem: validate first, write atomically
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, out_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    print(f"asset              : {asset}")
    print(f"AID entries        : {len(data['aids'])}")
    print(f"CAPK entries       : {len(data['caps'])} (skipped {len(skipped)})")
    for reason in skipped:
        print(f"  skipped          : {reason}")
    print(f"TERM               : {data['term'] or '(none)'}")
    print(f"DF05 encoding      : {args.df05}"
          f"{' + raw for non-dates' if args.include_raw_expiry else ''}")
    print(f"wrote              : {out_path} ({out_path.stat().st_size} bytes)")
    print()
    print("start the host with:")
    print(f"  python -m emv_host serve --kernel-params-dir \"{out_dir}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
