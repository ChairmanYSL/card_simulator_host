"""Import the Amex Expresspay 4.2.0 XML set into the host's named POS config
library (out-of-repo) as **two independent module kinds**::

    TerminalConfig/CONF_*.xml      -> <out>/<brand>/CONF_XX.json
        AID + CAPK + DRL(CRL) + ExceptionFile + TerminalParameter term
    TerminalProfile/Profile_*.xml  -> <out>/<brand>/PROFILE_XX.json
        Profile tags as term

A case pairs them freely at run time: ``kernel_params_ref`` selects a CONF
module, ``terminal_profile_ref`` selects a Profile module; the loader composes
``term = conf.term + profile.term``.  There is no same-number binding.

The output contains CAPK moduli, so -- like export_kernel_params.py -- this tool
refuses to write inside a repository working tree.

Usage::

    python tools/import_expresspay_xml.py ^
        --config-dir "refernce/Expresspay 4.2.0/TerminalConfig" ^
        --profile-dir "refernce/Expresspay 4.2.0/TerminalProfile" ^
        --out-dir "E:\\Work\\ZT\\_secure\\card_simulator_host\\kernel_params" ^
        --brand amex

Then start the host with::

    python -m emv_host serve --kernel-params-dir "<out-dir>"

Reference data is authoritative for the field set/order; the host model
(backend/emv_host/cases/models.py) is authoritative for the JSON shape.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_VERSION = "4.2.0"


class ImportError_(RuntimeError):
    """Bad source XML or an unsafe destination."""


# -- TLV / value encoding -----------------------------------------------------

def encode_value(fmt: str, text: str) -> str:
    """Encode one Cfg value following the XML Format attribute.

    * ``b``            -> value is already hex, keep verbatim;
    * ``n``            -> packed BCD (a digit string is its own hex; left-pad an
                          odd digit count with one zero nibble);
    * ``an`` / ``ans`` -> ASCII bytes to hex.
    """
    kind = (fmt or "").strip().lower()
    value = (text or "").strip()
    if kind.startswith("b"):
        hex_value = value.upper()
        if len(hex_value) % 2:
            raise ImportError_(
                f"Format {fmt!r} value {value!r} is odd-length hex")
        return hex_value
    if kind.startswith("n"):
        digits = value.upper()
        return ("0" + digits) if len(digits) % 2 else digits
    if kind.startswith("a"):
        return value.encode("ascii").hex().upper()
    return value.upper()


def ber_length(length: int) -> bytes:
    if length < 0:
        raise ImportError_(f"negative TLV length {length}")
    if length <= 0x7F:
        return bytes([length])
    if length <= 0xFF:
        return bytes([0x81, length])
    if length <= 0xFFFF:
        return bytes([0x82, (length >> 8) & 0xFF, length & 0xFF])
    raise ImportError_(f"TLV length {length} too large")


def tlv(tag: str, value_hex: str) -> str:
    tag = tag.strip().upper()
    value = value_hex.strip().upper()
    raw = bytes.fromhex(value)
    return tag + ber_length(len(raw)).hex().upper() + value


def decode_df05(hex_value: str) -> tuple[str, str, str]:
    """Infer ``(encoding, expiry, expiry_raw)`` from a DF05 hex value."""
    value = (hex_value or "").strip().upper()
    raw = bytes.fromhex(value)
    is_bcd = all((b >> 4) <= 9 and (b & 0x0F) <= 9 for b in raw)
    if len(raw) == 8:
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            return "raw", "", value
        return ("ascii8", text, "") if text.isdigit() else ("raw", "", value)
    if len(raw) == 4 and is_bcd:
        return "bcd4", raw.hex().upper(), ""
    if len(raw) == 3 and is_bcd:
        return "bcd3", "20" + raw.hex().upper(), ""
    return "raw", "", value


def cfg_items(node: ET.Element) -> list[tuple[str, str, str]]:
    """Flatten one XML element's ``Cfg`` children into (tag, format, text)."""
    out: list[tuple[str, str, str]] = []
    for cfg in node.findall("Cfg"):
        tag = (cfg.get("label") or "").strip().upper()
        out.append((tag, cfg.get("Format") or "",
                    (cfg.text or "").strip()))
    return out


def cfg_map(node: ET.Element) -> dict[str, str]:
    return {tag: text for tag, _fmt, text in cfg_items(node)}


# -- element builders ---------------------------------------------------------

def build_aid(node: ET.Element) -> dict:
    items = cfg_items(node)
    if not items or items[0][0] != "9F06":
        raise ImportError_("KernelAidCombination does not start with 9F06")
    aid = encode_value(items[0][1], items[0][2])
    fields = []
    for tag, fmt, text in items[1:]:
        fields.append({"tag": tag, "value": encode_value(fmt, text)})
    return {"aid": aid, "fields": fields}


def build_capk(node: ET.Element) -> dict:
    values = cfg_map(node)
    for required in ("9F06", "9F22", "DF05", "DF06", "DF07",
                     "DF02", "DF04", "DF03"):
        if required not in values:
            raise ImportError_(f"CAPK entry missing {required}")
    encoding, expiry, raw = decode_df05(values["DF05"])
    entry: dict = {
        "rid": values["9F06"].upper(),
        "key_index": values["9F22"].upper(),
        "hash_alg": int(values["DF06"], 16),
        "key_alg": int(values["DF07"], 16),
        "modulus": values["DF02"].upper(),
        "exponent": values["DF04"].upper(),
        "checksum": values["DF03"].upper(),
    }
    if encoding == "raw":
        entry["expiry_encoding"] = "raw"
        entry["expiry_raw"] = raw
    else:
        entry["expiry"] = expiry
        entry["expiry_encoding"] = encoding
    return entry


def build_drl(node: ET.Element) -> dict:
    values = cfg_map(node)
    return {"rid": values["9F06"].upper(),
            "index": values["8F"].upper(),
            "serial": values["DF8105"].upper()}


def build_exception_file(node: ET.Element) -> dict:
    values = cfg_map(node)
    return {"pan": values["5A"].upper(),
            "pan_sequence": values["5F34"].upper()}


def term_from_nodes(nodes: list[ET.Element]) -> str:
    parts: list[str] = []
    for node in nodes:
        for tag, fmt, text in cfg_items(node):
            parts.append(tlv(tag, encode_value(fmt, text)))
    return "".join(parts)


# -- source scanning ----------------------------------------------------------

def read_xml(path: Path) -> ET.Element:
    try:
        return ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ImportError_(f"{path}: cannot parse XML: {exc}") from exc


def profile_number(stem: str) -> str | None:
    match = re.match(r"^Profile_(\d+)", stem)
    return match.group(1) if match else None


def build_conf(conf_path: Path, version: str) -> dict:
    """One CONF module: AID/CAPK/DRL/ExceptionFile + TerminalParameter term.

    The Profile part is deliberately NOT merged here: CONF and Profile are two
    independent modules and the case pairs them freely via
    ``kernel_params_ref`` + ``terminal_profile_ref``.
    """
    root = read_xml(conf_path)
    return {
        "aids": [build_aid(n) for n in root.findall("KernelAidCombination")],
        "caps": [build_capk(n) for n in root.findall("CAPK")],
        "drl": [build_drl(n) for n in root.findall("CRL")],
        "exception_file": [build_exception_file(n)
                           for n in root.findall("ExceptionFile")],
        "term": term_from_nodes(root.findall("TerminalParameter")),
        "version": version,
    }


def build_profile(profile_path: Path, version: str) -> dict:
    """One Profile module: the ``Profile`` element's tags as term only."""
    node = read_xml(profile_path).find("Profile")
    if node is None:
        raise ImportError_(f"{profile_path}: no <Profile> element")
    return {"aids": [], "caps": [], "drl": [], "exception_file": [],
            "term": term_from_nodes([node]), "version": version}


# -- destination guard / output ----------------------------------------------

def _looks_like_repo(path: Path) -> bool:
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists() or (candidate / ".gitignore").is_file():
            return True
    return False


def ensure_outside_repo(out_dir: Path, repo_root: Path) -> None:
    try:
        out_dir.resolve().relative_to(repo_root.resolve())
    except ValueError:
        pass
    else:
        raise ImportError_(
            f"refusing to write kernel params inside the repository ({out_dir} "
            f"is under {repo_root}); pass an out-of-repo --out-dir")
    if _looks_like_repo(out_dir.resolve()):
        raise ImportError_(
            f"refusing to write kernel params into a repository working tree: "
            f"{out_dir}")


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Import the Expresspay XML set as modular named POS configs "
                    "(CONF_* + PROFILE_*; out-of-repo; contains CAPK moduli).")
    parser.add_argument("--config-dir", required=True,
                        help="TerminalConfig dir (CONF_*.xml)")
    parser.add_argument("--profile-dir", default="",
                        help="TerminalProfile dir (Profile_*.xml); optional")
    parser.add_argument("--out-dir", required=True,
                        help="out-of-repo root; files go to <out>/<brand>/")
    parser.add_argument("--brand", default="amex")
    parser.add_argument("--version", default=DEFAULT_VERSION,
                        help=f"config revision stamped on every file "
                             f"(default {DEFAULT_VERSION})")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing non-empty <out>/<brand>/")
    args = parser.parse_args(argv)

    config_dir = Path(args.config_dir)
    profile_dir = Path(args.profile_dir) if args.profile_dir else None
    out_root = Path(args.out_dir)
    out_dir = out_root / args.brand
    if not config_dir.is_dir():
        print(f"error: config dir not found: {config_dir}", file=sys.stderr)
        return 2
    try:
        ensure_outside_repo(out_root, repo_root)
    except ImportError_ as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if out_dir.exists() and any(out_dir.iterdir()) and not args.force:
        print(f"error: {out_dir} is not empty; pass --force to overwrite",
              file=sys.stderr)
        return 2

    try:
        conf_paths = sorted(config_dir.glob("CONF_*.xml"))
        if not conf_paths:
            raise ImportError_(f"no CONF_*.xml under {config_dir}")
        profile_paths = (sorted(profile_dir.glob("Profile_*.xml"))
                         if profile_dir else [])
        out_dir.mkdir(parents=True, exist_ok=True)
        index_entries: list[dict] = []
        errors: list[str] = []
        for conf_path in conf_paths:
            try:
                config = build_conf(conf_path, args.version)
            except ImportError_ as exc:
                errors.append(f"{conf_path.name}: {exc}")
                continue
            ref = conf_path.stem
            write_json(out_dir / f"{ref}.json", config)
            index_entries.append({
                "id": ref, "kind": "conf", "file": f"{ref}.json",
                "source": conf_path.name,
                "aids": len(config["aids"]), "capk": len(config["caps"]),
                "drl": len(config["drl"]),
                "exception_file": len(config["exception_file"]),
                "term": bool(config["term"]),
            })
        for profile_path in profile_paths:
            try:
                config = build_profile(profile_path, args.version)
            except ImportError_ as exc:
                errors.append(f"{profile_path.name}: {exc}")
                continue
            num = profile_number(profile_path.stem)
            ref = f"PROFILE_{num}" if num else profile_path.stem
            write_json(out_dir / f"{ref}.json", config)
            index_entries.append({
                "id": ref, "kind": "profile", "file": f"{ref}.json",
                "source": profile_path.name, "aids": 0, "capk": 0,
                "drl": 0, "exception_file": 0, "term": bool(config["term"]),
            })
        write_json(out_dir / "index.json", {
            "brand": args.brand, "version": args.version,
            "source_config_dir": str(config_dir),
            "source_profile_dir": str(profile_dir) if profile_dir else "",
            "configs": index_entries,
        })
    except (ImportError_, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {len(index_entries)} modules to {out_dir}")
    print(f"reference: {config_dir}")
    if profile_dir:
        print(f"reference: {profile_dir}")
    if errors:
        print(f"\n{len(errors)} source file(s) skipped:", file=sys.stderr)
        for line in errors:
            print(f"  {line}", file=sys.stderr)
    print("start the host with:")
    print(f'  python -m emv_host serve --kernel-params-dir "{out_root}"')
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
