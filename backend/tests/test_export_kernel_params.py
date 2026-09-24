"""Tests for tools/export_kernel_params.py.

The tool turns a POS asset file into the host's kernel-params JSON.  These tests
use a **synthetic** asset (small modulus, invented values) so no real CAPK
material ever enters the repository, and they assert the two safety properties
that matter most: the tool refuses to write inside the repository, and it
refuses to trust an entry whose DF03 does not verify.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "export_kernel_params.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("export_kernel_params", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["export_kernel_params"] = module
    spec.loader.exec_module(module)
    return module


tool = load_tool()

#: One 128-byte modulus is required by the validator; this one is synthetic.
MODULUS = "AB" * 128
RID, INDEX, EXPONENT, EXPIRY = "A000000003", "01", "03", "20441231"


def tlv(tag: str, value_hex: str) -> str:
    """One TLV with BER length (short form, or 81 xx for 128-255 bytes)."""
    length = len(value_hex) // 2
    ber = f"{length:02X}" if length <= 0x7F else f"81{length:02X}"
    return tag + ber + value_hex


def capk_line(*, df05_hex: str | None = None, checksum: str | None = None,
              rid: str = RID, index: str = INDEX) -> str:
    df05 = df05_hex or EXPIRY.encode("ascii").hex().upper()
    digest = checksum or hashlib.sha1(
        bytes.fromhex(rid + index + MODULUS + EXPONENT)).hexdigest().upper()
    return "".join((
        tlv("9F06", rid), tlv("9F22", index), tlv("DF05", df05),
        tlv("DF06", "01"), tlv("DF07", "01"), tlv("DF02", MODULUS),
        tlv("DF04", EXPONENT), tlv("DF03", digest),
    ))


AID_LINE = tlv("9F06", "A0000000031010") + tlv("DF01", "01") + tlv("9F08", "0002")
TERM = "9F6604360000809F1A0201569F7A0101"


def write_asset(tmp_path: Path, *, aids=(), caps=(), term=TERM) -> Path:
    path = tmp_path / "params"
    lines = ["AID=", *aids, "CAPK=", *caps, "TERM=", term]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_export_produces_host_loadable_json(tmp_path):
    asset = write_asset(tmp_path, aids=[AID_LINE], caps=[capk_line()])
    out_dir = tmp_path / "secure" / "kp"

    rc = tool.main(["--asset", str(asset), "--out-dir", str(out_dir),
                    "--brand", "visa"])
    assert rc == 0

    data = json.loads((out_dir / "visa.json").read_text(encoding="utf-8"))
    assert len(data["aids"]) == 1 and len(data["caps"]) == 1
    assert data["term"] == TERM
    assert data["aids"][0]["aid"] == "A0000000031010"
    assert data["aids"][0]["fields"] == [{"tag": "DF01", "value": "01"},
                                         {"tag": "9F08", "value": "0002"}]
    entry = data["caps"][0]
    assert entry["rid"] == RID and entry["key_index"] == INDEX
    assert entry["expiry"] == EXPIRY and entry["expiry_encoding"] == "ascii8"
    assert entry["modulus"] == MODULUS

    # and the host accepts it unchanged
    from emv_host.cases.models import KernelParams
    from emv_host.cases.validation import validate_kernel_params
    report = validate_kernel_params(KernelParams.model_validate(data), "visa")
    assert not report.errors, [i.message for i in report.errors]


def test_export_refuses_a_destination_inside_the_repository(tmp_path):
    asset = write_asset(tmp_path, aids=[AID_LINE], caps=[capk_line()])
    rc = tool.main(["--asset", str(asset),
                    "--out-dir", str(REPO / "cases" / "visa")])
    assert rc == 2
    assert not (REPO / "cases" / "visa" / "visa.json").exists()


def test_export_refuses_to_overwrite_without_force(tmp_path):
    asset = write_asset(tmp_path, aids=[AID_LINE], caps=[capk_line()])
    out_dir = tmp_path / "kp"
    assert tool.main(["--asset", str(asset), "--out-dir", str(out_dir)]) == 0
    assert tool.main(["--asset", str(asset), "--out-dir", str(out_dir)]) == 2
    assert tool.main(["--asset", str(asset), "--out-dir", str(out_dir),
                      "--force"]) == 0


def test_export_rejects_wrong_df03(tmp_path):
    asset = write_asset(tmp_path, aids=[AID_LINE],
                        caps=[capk_line(checksum="00" * 20)])
    rc = tool.main(["--asset", str(asset), "--out-dir", str(tmp_path / "kp")])
    assert rc == 1, "an entry whose DF03 does not verify must not be exported"


def test_export_rejects_unsupported_modulus_length(tmp_path):
    short = "AB" * 64  # 64 bytes is not 128/144/176/248
    line = capk_line().replace(tlv("DF02", MODULUS), tlv("DF02", short))
    digest = hashlib.sha1(bytes.fromhex(RID + INDEX + short + EXPONENT)).hexdigest().upper()
    line = line[:-len(tlv("DF03", "00" * 20))] + tlv("DF03", digest)
    asset = write_asset(tmp_path, aids=[AID_LINE], caps=[line])
    assert tool.main(["--asset", str(asset), "--out-dir", str(tmp_path / "kp")]) == 1


def test_non_date_df05_is_skipped_by_default_and_kept_as_raw(tmp_path):
    """The real asset's 银联 entry: excluded unless explicitly requested."""
    phwwipqi = "PHWWIPQI".encode("ascii").hex().upper()
    asset = write_asset(
        tmp_path, aids=[AID_LINE],
        caps=[capk_line(),
              # same shape as the real anomaly, but a different key index so the
              # validator's RID+index duplicate rule does not also fire
              capk_line(df05_hex=phwwipqi, rid="A000000333", index="08")])
    out_dir = tmp_path / "kp"

    assert tool.main(["--asset", str(asset), "--out-dir", str(out_dir),
                      "--brand", "default"]) == 0
    default = json.loads((out_dir / "default.json").read_text(encoding="utf-8"))
    assert len(default["caps"]) == 1, "the non-date entry is excluded by default"

    assert tool.main(["--asset", str(asset), "--out-dir", str(out_dir),
                      "--brand", "raw", "--include-raw-expiry"]) == 0
    kept = json.loads((out_dir / "raw.json").read_text(encoding="utf-8"))
    assert len(kept["caps"]) == 2
    entry = [c for c in kept["caps"] if c.get("expiry_encoding") == "raw"][0]
    assert entry["expiry_raw"] == phwwipqi
    assert "expiry" not in entry

    # the host loads the raw form but never silently: warning, no error
    from emv_host.cases.models import KernelParams
    from emv_host.cases.validation import validate_kernel_params
    report = validate_kernel_params(KernelParams.model_validate(kept), "visa")
    assert not report.errors, [i.message for i in report.errors]
    assert any("raw" in i.message for i in report.warnings)


def test_df05_encoding_option_is_honoured(tmp_path):
    asset = write_asset(tmp_path, aids=[AID_LINE], caps=[capk_line()])
    out_dir = tmp_path / "kp"
    for brand, expected in (("a8", "ascii8"), ("b4", "bcd4"), ("b3", "bcd3")):
        opt = {"a8": "ascii8", "b4": "bcd4", "b3": "bcd3"}[brand]
        assert tool.main(["--asset", str(asset), "--out-dir", str(out_dir),
                          "--brand", brand, "--df05", opt, "--force"]) == 0
        data = json.loads((out_dir / f"{brand}.json").read_text(encoding="utf-8"))
        assert data["caps"][0]["expiry_encoding"] == expected

    # The importer records the asset's own encoding, but the wire form is always
    # the one the KS8226 kernel accepts: 8 ASCII bytes (DF05_KERNEL_ENCODING).
    # The 2026-09-24 joint run showed every CAPK add carrying a bcd3 DF05 being
    # rejected with -1, while the factory asset's ascii8 entries load fine.
    from emv_host.cases.models import KernelParams
    from emv_host.protocol.kernel_tlv import decode_tlv_map, encode_capk
    from emv_host.cases.models import CapkParam
    for brand in ("a8", "b4", "b3"):
        data = json.loads((out_dir / f"{brand}.json").read_text(encoding="utf-8"))
        entry = CapkParam.model_validate(data["caps"][0])
        assert decode_tlv_map(encode_capk(entry))["DF05"] == "3230343431323331", brand

    # A raw sample (not a calendar date) still goes out verbatim.
    raw = CapkParam(rid="A000000333", key_index="01", expiry="", expiry_encoding="raw",
                    expiry_raw="50485757495051", hash_alg=1, key_alg=1,
                    modulus="BB" * 64, exponent="03", checksum="")
    assert decode_tlv_map(encode_capk(raw))["DF05"] == "50485757495051"

    # DF07: the vendor vectors' 0 is written as 01 (RSA).
    assert decode_tlv_map(encode_capk(CapkParam(
        rid="A000000333", key_index="01", expiry="20441231", expiry_encoding="bcd3",
        hash_alg=1, key_alg=0, modulus="BB" * 64, exponent="03",
        checksum="")))["DF07"] == "01"


def test_missing_asset_is_reported(tmp_path):
    assert tool.main(["--asset", str(tmp_path / "nope"),
                      "--out-dir", str(tmp_path / "kp")]) == 2


def test_asset_without_aid_section_is_rejected(tmp_path):
    path = tmp_path / "params"
    path.write_text("CAPK=\n" + capk_line() + "\n", encoding="utf-8")
    assert tool.main(["--asset", str(path), "--out-dir", str(tmp_path / "kp")]) == 1


# -- gen_cases manifest guard (T7) -------------------------------------------

def load_gen_cases():
    spec = importlib.util.spec_from_file_location(
        "gen_cases", REPO / "tools" / "gen_cases.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_cases"] = module
    spec.loader.exec_module(module)
    return module


def test_gen_cases_ignores_manifest_entries_outside_the_brand_dir(tmp_path):
    """A corrupted manifest must not let a regeneration delete outside files."""
    gen = load_gen_cases()

    outside = tmp_path / "outside.json"
    outside.write_text("keep me", encoding="utf-8")
    brand_root = tmp_path / "cases" / "visa"
    brand_root.mkdir(parents=True)
    victim = brand_root / "generated.json"
    victim.write_text("{}", encoding="utf-8")
    (brand_root / ".gen_cases_manifest.json").write_text(json.dumps({
        "files": ["generated.json", "../../outside.json"]}), encoding="utf-8")

    gen._clean_previous_generated(brand_root)

    assert not victim.exists(), "the in-tree generated file is still cleaned"
    assert outside.exists(), "an escaping entry must be skipped, not deleted"


def test_gen_cases_clean_is_bounded_to_the_manifest(tmp_path):
    gen = load_gen_cases()
    brand_root = tmp_path / "cases" / "visa"
    cases = brand_root / "contactless" / "cases"
    cases.mkdir(parents=True)
    (cases / "OPBD-001-01.json").write_text("{}", encoding="utf-8")  # hand-made
    (cases / "VISA-CL-001.json").write_text("{}", encoding="utf-8")  # generated
    (brand_root / ".gen_cases_manifest.json").write_text(json.dumps({
        "files": ["contactless/cases/VISA-CL-001.json"]}), encoding="utf-8")

    gen._clean_previous_generated(brand_root)

    assert not (cases / "VISA-CL-001.json").exists()
    assert (cases / "OPBD-001-01.json").exists(), "hand-written cases are kept"
