"""The Mastercard CDA case ``3C05-9100-06`` (CTEL2/KSAPI log 3C05-9100-06).

The case's card is a CDA card, so it can only be approved offline if the ICC
private key in ``keys.rsa_keys`` pairs with the 9F46 certificate that the card
emits, and if that certificate chains up to the CA public key the host pushes in
``PPS_perf_MC`` (index 01).  These tests replay that chain with public
operations only -- no private key material is needed -- and pin the card data
against the reference log.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization

from emv_host.cases import load_library
from emv_host.cases.models import MatchRule
from emv_host.judge.asserter import assert_result
from emv_host.judge.outcome import build_result

REPO = Path(__file__).resolve().parents[2]
BRAND = REPO / "cases" / "mastercard"
CONF_PATH = BRAND / "pos_configs" / "PPS_perf_MC.json"
CASE_PATH = BRAND / "contactless" / "cases" / "3C05-9100-06.json"
TOOL_PATH = REPO / "tools" / "make_mc_cda_case.py"

RID = "A000000004"
CA_KEY_INDEX = "01"
CASE_ID = "3C05-9100-06"

#: The reference log's SFI 2/1 record with the ``70`` template stripped.
LOG_SFI2_1 = ("57125413339000001513D4912201000000000000"
              "5A0854133390000015135F24034912315F25030601015F280202505F340101"
              "8C279F02069F03069F1A0295055F2A029A039C019F37049F35019F45029F4C08"
              "9F34039F21039F7C14"
              "8D12910A8A0295059F37049F4C089F02069F0306"
              "8E0A00000000000000001F03"
              "9F0702FF009F080200029F0D05F8406420009F0E0500108800009F0F05F86064F800"
              "9F42020978"
              "9F51039F37049F4A0182")

OPS_APPROVED = "10F0F000B0F0FF00"          # log OPS: APPROVED, CVM NO_CVM
DATA_RECORD = ("9F0206000000000001" "9F0306000000000000" "9F2608B7BF643B823DADAE"
               "5F2403491231" "82021980" "500A4D617374657243617264"
               "5A085413339000001513" "5F340101" "9F36027B39" "9F0702FF00"
               "9F08020002" "9F270140" "9F34031F0302" "8407A0000000041010"
               "9F4C080000000100000001")
DISC_DATA = "9F42020978DF8115060000000000FF"


@pytest.fixture(scope="module")
def conf() -> dict:
    return json.loads(CONF_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def case() -> dict:
    return json.loads(CASE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ca_key(conf: dict) -> dict:
    keys = [c for c in conf["caps"]
            if str(c["key_index"]).upper() == CA_KEY_INDEX
            and c["rid"].upper() == RID]
    assert len(keys) == 1, "PPS_perf_MC must carry exactly one index-01 CA key"
    return keys[0]


def _recover(modulus_hex: str, exponent_hex: str, signature_hex: str) -> bytes:
    modulus = int(modulus_hex, 16)
    size = (modulus.bit_length() + 7) // 8
    assert len(signature_hex) // 2 == size, "signature length != CA modulus length"
    return pow(int(signature_hex, 16), int(exponent_hex, 16),
               modulus).to_bytes(size, "big")


def _spki_rsa(b64: str) -> tuple[int, int]:
    key = serialization.load_der_public_key(base64.b64decode(b64))
    numbers = key.public_numbers()
    return numbers.n, numbers.e


# ---------------------------------------------------------------------------
# the case file itself
# ---------------------------------------------------------------------------

def test_case_loads_and_matches_the_log_shape(case: dict):
    assert case["case_id"] == CASE_ID
    assert case["brand"] == "mastercard"
    assert case["entry_mode"] == "contactless"
    assert case["kernel_params_ref"] == "PPS_perf_MC"

    profile = case["profile"]
    assert profile["aip"] == "1980", "log GPO: 82=1980 (CDA + CVM)"
    assert profile["afl"] == "08010100100101011801010020010200", "log 94"
    assert profile["pdol"] == "9F4005", "log FCI 9F38 (requests 9F40)"
    assert profile["pan"] == "5413339000001513" and profile["panSequenceNumber"] == 1
    assert profile["applicationExpirationDate"] == "491231"
    assert profile["applicationCurrencyCode"] == "0978"
    assert profile["acBehavior"] == "offline_approved", "log answered TC (CID=40)"
    assert [a["aid"] for a in profile["aids"]] == ["A0000000041010"]
    assert profile["caPublicKeyIndex"] == CA_KEY_INDEX

    records = {(r["sfi"], r["record"]): r["fields"] for r in profile["records"]}
    assert set(records) == {(2, 1), (3, 1), (4, 1), (4, 2)}, \
        "log reads SFI 2/1 (app record), 3/1 (issuer PK), 4/1 + 4/2 (ICC PK)"
    assert records[(4, 2)] == ["iccPublicKeyCertificate"]
    assert "caPublicKeyIndex" in records[(3, 1)]


def test_case_library_accepts_the_case():
    library = load_library(REPO / "cases")
    errors = [i for i in library.issues if i.severity == "error"]
    assert not errors, [i.message for i in errors]
    loaded = library.get_case("mastercard", "contactless", CASE_ID)
    assert loaded is not None
    assert loaded.kernel_params_ref == "PPS_perf_MC"
    assert loaded.kernel_params is not None
    assert len(loaded.kernel_params.caps) == 13, "12 Appendix B keys + our CA key"


def test_act_carries_the_logs_transaction_data(case: dict):
    act = {item["id"]: item["value"] for item in case["transaction"]["act"]}
    assert act["9F02"] == "000000000001", "log CDOL1 data: amount 0.01"
    assert act["9F03"] == "000000000000"
    assert act["5F2A"] == "0978"
    assert act["5F36"] == "02"
    assert act["9C"] == "00"


# ---------------------------------------------------------------------------
# the config patch (locally generated CA key)
# ---------------------------------------------------------------------------

def test_config_carries_the_generated_ca_key(conf: dict, ca_key: dict):
    assert len(conf["caps"]) == 13
    assert conf["version"] == "ctel2-4.1"
    assert ca_key["hash_alg"] == 1 and ca_key["key_alg"] == 1
    assert ca_key["exponent"] == "010001"
    assert ca_key["expiry"] == "20491231" and ca_key["expiry_encoding"] == "ascii8"
    blob = bytes.fromhex(RID + CA_KEY_INDEX + ca_key["modulus"]
                         + ca_key["exponent"])
    assert ca_key["checksum"] == hashlib.sha1(blob).hexdigest().upper()


def test_index_notes_the_generated_key():
    index = json.loads((BRAND / "pos_configs" / "index.json")
                       .read_text(encoding="utf-8"))
    item = [c for c in index["configs"] if c["id"] == "PPS_perf_MC"][0]
    assert item["capk"] == 13
    assert CA_KEY_INDEX in item["note"] and CASE_ID in item["note"]


# ---------------------------------------------------------------------------
# log fidelity of the card data
# ---------------------------------------------------------------------------

def _tool():
    """Load the generator module (it is a script, not a package member)."""
    spec = importlib.util.spec_from_file_location("make_mc_cda_case", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_static_record_reproduces_the_log():
    tool = _tool()
    built = tool.build_record_value()
    with_unsupported = tool.build_record_value(include_unsupported=True)
    assert with_unsupported == LOG_SFI2_1, "profile layout must mirror the log"
    # 9F51 (DRDOL) is the only tag the profile cannot express
    assert with_unsupported.replace(tool.tlv("9F51", "9F3704"), "") == built
    assert tool.pack_track2_ascii(tool.TRACK2_ASCII) == \
        "5413339000001513D4912201000000000000", "log tag 57"


# ---------------------------------------------------------------------------
# the certificate chain: CA(config) -> issuer(90) -> ICC(9F46) == keys.rsa_keys
# ---------------------------------------------------------------------------

def test_certificate_chain_pairs_with_the_pushed_icc_private_key(ca_key, case):
    profile = case["profile"]
    static_hex = _tool().static_data_hex()

    # 1. terminal: recover the issuer public key from tag 90 with the CAPK
    issuer_cert = _recover(ca_key["modulus"], ca_key["exponent"],
                           profile["issuerPublicKeyCertificate"])
    assert issuer_cert[0] == 0x6A and issuer_cert[1] == 0x02
    assert issuer_cert[-1] == 0xBC
    capacity = len(issuer_cert) - 36
    issuer_modulus = (issuer_cert[15:15 + capacity]
                      + bytes.fromhex(profile["issuerPublicKeyRemainder"]))
    assert len(issuer_modulus) == int.from_bytes(issuer_cert[13:14], "big")
    assert hashlib.sha1(issuer_cert[1:len(issuer_cert) - 21]
                        + bytes.fromhex(profile["issuerPublicKeyRemainder"])
                        + bytes.fromhex(profile["issuerPublicKeyExponent"])
                        ).digest() == issuer_cert[-21:-1]

    # 2. terminal: recover the ICC public key from tag 9F46
    icc_cert = _recover(issuer_modulus.hex().upper(),
                        profile["issuerPublicKeyExponent"],
                        profile["iccPublicKeyCertificate"])
    assert icc_cert[0] == 0x6A and icc_cert[1] == 0x04 and icc_cert[-1] == 0xBC
    capacity46 = len(icc_cert) - 42
    icc_modulus = (icc_cert[21:21 + capacity46]
                   + bytes.fromhex(profile["iccPublicKeyRemainder"]))
    assert hashlib.sha1(icc_cert[1:len(icc_cert) - 21]
                        + bytes.fromhex(profile["iccPublicKeyRemainder"])
                        + bytes.fromhex(profile["iccPublicKeyExponent"])
                        + bytes.fromhex(static_hex)
                        + bytes.fromhex(profile["aip"])).digest() == icc_cert[-21:-1]

    # 3. that recovered key must be the one the card signs with
    for rsa in case["keys"]["rsa_keys"]:
        modulus, exponent = _spki_rsa(rsa["public_key"])
        if rsa["id"] == profile["iccPrivateKeyId"]:
            assert modulus == int.from_bytes(icc_modulus, "big")
            assert exponent == int(profile["iccPublicKeyExponent"], 16)
            break
    else:  # pragma: no cover - guarded by the assert below
        pytest.fail(f"no key {profile['iccPrivateKeyId']} in keys.rsa_keys")


# ---------------------------------------------------------------------------
# the pass criteria
# ---------------------------------------------------------------------------

def _out(ops: str, datarecord: str = "", discdata: str = "",
         gac: str = "9F2701409F36027B399F4B8180AA") -> dict:
    signal = [{"id": "OPS", "value": ops}]
    if datarecord:
        signal.append({"id": "DataRecord", "value": datarecord})
    if discdata:
        signal.append({"id": "DiscData", "value": discdata})
    return build_result(signal, card={"gac": {"response": gac}})


def _verdict(case: dict, **kwargs):
    rules = [MatchRule.model_validate(r) for r in case["expected_results"]]
    return assert_result(_out(**kwargs), rules)


def test_case_passes_on_the_logs_approved_out(case: dict):
    verdict = _verdict(case, ops=OPS_APPROVED, datarecord=DATA_RECORD,
                       discdata=DISC_DATA)
    assert verdict.status == "PASS", [r.message for r in verdict.failed]


def test_case_fails_when_the_card_is_declined(case: dict):
    """Non-vacuous contrast: without CDA the TAC denial bit declines the txn."""
    verdict = _verdict(case, ops="20F0F000B0F0FF00",
                       datarecord=DATA_RECORD.replace("9F270140", "9F270100"),
                       discdata="9F42020978DF8115060000000020FF")
    assert verdict.status == "FAIL"
    failed = {r.rule.path for r in verdict.rules if not r.ok
              and r.rule.severity != "warn"}
    assert {"outcome", "resp_code", "out.ops.status"} <= failed
