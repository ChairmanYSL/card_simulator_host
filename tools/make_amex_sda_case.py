"""Generate the AMEX ``GPO-PROC71-XP2`` case and its test-CA CAPK entry.

Why this tool exists
--------------------
``GPO-PROC71-XP2`` checks that the terminal processes the data the card returns
in GPO (AIP ``58 80`` = SDA supported, AFL whose first entry points at the
certificate record) and completes SDA, so the transaction approves offline.

SDA cannot run against the vendor CAPK set that ships with ``CONF_01``: tag 90
(the Issuer Public Key Certificate) must be a genuine signature of the CA
private key, and those private keys do not exist here.  This tool therefore

* generates a **test CA key pair** and appends its public half as an extra CAPK
  entry (key index ``0A``) to ``cases/amex/pos_configs/CONF_01.json``, and
* generates an **issuer key pair** whose public half is embedded in tag 90 and
  whose private half travels in the case's ``keys`` bundle, so the card signs
  the SSAD (tag 93) at runtime (EMV Book 2 §5.4, raw RSA).

Derived bytes (all verified by the terminal, EMV 4.4 Book 2):

  tag 90  (Issuer Public Key Certificate, length = CA modulus length)
          6A 02 <issuer id 4> <MMYY 2> <serial 3> 01 01 <mod len> <exp len>
          <issuer modulus, capacity bytes> <SHA1(elements 2..10 || remainder ||
          exponent)> BC, signed with the CA private key (block^d mod n)
  tag 92  issuer public key remainder (needed when NI > NCA - 36)
  tag 9F32 issuer public key exponent (010001 = 2^16+1)
  tag 8F  CAPK index the terminal must pick (RID = the card's AID RID)

Key material is reused from ``tmp/sda_keys/amex_gpo_proc71.json`` when present,
so re-running keeps the committed case byte-identical; delete that file to roll
fresh keys (the CAPK entry and the case are then rewritten consistently).

Usage:
    python tools/make_amex_sda_case.py --dry-run     # report only
    python tools/make_amex_sda_case.py               # write case + CONF_01
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
from emv_host.protocol.kernel_tlv import compute_capk_checksum  # noqa: E402

POS_CONFIGS = REPO / "cases" / "amex" / "pos_configs"
CONF_FILE = POS_CONFIGS / "CONF_01.json"
INDEX_FILE = POS_CONFIGS / "index.json"
CASE_FILE = REPO / "cases" / "amex" / "contactless" / "cases" / "GPO-PROC71-XP2.json"
KEY_FILE = REPO / "tmp" / "sda_keys" / "amex_gpo_proc71.json"
REPORT_FILE = REPO / "tmp" / "sda_report.json"

CASE_ID = "GPO-PROC71-XP2"
RID = "A000000025"
CA_KEY_INDEX = "0A"
NCA = 128           # CA modulus length in bytes (1024-bit)
NI = 128            # issuer modulus length in bytes (1024-bit)
CERT_CAPACITY = NCA - 36        # bytes of the issuer modulus tag 90 can carry
REMAINDER_LEN = NI - CERT_CAPACITY
CERT_EXPIRY_MMYY = "3012"       # December 2030 (must be >= the terminal date)
EXPONENT = 0x10001              # 2^16 + 1, encoded as 010001
EXPONENT_HEX = "010001"
RSA_KEY_ID = "sda-issuer-gpo71"

#: Record layout.  SFI 2 carries the SDA material (first AFL entry, per Book 3
#: §7.1 the certificates belong in the first record the AFL references); the
#: application record keeps the reference log's tag order, with the masked
#: PAN/track 2 replaced by generated values (the reference masked them to Fs).
SDA_CERT_RECORD_FIELDS = [
    "caPublicKeyIndex",
    "issuerPublicKeyExponent",
    "issuerPublicKeyCertificate",
    "issuerPublicKeyRemainder",
]
SDA_SSAD_RECORD_FIELDS = ["sda"]
APP_RECORD_FIELDS = [
    "cardholderName",
    "cdol1",
    "cdol2",
    "cardInterfaceAndPaymentCapabilities",
    "applicationVersionNumber",
    "applicationUsageControl",
    "issuerCountryCode",
    "applicationCurrencyCode",
    "cvmList",
    "applicationEffectiveDate",
    "expiryDate",
    "pan",
    "panSequenceNumber",
    "iacDefault",
    "iacDenial",
    "iacOnline",
    "track2",
]

#: EMV SDA Tag List: the reference record does not carry 9F4A, so the terminal
#: appends nothing to the static data (Book 3 §10.3).
AFL = "1701010008010100"        # SFI 2 rec 1 (0 ODA records) + SFI 1 rec 1 (0)
PDOL = "9F6E049F35019F370495059F34039F02069F0306"
CDOL1 = "9F02069F03069F1A0295055F2A029A039C019F37049F6E049F3403"
CDOL2 = "8A029F02069F03069F1A0295055F2A029A039C019F37049F6E049F3403"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _tlv(tag: str, value_hex: str) -> bytes:
    raw = bytes.fromhex(value_hex)
    if len(raw) < 0x80:
        return bytes.fromhex(tag) + bytes([len(raw)]) + raw
    return bytes.fromhex(tag) + bytes([0x81, len(raw)]) + raw


def luhn_pan(prefix: str, body: str) -> str:
    """Build a Luhn-valid PAN from a prefix + body (check digit appended)."""
    digits = list(prefix + body)
    for check in range(10):
        candidate = digits + [str(check)]
        total = 0
        for index, ch in enumerate(reversed(candidate)):
            value = int(ch)
            if index % 2 == 1:
                value *= 2
                if value > 9:
                    value -= 9
            total += value
        if total % 10 == 0:
            return "".join(candidate)
    raise AssertionError("no Luhn digit found")


def pan_to_bcd(pan: str) -> str:
    """Mirror the card app's ``Hex.panToBcd``: digits, right-padded with F when odd."""
    digits = "".join(ch for ch in pan if ch.isdigit())
    if len(digits) % 2:
        digits += "F"
    return digits.upper()


def load_or_create_keys(path: Path, dry_run: bool) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    if dry_run:
        raise SystemExit(f"{path} does not exist yet; run without --dry-run once")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    def make() -> tuple[object, dict]:
        key = rsa.generate_private_key(public_exponent=EXPONENT, key_size=1024)
        numbers = key.private_numbers().public_numbers
        top = numbers.n.bit_length()
        assert top > 0x6A * 8, "modulus too small for the 0x6A recovery header"
        return key, {
            "n": f"{numbers.n:0{NCA * 2}X}",
            "d": f"{key.private_numbers().d:0{NCA * 2}X}",
            "e": EXPONENT_HEX,
            "spki_b64": base64.b64encode(key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo)).decode(),
            "pkcs8_b64": base64.b64encode(key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption())).decode(),
        }

    _, ca = make()
    _, issuer = make()
    data = {
        "_comment": "test-only SDA key material for GPO-PROC71-XP2; "
                    "regenerate by deleting this file",
        "ca": ca,
        "issuer": issuer,
        "serial": "000001",       # 3-byte certificate serial number
        "dac": "0139",            # 2-byte Data Authentication Code inside the SSAD
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def raw_sign(modulus_hex: str, private_hex: str, block: bytes) -> bytes:
    """EMV recovery is the plain RSA primitive: signature = block^d mod n."""
    modulus = int(modulus_hex, 16)
    private = int(private_hex, 16)
    m = int.from_bytes(block, "big")
    assert m < modulus, "recoverable block is not smaller than the modulus"
    return pow(m, private, modulus).to_bytes(NCA, "big")


def build_issuer_certificate(ca: dict, issuer: dict, serial_hex: str,
                             pan_bcd_hex: str) -> tuple[str, str, str]:
    """Return (tag90 hex, tag92 remainder hex, hash hex) per Book 2 Table 6."""
    modulus = int(issuer["n"], 16).to_bytes(NI, "big")
    head = modulus[:CERT_CAPACITY]
    remainder = modulus[CERT_CAPACITY:]

    block = bytearray(NCA)
    block[0] = 0x6A                                   # Recovered Data Header
    block[1] = 0x02                                   # Certificate Format
    block[2:6] = bytes.fromhex(pan_bcd_hex)[:4]       # Issuer Identifier (4B)
    block[6:8] = bytes.fromhex(CERT_EXPIRY_MMYY)      # Certificate Expiration
    block[8:11] = bytes.fromhex(serial_hex)           # Certificate Serial Number
    block[11] = 0x01                                  # Hash Algorithm = SHA-1
    block[12] = 0x01                                  # RSA
    block[13] = NI                                    # Issuer Public Key Length
    block[14] = len(bytes.fromhex(EXPONENT_HEX))      # Exponent Length (3)
    block[15:15 + CERT_CAPACITY] = head               # modulus (capacity bytes)
    # Book 2 §5.3 step 5-6: hash elements 2..10, then the remainder (if any),
    # then the exponent (as many bytes as the exponent-length byte says).
    digest = hashlib.sha1(
        bytes(block[1:NCA - 21]) + remainder + bytes.fromhex(EXPONENT_HEX)
    ).digest()
    block[NCA - 21:NCA - 1] = digest
    block[NCA - 1] = 0xBC                             # Recovered Data Trailer

    signature = raw_sign(ca["n"], ca["d"], bytes(block))
    return signature.hex().upper(), remainder.hex().upper(), digest.hex().upper()


def build_ssad(issuer: dict, dac_hex: str, static_data_hex: str) -> tuple[str, str]:
    """Return (tag93 hex, sha1(prefix || static data) hex) per Book 2 Table 7."""
    dac = bytes.fromhex(dac_hex)
    prefix = bytearray(NI - 22)
    prefix[0] = 0x03                # Signed Data Format
    prefix[1] = 0x01                # Hash Algorithm Indicator (SHA-1)
    prefix[2:4] = dac               # Data Authentication Code
    for index in range(4, len(prefix)):
        prefix[index] = 0xBB        # Pad Pattern
    digest = hashlib.sha1(bytes(prefix) + bytes.fromhex(static_data_hex)).digest()
    block = bytearray(NI)
    block[0] = 0x6A
    block[1:1 + len(prefix)] = prefix
    block[NI - 21:NI - 1] = digest
    block[NI - 1] = 0xBC
    return raw_sign(issuer["n"], issuer["d"], bytes(block)).hex().upper(), digest.hex().upper()


def capk_entry(ca: dict, serial_hex: str) -> dict:
    checksum = compute_capk_checksum(RID, CA_KEY_INDEX, ca["n"], EXPONENT_HEX)
    return {
        "rid": RID,
        "key_index": CA_KEY_INDEX,
        "hash_alg": 1,
        "key_alg": 0,
        "modulus": ca["n"],
        "exponent": EXPONENT_HEX,
        "checksum": checksum,
        "expiry": "20301231",
        "expiry_encoding": "bcd3",
    }


def patch_conf(entry: dict, dry_run: bool) -> tuple[int, bool]:
    conf = json.loads(CONF_FILE.read_text(encoding="utf-8"))
    before = len(conf["caps"])
    conf["caps"] = [c for c in conf["caps"]
                    if str(c.get("key_index", "")).upper() != CA_KEY_INDEX]
    replaced = len(conf["caps"]) != before
    conf["caps"].append(entry)
    if not dry_run:
        CONF_FILE.write_text(json.dumps(conf, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        for item in index["configs"]:
            if item["id"] == "CONF_01":
                item["capk"] = len(conf["caps"])
                item.setdefault("note", "")
                item["note"] = ("capk includes one locally generated test CA key "
                                f"(index {CA_KEY_INDEX}) for GPO-PROC71-XP2 SDA")
        INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    return len(conf["caps"]), replaced


def build_case(ca: dict, issuer: dict, cert90: str, rem92: str, dac: str,
               pan: str, pan_bcd_hex: str) -> dict:
    expiry = "491231"
    track2_ascii = f"{pan}={expiry[:4]}201"
    return {
        "case_id": CASE_ID,
        "title": "GPO 处理（终端正确处理 GPO 返回的卡片数据 + SDA）",
        "summary": (
            "GPO 响应 AIP=5880（卡片支持 SDA）、AFL=17010100 08010100（第一条指向证书记录）；"
            "终端读取证书记录（8F/9F32/90/92）与应用记录，用 CONF_01 的测试 CA 公钥恢复签发者公钥、"
            "校验 SSAD（93），完成静态数据认证后应脱机批准（acBehavior=offline_approved → TC）。"
        ),
        "brand": "amex",
        "entry_mode": "contactless",
        "profile": {
            "id": CASE_ID,
            "name": "AMEX GPO 处理（GPO.PROC71.XP2）",
            "network": "AMEX",
            "isActive": True,
            "aids": [{"aid": "A000000025010403", "label": "AMEX", "priority": 0}],
            "pan": pan,
            "panSequenceNumber": 1,
            "applicationExpirationDate": expiry,
            "cardholderName": "USER1",
            "track2EquivalentData": track2_ascii.encode("ascii").hex().upper(),
            "applicationLabel": "AMEX",
            "applicationVersionNumber": "0001",
            "aip": "5880",
            "afl": AFL,
            "issuerCountryCode": "0620",
            "pdol": PDOL,
            "cdol1": CDOL1,
            "cdol2": CDOL2,
            "cvmList": [{"cvmCode": 2, "cvmCondition": 0, "applySucceedingCvm": 1}],
            "applicationUsageControl": "FFC0",
            "applicationCurrencyCode": "0978",
            "applicationEffectiveDate": "101004",
            "iacDefault": "0000000000",
            "iacDenial": "0000000000",
            "iacOnline": "0000000000",
            "cardInterfaceAndPaymentCapabilities": "1000",
            "rsaKeyId": RSA_KEY_ID,
            "caPublicKeyIndex": CA_KEY_INDEX,
            "issuerPublicKeyExponent": EXPONENT_HEX,
            "issuerPublicKeyCertificate": cert90,
            "issuerPublicKeyRemainder": rem92,
            "dataAuthenticationCode": dac,
            "acBehavior": "offline_approved",
            "records": [
                {"sfi": 2, "record": 1, "fields": list(SDA_CERT_RECORD_FIELDS)},
                {"sfi": 2, "record": 2, "fields": list(SDA_SSAD_RECORD_FIELDS)},
                {"sfi": 1, "record": 1, "fields": list(APP_RECORD_FIELDS)},
            ],
        },
        "kernel_params_ref": "CONF_01",
        "terminal_profile_ref": "PROFILE_01",
        "keys": {
            "symmetric_keys": [],
            "rsa_keys": [{
                "id": RSA_KEY_ID,
                "public_key": issuer["spki_b64"],
                "private_key": issuer["pkcs8_b64"],
            }],
        },
        "transaction": {
            "operator_instructions": {
                "amount": "15.00",
                "currency": "EUR",
                "entry_mode": "contactless",
                "pin_hint": "none",
                "notes": "tap the phone on the POS reader when it shows 15.00 EUR",
            },
            "expected_values": {
                "amount": "15.00",
                "currency": "978",
                "entry_mode": "contactless",
            },
            "act": [
                {"id": "9F02", "value": "000000001500"},
                {"id": "9F03", "value": "000000000000"},
                {"id": "9C", "value": "00"},
            ],
        },
        "preconditions": [
            "phone NFC enabled and card simulator app in foreground",
            "POS loaded with CONF_01 (which now carries the test CA key index 0A) "
            "+ PROFILE_01 (9F33=000888 → SDA supported by the terminal)",
            "card AIP=5880 selects SDA in the kernel (no CDA: AIP byte1 bit1 = 0)",
            "the CVM list mirrors the reference (8E=00000000000000000200)",
        ],
        "test_plan": [
            {"step": 1, "action": "host pushes card profile (AID A000000025010403, "
                                  "PDOL with 9F6E+9F35, SDA material) + issuer key "
                                  "and starts emulation",
             "expect": "android reports emulating"},
            {"step": 2, "action": "operator completes the 15.00 EUR transaction",
             "expect": "POS sends GPO (AIA) then reads SFI 2 rec 1-2 and SFI 1 rec 1"},
            {"step": 3, "action": "host judges the APDU trace: GPO response AIP/AFL "
                                  "and the certificate/SSAD records",
             "expect": "card.gpo.aip=5880, card.gpo.afl=1701010008010100, "
                       "records carry 8F=0A, 90 and 93"},
            {"step": 4, "action": "host asserts the transaction outcome",
             "expect": "offline approved (outcome=approved, online=offline, "
                       "resp_code=00) → SDA verified by the terminal"},
        ],
        "timeout_seconds": 180,
        "retry_on_fail": 0,
        "expected_results": [
            {"path": "card.gpo.aip", "op": "eq", "expect": "5880"},
            {"path": "card.gpo.afl", "op": "eq", "expect": AFL},
            {"path": "card.read_record.records.2.1.90", "op": "present"},
            {"path": "card.read_record.records.2.1.8F", "op": "eq", "expect": CA_KEY_INDEX},
            {"path": "card.read_record.records.2.2.93", "op": "present"},
            {"path": "outcome", "op": "eq", "expect": "approved"},
            {"path": "online", "op": "eq", "expect": "offline"},
            {"path": "resp_code", "op": "eq", "expect": "00"},
        ],
    }


def recover(modulus_hex: str, exponent_hex: str, signature_hex: str,
            length: int) -> bytes:
    """Terminal-side recovery: recovered = signature^e mod n (Book 2 Annex A2.1)."""
    return pow(int(signature_hex, 16), int(exponent_hex, 16),
               int(modulus_hex, 16)).to_bytes(length, "big")


def selfcheck(ca: dict, issuer: dict, cert90: str, rem92: str, ssad93: str,
              dac_hex: str, static_data_hex: str) -> list[str]:
    """Reproduce the kernel's SDA verification offline (Book 2 §5.3 / §5.4).

    Mirrors ``retrieve_pk_from_icc`` + ``verify_ssad`` in
    Expresspay/submodule/emvco/src/emv_offline_data_authentication.c.
    """
    problems: list[str] = []
    cert = recover(ca["n"], EXPONENT_HEX, cert90, NCA)
    if (cert[0], cert[1], cert[NCA - 1]) != (0x6A, 0x02, 0xBC):
        problems.append("tag 90 header/format/trailer")
    if (cert[11], cert[12]) != (0x01, 0x01):
        problems.append("tag 90 hash/algorithm indicators")
    if cert[13] != NI or cert[14] != len(bytes.fromhex(EXPONENT_HEX)):
        problems.append("tag 90 key/exponent length bytes")
    hash_input = (bytes(cert[1:NCA - 21]) + bytes.fromhex(rem92)
                  + bytes.fromhex(EXPONENT_HEX))
    if hashlib.sha1(hash_input).digest() != bytes(cert[NCA - 21:NCA - 1]):
        problems.append("tag 90 hash result")
    recovered = bytes(cert[15:15 + CERT_CAPACITY]) + bytes.fromhex(rem92)
    if recovered != int(issuer["n"], 16).to_bytes(NI, "big"):
        problems.append("tag 90 recovered issuer modulus")
    if bytes(cert[6:8]).hex().upper() != CERT_EXPIRY_MMYY:
        problems.append("tag 90 expiry encoding")

    ssad = recover(issuer["n"], EXPONENT_HEX, ssad93, NI)
    if (ssad[0], ssad[1], ssad[NI - 1]) != (0x6A, 0x03, 0xBC):
        problems.append("tag 93 header/format/trailer")
    if ssad[3:5].hex().upper() != dac_hex.upper():
        problems.append("tag 93 data authentication code")
    digest = hashlib.sha1(bytes(ssad[1:NI - 21])
                          + bytes.fromhex(static_data_hex)).digest()
    if digest != bytes(ssad[NI - 21:NI - 1]):
        problems.append("tag 93 hash result")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="report the generated material without writing files")
    args = parser.parse_args()

    keys = load_or_create_keys(KEY_FILE, args.dry_run)
    ca, issuer = keys["ca"], keys["issuer"]
    assert len(bytes.fromhex(ca["n"])) == NCA, "CA modulus length changed"
    assert len(bytes.fromhex(issuer["n"])) == NI, "issuer modulus length changed"
    pan = luhn_pan("34", "0000020000000")
    pan_bcd_hex = pan_to_bcd(pan)

    cert90, rem92, cert_hash = build_issuer_certificate(
        ca, issuer, keys["serial"], pan_bcd_hex)
    # The AFL marks no record as participating in ODA (both byte-4 values are 0)
    # and no record carries the SDA Tag List, so the static data is empty.
    static_data_hex = ""
    ssad93, ssad_hash = build_ssad(issuer, keys["dac"], static_data_hex)
    entry = capk_entry(ca, keys["serial"])

    drl_hit = any(str(d.get("serial", "")).upper().endswith(keys["serial"].upper())
                  for d in json.loads(CONF_FILE.read_text(encoding="utf-8"))["drl"])
    if drl_hit:
        raise SystemExit(f"certificate serial {keys['serial']} is on CONF_01's DRL")

    conf_caps, replaced = patch_conf(entry, args.dry_run)
    case = build_case(ca, issuer, cert90, rem92, keys["dac"], pan, pan_bcd_hex)
    problems = selfcheck(ca, issuer, cert90, rem92, ssad93, keys["dac"],
                         static_data_hex)
    if problems:
        raise SystemExit("SELFCHECK FAILED: " + "; ".join(problems))
    if not args.dry_run:
        CASE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CASE_FILE.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        REPORT_FILE.write_text(json.dumps({
            "case_id": CASE_ID,
            "ca": {"modulus": ca["n"], "exponent": EXPONENT_HEX,
                   "checksum": entry["checksum"], "key_index": CA_KEY_INDEX},
            "issuer": {"modulus": issuer["n"], "exponent": EXPONENT_HEX,
                       "serial": keys["serial"]},
            "tag90": cert90, "tag90_hash": cert_hash, "tag92": rem92,
            "dac": keys["dac"], "tag93": ssad93, "tag93_hash": ssad_hash,
            "static_data_hex": static_data_hex, "afl": AFL,
            "sizes": {"NCA": NCA, "NI": NI, "cert_capacity": CERT_CAPACITY,
                      "remainder": REMAINDER_LEN},
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"{'DRY-RUN' if args.dry_run else 'WROTE'} {CASE_FILE.relative_to(REPO)}")
    print(f"{'DRY-RUN' if args.dry_run else 'WROTE'} {CONF_FILE.relative_to(REPO)}"
          f" (CAPK entries: {conf_caps}, replaced existing index {CA_KEY_INDEX}: {replaced})")
    print(f"  PAN                : {pan} (issuer id {pan_bcd_hex[:8]})")
    print(f"  CA modulus         : {len(bytes.fromhex(ca['n']))}B, checksum {entry['checksum']}")
    print(f"  issuer modulus     : {len(bytes.fromhex(issuer['n']))}B, "
          f"cert capacity {CERT_CAPACITY}B, remainder {REMAINDER_LEN}B")
    print(f"  tag90 / tag92 / 93 : {len(cert90) // 2}B / {len(rem92) // 2}B / {len(ssad93) // 2}B")
    print(f"  DAC                : {keys['dac']}   static data: '{static_data_hex}' (empty per AFL)")
    print(f"  expected SSAD hash : {ssad_hash}")
    print(f"  report             : {REPORT_FILE.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
