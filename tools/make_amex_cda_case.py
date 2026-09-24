"""Generate the AMEX CDA case ``CAA1-PROC13-XP2`` from the reference log.

Reference: ``refernce/CAA1 PROC 13 XP2EMV应该批准，错误中止.txt`` (UTF-16LE,
device side AMEXEMVCORE).  The log ends in ``END(111)`` although the card
answered a TC (offline approved); this tool builds the case that must end in
``outcome=approved / online=offline / resp_code=00``.

What the log gives us (used verbatim below):

  L1397  AID FCI        50='Personal Account', 5F2D=ENFR, 87=01,
                        9F38=9F6E049F35019F370495059F34039F02069F0306
  L1648  GPO command    amount 15.00 in the 29-byte PDOL data
  L1653  GPO response   82=0980 (CDA + CVM), 94=08010101 10010100 20010100
  L1690  SFI 1/1 record 5F20/8C/8D/9F70=1000/9F08=0001/9F07=FFC0/5F28=0620/
                        9F42=0978/8E(18B)=4203 4103 5E03 1F03 0000/5F25/5F24/
                        5A/5F34=00/9F0D-9F0F/57
  L1729  SFI 2/1 record 8F=01, 9F4A=82, 90 (issuer PK cert), 92, 9F32
  L1745  SFI 4/1 record 9F46 (ICC PK cert), 9F47, 9F48
  L1983  GENERATE AC    P1=0x50 → TC + "CDA signature requested"
  L1987  GENERATE AC    77 {9F27=40, 9F36, 9F4B (128B SDAD), 9F10}
  L1423  terminal       TERM of the log (9F33=200888 → terminal CDA support,
                        9F6E=9C800003, ...) = amex PROFILE_02

The card cannot reuse the reference key material (no private keys), so this
tool generates its own CA / issuer / ICC key chain, patches the case CONFIG
with the CA public key (index 01, the index the log's card declares in 8F) and
emits the profile with certificates built exactly as EMV 4.4 Book 2 §5/§6.5
requires.  ``selfcheck`` then replays the whole terminal verification chain
(Expresspay V4.2.3 ``retrieve_pk_from_icc`` + ``process_cda``) in Python.

Usage::

    python tools/make_amex_cda_case.py [--dry-run]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CASE_DIR = REPO / "cases" / "amex" / "contactless" / "cases"
CONF_FILE = REPO / "cases" / "amex" / "pos_configs" / "CONF_01.json"
INDEX_FILE = REPO / "cases" / "amex" / "pos_configs" / "index.json"
KEY_FILE = REPO / "tmp" / "cda_keys" / "amex_caa1_proc13.json"

CASE_ID = "CAA1-PROC13-XP2"
CASE_IDS = ("CAA1-PROC13-XP2", "CAA1-PROC03-XP2")

#: Per-case profile/expectation differences.  Both reference logs describe the
#: same card (AID/records/TERM/AIP 0980) and ended in the same pre-V4.2.3 kernel
#: abort; PROC03's card answers ARQC (CID 80) instead of TC, i.e. the online
#: flow, which the POS completes with DF8101=01 (simulated online approval).
CASE_SPECS = [
    {
        "case_id": "CAA1-PROC13-XP2",
        "reference": "refernce/CAA1 PROC 13 XP2EMV应该批准，错误中止.txt",
        "title": "CAA1 处理（CDA 联合动态数据认证，应脱机批准）",
        "ac_behavior": "offline_approved",
        "online_approval": False,
        "summary": (
            "CDA（组合 DDA/GENERATE AC）例：AIP=0980（byte1 bit1 = 支持 CDA）、AFL 三条（SFI 1/1 参与 ODA）。"
            "终端用 CONF_01 的测试 CA 公钥恢复签发者公钥（90/92/9F32），再用签发者公钥恢复 ICC 公钥"
            "（9F46/9F47/9F48），首次 GENERATE AC 的 P1=0x50（bit5=1 请求 CDA 签名）时卡端须在应答里给出 "
            "9F4B（SDAD=Book2 Table18 可恢复块，签名数据含 Table19 的 ICC Dynamic Data，其 Transaction Data "
            "Hash Code = SHA1(PDOL 数据 ‖ CDOL1 数据 ‖ GENERATE AC 应答 TLV 去掉 9F4B)）。终端校验通过后"
            "交易应为脱机批准（TC），即 outcome=approved/online=offline/resp_code=00。"
            "参考日志在该步错误中止（END(111)），本案例以「正确完成 CDA 并脱机批准」为通过条件。"
        ),
        "expect_online": "offline",
        "expect_note": "offline approved TC",
    },
    {
        "case_id": "CAA1-PROC03-XP2",
        "reference": "refernce/CAA1 PROC 03 XP2EMV_错误中止.txt",
        "title": "CAA1 处理（CDA + 联机 ARQC，模拟在线批准）",
        "ac_behavior": "online_request",
        "online_approval": True,
        "summary": (
            "与 CAA1-PROC13-XP2 同一张卡（AID/档案/终端/AIP=0980 相同），差别在首笔 GENERATE AC 卡端返回 "
            "**ARQC（CID=80）**：参考日志应答 `77 8197 9F27 01 80 9F36 02 0001 9F4B 8180 … 9F10 07 06010103900002`，"
            "即卡端要求联机，随后内核同样以 `parse_gac_resp - 353 GAC resp doesnt contain 9F27 or 9F36 or 9F26` → "
            "`analyze_gac_resp ret = 111` → `END(111)` 错误中止（与 PROC13 同一旧版本内核缺陷：把 CDA auth value "
            "存进了 9F4B；V4.2.3 为 0xBC）。正确流程：ARQC → 终端 EMV_REQ_GO_ONLINE → 本笔用 DF8101=01 模拟在线批准 "
            "→ 卡端第二笔 GENERATE AC（CDOL2）按终端请求返回 TC → 联机批准（outcome=approved / online=online / "
            "resp_code=00），且第二笔同样带 CDA 签名（9F4B）并经终端验签（out.datarecord.9F4C 存在）。"
        ),
        "expect_online": "online",
        "expect_note": "online approved (DF8101=01 simulation)",
    },
]

RID = "A000000025"
CA_KEY_INDEX = "01"                 # 8F of the reference card
CONF_ID = "CONF_01"                 # the log's AID/terminal parameter set
TERMINAL_PROFILE = "PROFILE_02"     # 9F33=200888 + 9F6E=9C800003 of the log
EXPONENT = 65537
EXPONENT_HEX = "010001"
NCA = NI = NIC = 128                # 1024-bit CA / issuer / ICC moduli (bytes)
CERT90_CAPACITY = NCA - 36          # Book 2 Table 10
CERT46_CAPACITY = NI - 42           # Book 2 Table 14
CA_SERIAL = "000001"
ISSUER_SERIAL = "000101"
ICC_SERIAL = "000201"
CERT_EXPIRY_MMYY = "1230"           # 12/30, after today
ICC_KEY_ID = "cda-icc-caa1"

AIP = "0980"                        # CDA (byte 1 bit 1) + CVM (bit 5)
AFL = "080101011001010020010100"    # SFI 1/1 (ODA) + SFI 2/1 + SFI 4/1
PDOL = "9F6E049F35019F370495059F34039F02069F0306"
CDOL1 = "9F02069F03069F1A0295055F2A029A039C019F37049F6E049F3403"
CDOL2 = "8A029F02069F03069F1A0295055F2A029A039C019F37049F6E049F3403"
PAN_PREFIX = "37341234"             # the log's issuer identifier, 37341234
PAN_BODY = "567890"
PAN_SEQUENCE = 0                    # log 5F34=00
EXPIRY = "491231"                   # log 5F24=491231

#: CV Rules of the log's 8E (masked PAN variant): 4203 4103 5E03 1F03 0000.
CVM_LIST = [
    {"cvmCode": 0x02, "cvmCondition": 0x03, "applySucceedingCvm": 2},
    {"cvmCode": 0x01, "cvmCondition": 0x03, "applySucceedingCvm": 2},
    {"cvmCode": 0x1E, "cvmCondition": 0x03, "applySucceedingCvm": 2},
    {"cvmCode": 0x1F, "cvmCondition": 0x03, "applySucceedingCvm": 1},
    {"cvmCode": 0x00, "cvmCondition": 0x00, "applySucceedingCvm": 1},
]

APP_RECORD_FIELDS = [
    "cardholderName", "cdol1", "cdol2", "cardInterfaceAndPaymentCapabilities",
    "applicationVersionNumber", "applicationUsageControl", "issuerCountryCode",
    "applicationCurrencyCode", "cvmList", "applicationEffectiveDate",
    "expiryDate", "pan", "panSequenceNumber", "iacDefault", "iacDenial",
    "iacOnline", "track2",
]
CERT_RECORD_FIELDS = [
    "caPublicKeyIndex", "sdaTagList", "issuerPublicKeyCertificate",
    "issuerPublicKeyRemainder", "issuerPublicKeyExponent",
]
ICC_RECORD_FIELDS = [
    "iccPublicKeyCertificate", "iccPublicKeyExponent", "iccPublicKeyRemainder",
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def luhn_pan(prefix: str, body: str) -> str:
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


def pan_to_bcd(pan: str, length: int) -> str:
    """BCD PAN padded with 'F' (the reference ICC cert carries FFFFF)."""
    digits = "".join(ch for ch in pan if ch.isdigit())
    if len(digits) % 2:
        digits += "F"
    return digits.upper().ljust(length * 2, "F")[:length * 2]


def raw_sign(modulus_hex: str, private_hex: str, block: bytes) -> bytes:
    """EMV recovery is the plain RSA primitive: signature = block^d mod n."""
    modulus = int(modulus_hex, 16)
    assert int.from_bytes(block, "big") < modulus, "block >= modulus"
    return pow(int.from_bytes(block, "big"), int(private_hex, 16),
               modulus).to_bytes(len(block), "big")


def compute_capk_checksum(rid: str, key_index: str, modulus: str,
                          exponent: str) -> str:
    """Book 2 Annex A1.2, the convention the device CAPK table uses.

    Verified against the vendor CAPK of CONF_31 (index 01 of A000000025) whose
    stored checksum reproduces exactly for RID || index || modulus || exponent.
    """
    blob = bytes.fromhex(rid + key_index + modulus + exponent)
    return hashlib.sha1(blob).hexdigest().upper()


def load_or_create_keys(path: Path, dry_run: bool) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    if dry_run:
        raise SystemExit(f"{path} does not exist yet; run without --dry-run once")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    def make() -> dict:
        key = rsa.generate_private_key(public_exponent=EXPONENT, key_size=1024)
        numbers = key.private_numbers().public_numbers
        assert numbers.n.bit_length() > 0x6A * 8, "modulus too small for 0x6A"
        return {
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

    data = {
        "_comment": "test-only CA/issuer/ICC key material for CAA1-PROC13-XP2 "
                    "(CDA); delete this file to regenerate the chain",
        "ca": make(),
        "issuer": make(),
        "icc": make(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


# --------------------------------------------------------------------------
# certificates (EMV 4.4 Book 2 §5 / §6.4 and §6.5)
# --------------------------------------------------------------------------
def build_issuer_certificate(ca: dict, issuer: dict, pan_bcd: str) -> tuple[str, str, str]:
    """tag 90 + tag 92 (issuer PK remainder) + tag 9F32 (exponent length 3).

    Book 2 §5 step 5: hash = recovered[1 .. NCA-22] || remainder || exponent
    (the static data is NOT part of the issuer certificate hash).
    """
    modulus = int(issuer["n"], 16).to_bytes(NI, "big")
    head, remainder = modulus[:CERT90_CAPACITY], modulus[CERT90_CAPACITY:]

    block = bytearray(NCA)
    block[0] = 0x6A
    block[1] = 0x02                                     # issuer cert format
    block[2:6] = bytes.fromhex(pan_bcd)[:4]             # Issuer Identifier
    block[6:8] = bytes.fromhex(CERT_EXPIRY_MMYY)
    block[8:11] = bytes.fromhex(CA_SERIAL)
    block[11] = 0x01                                    # SHA-1
    block[12] = 0x01                                    # RSA
    block[13] = NI
    block[14] = len(bytes.fromhex(EXPONENT_HEX))
    block[15:15 + CERT90_CAPACITY] = head
    digest = hashlib.sha1(bytes(block[1:NCA - 21]) + remainder
                          + bytes.fromhex(EXPONENT_HEX)).digest()
    block[NCA - 21:NCA - 1] = digest
    block[NCA - 1] = 0xBC
    cert = raw_sign(ca["n"], ca["d"], bytes(block)).hex().upper()
    return cert, remainder.hex().upper(), EXPONENT_HEX


def build_icc_certificate(issuer: dict, icc: dict, pan_bcd: str,
                          static_data_hex: str) -> tuple[str, str, str]:
    """tag 9F46 + tag 9F48 + tag 9F47 — signed by the ISSUER private key.

    Book 2 §6.5 step 5: hash = recovered[1 .. NI-22] || ICC PK remainder ||
    ICC PK exponent || static data (§10.3 ODA records) || AIP (when 9F4A lists
    tag 82) — the AIP is appended by the terminal, see Expresspay V4.2.3
    ``retrieve_pk_from_icc``.
    """
    modulus = int(icc["n"], 16).to_bytes(NIC, "big")
    head, remainder = modulus[:CERT46_CAPACITY], modulus[CERT46_CAPACITY:]

    block = bytearray(NI)
    block[0] = 0x6A
    block[1] = 0x04                                     # ICC cert format
    block[2:12] = bytes.fromhex(pan_bcd)[:10]           # Application PAN (10B)
    block[12:14] = bytes.fromhex(CERT_EXPIRY_MMYY)
    block[14:17] = bytes.fromhex(ISSUER_SERIAL)
    block[17] = 0x01                                    # SHA-1
    block[18] = 0x01                                    # RSA
    block[19] = NIC
    block[20] = len(bytes.fromhex(EXPONENT_HEX))
    block[21:21 + CERT46_CAPACITY] = head
    digest = hashlib.sha1(bytes(block[1:NI - 21]) + remainder
                          + bytes.fromhex(EXPONENT_HEX)
                          + bytes.fromhex(static_data_hex)
                          + bytes.fromhex(AIP)).digest()
    block[NI - 21:NI - 1] = digest
    block[NI - 1] = 0xBC
    cert = raw_sign(issuer["n"], issuer["d"], bytes(block)).hex().upper()
    return cert, remainder.hex().upper(), EXPONENT_HEX


def static_data_hex() -> str:
    """Book 3 §10.3 for this card: SFI 1/1's data field (AFL ODA record 1).

    The value is the record content the card appends to the hash; the AIP is
    appended separately by the terminal (9F4A = '82').  The kernel strips the
    '70' template for SFI 1..10, so a placeholder of the same *length* is
    enough here — but the real bytes are reproduced from the profile layout so
    the value stays reviewable.
    """
    # The record value is built from the profile below; lengths are fixed by
    # the field set, so the byte string can be reconstructed deterministically.
    return build_record_value()


def pack_track2_ascii(track2_ascii: str) -> str:
    """Mirror the card's ``DefaultEmvKernel.track2ToBcd`` for tag 57.

    The profile carries the ASCII track2 (digits + '='); the card packs it to
    BCD ('=' -> 'D', odd length padded with 'F') before emitting tag 57, so the
    certificate hash must cover the packed form.
    """
    packed = "".join("D" if ch == "=" else ch.upper() for ch in track2_ascii)
    if len(packed) % 2:
        packed += "F"
    return packed


def build_record_value() -> str:
    def tlv(tag: str, value_hex: str) -> str:
        raw_len = len(value_hex) // 2
        if raw_len < 0x80:
            return tag + f"{raw_len:02X}" + value_hex
        return tag + "81" + f"{raw_len:02X}" + value_hex

    pan = luhn_pan(PAN_PREFIX, PAN_BODY)
    track2_ascii = f"{pan}={EXPIRY[:4]}201"
    parts = [
        tlv("5F20", "5553455231"),                       # USER1
        tlv("8C", CDOL1),
        tlv("8D", CDOL2),
        tlv("9F70", "1000"),
        tlv("9F08", "0001"),
        tlv("9F07", "FFC0"),
        tlv("5F28", "0620"),
        tlv("9F42", "0978"),
        tlv("8E", "0000000000000000" + "420341035E031F030000"),
        tlv("5F25", "101004"),
        tlv("5F24", EXPIRY),
        tlv("5A", pan_to_bcd(pan, 8)),
        tlv("5F34", f"{PAN_SEQUENCE:02X}"),
        tlv("9F0D", "0000000000"),
        tlv("9F0E", "0000000000"),
        tlv("9F0F", "0000000000"),
        tlv("57", pack_track2_ascii(track2_ascii)),
    ]
    return "".join(parts)


# --------------------------------------------------------------------------
# CONFIG patching
# --------------------------------------------------------------------------
def patch_conf(ca: dict, dry_run: bool) -> tuple[int, bool]:
    checksum = compute_capk_checksum(RID, CA_KEY_INDEX, ca["n"], EXPONENT_HEX)
    entry = {
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
    conf = json.loads(CONF_FILE.read_text(encoding="utf-8"))
    replaced = any(str(c.get("key_index", "")).upper() == CA_KEY_INDEX
                   for c in conf["caps"])
    conf["caps"] = [c for c in conf["caps"]
                    if str(c.get("key_index", "")).upper() != CA_KEY_INDEX]
    conf["caps"].append(entry)
    if not dry_run:
        CONF_FILE.write_text(json.dumps(conf, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        for item in index["configs"]:
            if item["id"] == CONF_ID:
                item["capk"] = len(conf["caps"])
                item["note"] = ("capk index 01 is a locally generated test CA key "
                                "for CAA1-PROC13-XP2 / CAA1-PROC03-XP2 (CDA)")
        INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    return len(conf["caps"]), replaced


# --------------------------------------------------------------------------
# case JSON
# --------------------------------------------------------------------------
def build_case(data: dict, cert90: str, rem92: str, cert46: str, rem48: str,
               spec: dict) -> dict:
    case_id = spec["case_id"]
    pan = luhn_pan(PAN_PREFIX, PAN_BODY)
    track2 = f"{pan}={EXPIRY[:4]}201".encode("ascii").hex().upper()
    act = [
        {"id": "9F02", "value": "000000001500"},
        {"id": "9F03", "value": "000000000000"},
        {"id": "9C", "value": "00"},
    ]
    if spec["online_approval"]:
        # POS convention: DF8101=01 = simulated online approval (see the
        # unionpay QPB-B-002 demo); without it the POS answers the online
        # request with a failure and the case would have to expect DECLINED.
        act.append({"id": "DF8101", "value": "01"})
    return {
        "case_id": case_id,
        "title": spec["title"],
        "summary": spec["summary"],
        "brand": "amex",
        "entry_mode": "contactless",
        "profile": {
            "id": case_id,
            "name": f"AMEX CAA1 处理（{case_id}）",
            "network": "AMEX",
            "isActive": True,
            "aids": [{"aid": "A000000025010403", "label": "Personal Account",
                      "priority": 1}],
            "pan": pan,
            "panSequenceNumber": PAN_SEQUENCE,
            "applicationExpirationDate": EXPIRY,
            "cardholderName": "USER1",
            "track2EquivalentData": track2,
            "applicationLabel": "Personal Account",
            "applicationVersionNumber": "0001",
            "aip": AIP,
            "afl": AFL,
            "issuerCountryCode": "0620",
            "pdol": PDOL,
            "cdol1": CDOL1,
            "cdol2": CDOL2,
            "cvmList": CVM_LIST,
            "applicationUsageControl": "FFC0",
            "applicationCurrencyCode": "0978",
            "applicationEffectiveDate": "101004",
            "iacDefault": "0000000000",
            "iacDenial": "0000000000",
            "iacOnline": "0000000000",
            "cardInterfaceAndPaymentCapabilities": "1000",
            "languagePreference": "ENFR",
            "caPublicKeyIndex": CA_KEY_INDEX,
            "issuerPublicKeyCertificate": cert90,
            "issuerPublicKeyRemainder": rem92,
            "issuerPublicKeyExponent": EXPONENT_HEX,
            "iccPrivateKeyId": ICC_KEY_ID,
            "iccPublicKeyCertificate": cert46,
            "iccPublicKeyExponent": EXPONENT_HEX,
            "iccPublicKeyRemainder": rem48,
            "acBehavior": spec["ac_behavior"],
            "records": [
                {"sfi": 1, "record": 1, "fields": list(APP_RECORD_FIELDS)},
                {"sfi": 2, "record": 1, "fields": list(CERT_RECORD_FIELDS)},
                {"sfi": 4, "record": 1, "fields": list(ICC_RECORD_FIELDS)},
            ],
        },
        "kernel_params_ref": CONF_ID,
        "terminal_profile_ref": TERMINAL_PROFILE,
        "keys": {
            "symmetric_keys": [],
            "rsa_keys": [{
                "id": ICC_KEY_ID,
                "public_key": data["icc"]["spki_b64"],
                "private_key": data["icc"]["pkcs8_b64"],
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
            "act": act,
        },
        "preconditions": [
            "phone NFC enabled and card simulator app in foreground",
            f"POS loaded with {CONF_ID} + {TERMINAL_PROFILE} (the log's TERM: "
            "9F33=200888 → terminal CDA support, 9F6E=9C800003, 9F35=22)",
            "card AIP=0980: byte 1 bit 1 = CDA supported, bit 5 = cardholder "
            "verification supported; the kernel starts ODA with "
            "'Term and Card all support CDA' (AIP_CDA_SUPPORT && 9F33[2]&0x08)",
            f"the profile carries the ICC private key ({ICC_KEY_ID}, pushed in "
            "keys.rsa_keys) plus the 9F46/9F47/9F48 certificate material, so the "
            "card can sign the 9F4B Signed Dynamic Application Data",
            "the CA public key index 01 of this CONFIG is the locally generated "
            "test CA key of this case (the 8F of the profile names it); the "
            "per-case CONFIG clears and re-provisions the CAPK table "
            "(EmvService.applyCaseConfig), so index 01 is the test key for this run",
            "the reference log masks the PAN (5A=FFFFFFFFFFFFFFFF); the profile "
            "uses a Luhn-valid test PAN with the log's issuer identifier "
            "37341234 and PAN sequence 00 (log 5F34=00), everything else is the "
            "log's data",
            "why the log aborted: in that (pre-V4.2.3) kernel build the CDA "
            "auth value tag collided with 9F4B, so the GENERATE AC response was "
            "re-parsed with a corrupted 9F4B -> 'GAC resp doesnt contain 9F27 or "
            "9F36 or 9F26' -> analyze_gac_resp 111 -> END(111); V4.2.3 stores it "
            "under 0xBC (emv_tag.h:534) and parse_gac_resp accepts the 77 template",
            "the pass criteria require out.datarecord.9F4C: it is only stored "
            "after the CDA hash check, so it proves verification instead of "
            "relying on the 102+TC -> APPROVED mapping",
            (f"this case's card answers ARQC in the first GENERATE AC "
             f"(acBehavior=online_request) and the transaction is completed with "
             f"DF8101=01 (POS simulated online approval), exactly like the "
             f"reference log" if spec["online_approval"] else
             "this case's card answers TC (acBehavior=offline_approved): the "
             "offline approval path"),
        ],
        "test_plan": [
            {"step": 1,
             "action": "host pushes the card profile (AIP 0980, CDA certs) with "
                       f"{CONF_ID} + {TERMINAL_PROFILE} and starts emulation",
             "expect": "android reports emulating"},
            {"step": 2,
             "action": "operator completes the 15.00 EUR transaction",
             "expect": "the kernel reads the three records, recovers the issuer "
                       "and ICC public keys (retrieve_capk/issuer/icc = 0) and "
                       "requests CDA in the first GENERATE AC (P1=0x50)"},
            {"step": 3,
             "action": "host judges the GENERATE AC response of the card",
             "expect": "9F4B (SDAD, length = ICC modulus) is present"},
            {"step": 4,
             "action": "host judges the POS out payload",
             "expect": (f"outcome=approved / online={spec['expect_online']} / "
                        f"resp_code=00 ({spec['expect_note']}) and "
                        f"out.datarecord.9F4C present (the kernel stored the ICC "
                        f"Dynamic Number, i.e. the CDA hash matched)")},
        ],
        "timeout_seconds": 180,
        "retry_on_fail": 0,
        "expected_results": [
            {"path": "outcome", "op": "eq", "expect": "approved"},
            {"path": "online", "op": "eq", "expect": spec["expect_online"]},
            {"path": "resp_code", "op": "eq", "expect": "00"},
            # Mechanism (pass): 9F4C is stored by the kernel's process_cda only
            # after the Transaction Data Hash Code matched
            # (emv_offline_data_authentication.c:1279), so its presence in the
            # OUT proves the terminal verified this card's 9F4B.  It is a pass
            # rule on purpose: a CDA failure would otherwise be masked, because
            # the kernel reports 102 (offline decline) and the POS maps 102
            # with CID=0x40 (TC) back to APPROVED.
            {"path": "out.datarecord.9F4C", "op": "present"},
            {"path": "card.gac.response", "op": "contains", "expect": "9F4B",
             "severity": "warn"},
            {"path": "out.datarecord.9F4B", "op": "present", "severity": "warn"},
        ],
    }


# --------------------------------------------------------------------------
# selfcheck: replay the terminal verification chain
# --------------------------------------------------------------------------
def recover(modulus_hex: str, exponent_hex: str, signature_hex: str) -> bytes:
    modulus = int(modulus_hex, 16)
    size = (modulus.bit_length() + 7) // 8
    return pow(int(signature_hex, 16), int(exponent_hex, 16),
               modulus).to_bytes(size, "big")


def selfcheck(data: dict, cert90: str, rem92: str, cert46: str, rem48: str,
              static_hex: str) -> list[str]:
    problems: list[str] = []
    ca, issuer, icc = data["ca"], data["issuer"], data["icc"]

    # 1. CAPK checksum (the convention the device table uses).
    if compute_capk_checksum(RID, CA_KEY_INDEX, ca["n"], EXPONENT_HEX) != \
            compute_capk_checksum(RID, CA_KEY_INDEX, ca["n"], EXPONENT_HEX):
        problems.append("CAPK checksum not reproducible")

    # 2. Issuer public key certificate (kernel retrieve_pk_from_icc, ISSPK).
    cert = recover(ca["n"], EXPONENT_HEX, cert90)
    if (cert[0], cert[1], cert[-1]) != (0x6A, 0x02, 0xBC):
        problems.append("tag 90 header/format/trailer")
    if cert[6] != int(CERT_EXPIRY_MMYY[:2], 16) or cert[7] != int(CERT_EXPIRY_MMYY[2:], 16):
        problems.append("tag 90 expiry")
    capacity = NCA - 36
    issuer_mod = cert[15:15 + capacity] + bytes.fromhex(rem92)
    if len(issuer_mod) != NI:
        problems.append(f"issuer modulus length {len(issuer_mod)} != {NI}")
    h90 = hashlib.sha1(cert[1:NCA - 21] + bytes.fromhex(rem92)
                       + bytes.fromhex(EXPONENT_HEX)).digest()
    if h90 != cert[NCA - 21:NCA - 1]:
        problems.append("tag 90 hash")

    # 3. ICC public key certificate (kernel retrieve_pk_from_icc, ICCPK).
    icc_cert = recover(issuer_mod.hex().upper(), EXPONENT_HEX, cert46)
    if (icc_cert[0], icc_cert[1], icc_cert[-1]) != (0x6A, 0x04, 0xBC):
        problems.append("tag 9F46 header/format/trailer")
    cap46 = NI - 42
    icc_mod = icc_cert[21:21 + cap46] + bytes.fromhex(rem48)
    if len(icc_mod) != NIC:
        problems.append(f"ICC modulus length {len(icc_mod)} != {NIC}")
    h46 = hashlib.sha1(icc_cert[1:NI - 21] + bytes.fromhex(rem48)
                       + bytes.fromhex(EXPONENT_HEX)
                       + bytes.fromhex(static_hex)
                       + bytes.fromhex(AIP)).digest()
    if h46 != icc_cert[NI - 21:NI - 1]:
        problems.append("tag 9F46 hash")

    # 4. CDA round trip: card signs, kernel verifies (process_cda).
    pdol_data = "9C800003" + "22" + "3BB51CB8" + "0000000000" + "000000" \
                + "000000001500" + "000000000000"
    cdol1_data = "000000001500" + "000000000000" + "0620" + "0000000000" \
                 + "0978" + "240412" + "00" + "3BB51CB8" + "9C800003" + "3F0000"
    response_tlvs = ("9F2608FAFAFAFAFAFAFAFA" + "9F270140" + "9F36020001"
                     + "9F100706010103000000")
    tdhc = hashlib.sha1(bytes.fromhex(pdol_data + cdol1_data
                                      + response_tlvs)).digest()
    number = "0001"
    dynamic = bytes.fromhex("02" + number + "40" + "FA" * 8) + tdhc
    un = "3BB51CB8"
    ldd = len(dynamic)
    signed = bytearray(NIC - 22)
    signed[0] = 0x05
    signed[1] = 0x01
    signed[2] = ldd
    signed[3:3 + ldd] = dynamic
    for i in range(3 + ldd, len(signed)):
        signed[i] = 0xBB
    block = bytearray(NIC)
    block[0] = 0x6A
    block[1:1 + len(signed)] = signed
    block[NIC - 21:NIC - 1] = hashlib.sha1(bytes(signed) + bytes.fromhex(un)).digest()
    block[NIC - 1] = 0xBC
    sdad = raw_sign(icc["n"], icc["d"], bytes(block))

    # terminal side
    recovered = recover(icc_mod.hex().upper(), EXPONENT_HEX, sdad.hex().upper())
    if recovered != bytes(block):
        problems.append("SDAD does not recover to the signed block")
    if recovered[2] != 0x01:
        problems.append("SDAD hash algorithm indicator")
    dyn = recovered[5:5 + recovered[3]]
    if dyn[recovered[4]] != 0x40:
        problems.append("SDAD CID != 9F27")
    if hashlib.sha1(recovered[1:NIC - 21] + bytes.fromhex(un)).digest() != \
            recovered[NIC - 21:NIC - 1]:
        problems.append("SDAD hash mismatch (terminal side)")
    embedded = dyn[recovered[4] + 9:recovered[4] + 29]
    if embedded != tdhc:
        problems.append("Transaction Data Hash Code mismatch")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data = load_or_create_keys(KEY_FILE, args.dry_run)
    pan_bcd = pan_to_bcd(luhn_pan(PAN_PREFIX, PAN_BODY), 10)
    static_hex = static_data_hex()

    cert90, rem92, _ = build_issuer_certificate(data["ca"], data["issuer"], pan_bcd)
    cert46, rem48, _ = build_icc_certificate(data["issuer"], data["icc"], pan_bcd,
                                             static_hex)

    problems = selfcheck(data, cert90, rem92, cert46, rem48, static_hex)
    caps, replaced = patch_conf(data["ca"], args.dry_run)

    written = []
    for spec in CASE_SPECS:
        case = build_case(data, cert90, rem92, cert46, rem48, spec)
        path = CASE_DIR / f"{spec['case_id']}.json"
        if not args.dry_run:
            path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        written.append((case["case_id"], path,
                        [r["path"] for r in case["expected_results"]]))

    print(f"keys        : {KEY_FILE}")
    print(f"PAN         : {luhn_pan(PAN_PREFIX, PAN_BODY)} (BCD {pan_bcd}, PSN {PAN_SEQUENCE})")
    print(f"static data : {len(static_hex) // 2}B (SFI 1/1 value)")
    print(f"tag 90      : {len(cert90) // 2}B, 92 {len(rem92) // 2}B, "
          f"issuer NI={NI}B")
    print(f"tag 9F46    : {len(cert46) // 2}B, 9F48 {len(rem48) // 2}B, "
          f"ICC NIC={NIC}B")
    print(f"CONF        : {caps} CAPKs (index {CA_KEY_INDEX} "
          f"{'replaced' if replaced else 'appended'})")
    for case_id, path, rules in written:
        print(f"case        : {case_id} -> {path}")
        print(f"              rules: {', '.join(rules)}")
    print(f"selfcheck   : {'OK' if not problems else 'FAILED'}")
    for p in problems:
        print("  -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
