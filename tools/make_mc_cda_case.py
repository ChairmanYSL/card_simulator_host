"""Generate the Mastercard CDA case ``3C05-9100-06`` from the reference log.

Reference: ``E:\\Work\\KSAPI\\ksapi-entrypoint-arch-prj\\Log\\3C05-9100-06.txt``
(KSAPI reader, manual 0.01 transaction, M-TIP card simulator PAN
5413339000001513).  The log ends in an **offline approval of a CDA card**:
the reader asks TC + CDA (GENERATE AC P1=0x50), the card answers CID=0x40 with
a 128-byte 9F4B SDAD, and the reader reports
``OPS=10F0F000B0F0FF00`` / ``resp_code=00`` with no error indication.

What the log gives us (used verbatim below):

  SELECT PPSE     61{4F=A0000000041010, 87=01}
  SELECT ADF      6F{84=A0000000041010, A5{50='MasterCard', 9F38=9F4005,
                   BF0C{9F4D=0B0A}}}
  GPO (PDOL 9F4005)  83 05 6000F00001 (Profile: PPS_perf_MC term 9F40)
  GPO response    77{82=1980, 94=08010100 10010101 18010100 20010200}
  SFI 2/1 record  the application record (57/5A/5F24/5F25/5F28/5F34/8C/8D/8E/
                  9F07/9F08/9F0D/9F0E/9F0F/9F42/9F51(DRDOL)/9F4A=82)
  SFI 3/1 record  8F=F3, 9F32=03, 92=36B, 90=128B (issuer PK certificate)
  SFI 4/1 record  9F48=26B, 9F47=03
  SFI 4/2 record  9F46=128B (ICC PK certificate)
  GENERATE AC     80 AE 50 00 42 <66B CDOL1 data> -> 77 81 AA{9F27=40,
                  9F36=7B39, 9F4B=128B(SDAD), 9F10=26B}
  OUT             DataRecord 9F02=000000000001 (0.01 EUR), 9F27=40,
                  9F36=7B39, 9F34=1F0302; DiscData 9F42=0978 +
                  DF8115=0000000000FF; UIRD=03000000130000000000000000

The reader's AID parameters and terminal parameters of that log are byte-identical
to ``PPS_perf_MC`` (Terminal Type 26 = attended/offline-only, TAC default
0400800000, TAC denial DC50000000, floor limit / CVM required limit /
Reader CTL = 9000.00, 9F40=6000F00001, DF8117=60), so the case pushes that
config.

The reference card's private keys are not available (its 8F names CA index F3,
whose modulus is 1152-bit in CTEL2 Appendix B while the log's 90 is 128 bytes),
so -- exactly like ``tools/make_amex_cda_case.py`` -- this tool generates its own
CA / issuer / ICC key chain, patches ``PPS_perf_MC`` with the CA public key under
index 01 (the index the profile's 8F then names) and builds the 90/92/9F32 and
9F46/9F47/9F48 certificates as EMV 4.4 Book 2 §5/§6.5 require.  ``selfcheck``
replays the terminal verification chain (and a synthetic CDA round trip) in
Python.

Differences from the reference log (all documented, none affects the outcome):

* the CA key is ours (index 01, 1024-bit) instead of the log's F3, so the
  certificate bytes and the SDAD differ from the log's;
* the profile cannot express the log's ``9F51`` (DRDOL) inside the SFI 2/1
  record nor ``9F4D`` (Log Entry) in the ADF FCI -- both are reported by
  ``report_log_diff()`` and left out.

Usage::

    python tools/make_mc_cda_case.py [--dry-run]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BRAND_DIR = REPO / "cases" / "mastercard"
CASE_DIR = BRAND_DIR / "contactless" / "cases"
CONF_FILE = BRAND_DIR / "pos_configs" / "PPS_perf_MC.json"
INDEX_FILE = BRAND_DIR / "pos_configs" / "index.json"
KEY_FILE = REPO / "tmp" / "cda_keys" / "mastercard_3c05_9100_06.json"

CASE_ID = "3C05-9100-06"
CONF_ID = "PPS_perf_MC"

# ---------------------------------------------------------------------------
# Brand / key chain constants
# ---------------------------------------------------------------------------
RID = "A000000004"
AID = "A0000000041010"
#: Index the generated CA key is provisioned under (the profile's 8F names it).
CA_KEY_INDEX = "01"
EXPONENT = 65537
EXPONENT_HEX = "010001"
NCA = NI = NIC = 128            # 1024-bit CA / issuer / ICC moduli (bytes)

CA_SERIAL = "000001"
ISSUER_SERIAL = "000101"
ICC_SERIAL = "000201"
CERT_EXPIRY_MMYY = "4912"       # 12/49, the card's own expiry era (5F24=491231)
ICC_KEY_ID = "cda-icc-mc-3c05"

# ---------------------------------------------------------------------------
# Card data of the reference log (3C05-9100-06), verbatim
# ---------------------------------------------------------------------------
PAN = "5413339000001513"                  # log 5A (Luhn-valid)
PAN_SEQUENCE = 1                          # log 5F34=01
EXPIRY = "491231"                         # log 5F24
EFFECTIVE = "060101"                      # log 5F25
ISSUER_COUNTRY = "0250"                   # log 5F28
#: log 57 = 5413339000001513D4912201000000000000 (BCD, '=' -> 'D')
TRACK2_ASCII = "5413339000001513=4912201000000000000"
AIP = "1980"                              # CDA (byte 1 bit 1) + CVM (bit 5)
#: 08 01 01 00 (dropped by the kernel, C-2 §3.3.2) + SFI 2/1 (ODA) + 3/1 + 4/1-2
AFL = "08010100100101011801010020010200"
PDOL = "9F4005"                           # requests Additional Terminal Cap. 9F40
CDOL1 = ("9F02069F03069F1A0295055F2A029A039C019F37049F35019F45029F4C08"
         "9F34039F21039F7C14")
CDOL2 = "910A8A0295059F37049F4C089F02069F0306"
#: log 8E = 0000000000000000 1F03 (one "no CVM required" rule, no third byte)
CVM_LIST = [{"cvmCode": 0x1F, "cvmCondition": 0x03, "applySucceedingCvm": 1}]
APPLICATION_USAGE_CONTROL = "FF00"        # log 9F07
APPLICATION_VERSION = "0002"              # log 9F08
IAC_DEFAULT = "F840642000"                # log 9F0D
IAC_DENIAL = "0010880000"                 # log 9F0E
IAC_ONLINE = "F86064F800"                 # log 9F0F
CURRENCY = "0978"                         # log 9F42 (EUR)
SDA_TAG_LIST = "82"                       # log 9F4A -> AIP is appended to the hash
APP_LABEL = "MasterCard"                  # log FCI 50

#: The log's SFI 2/1 record content with the ``70`` template stripped, verbatim.
LOG_SFI2_1 = ("57125413339000001513D4912201000000000000"
              "5A085413339000001513"
              "5F2403491231"
              "5F2503060101"
              "5F28020250"
              "5F340101"
              "8C279F02069F03069F1A0295055F2A029A039C019F37049F35019F45029F4C08"
              "9F34039F21039F7C14"
              "8D12910A8A0295059F37049F4C089F02069F0306"
              "8E0A00000000000000001F03"
              "9F0702FF00"
              "9F08020002"
              "9F0D05F840642000"
              "9F0E050010880000"
              "9F0F05F86064F800"
              "9F42020978"
              "9F51039F3704"
              "9F4A0182")

#: Tags of that record the card profile cannot express (dropped from the static
#: data and reported).  9F51 = DRDOL (Data Record DOL, KSAPI emv_tag.h).
UNSUPPORTED_RECORD_TAGS = ("9F51",)
#: Tag of the log's ADF FCI that the profile cannot express (PPSE can be raw,
#: the ADF FCI cannot): 9F4D = Log Entry.
UNSUPPORTED_FCI_TAGS = ("9F4D",)

#: Record layout of the generated profile (field names the card app maps to tags,
#: same vocabulary as the AMEX CDA cases).  Order == the log's record order.
APP_RECORD_FIELDS = [
    "track2", "pan", "expiryDate", "applicationEffectiveDate",
    "issuerCountryCode", "panSequenceNumber", "cdol1", "cdol2", "cvmList",
    "applicationUsageControl", "applicationVersionNumber", "iacDefault",
    "iacDenial", "iacOnline", "applicationCurrencyCode", "sdaTagList",
]
CERT_RECORD_FIELDS = [
    "caPublicKeyIndex", "issuerPublicKeyExponent", "issuerPublicKeyRemainder",
    "issuerPublicKeyCertificate",
]
ICC_REMAINDER_RECORD_FIELDS = ["iccPublicKeyRemainder", "iccPublicKeyExponent"]
ICC_CERT_RECORD_FIELDS = ["iccPublicKeyCertificate"]

CERT90_CAPACITY = NCA - 36      # issuer modulus head inside tag 90
CERT46_CAPACITY = NI - 42       # ICC modulus head inside tag 9F46

PAC_PATTERN = "BB"


class ImportError_(RuntimeError):
    """Bad source data or an unsafe destination."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def pan_to_bcd(pan: str, length: int) -> str:
    """BCD PAN padded with 'F' (EMV Book 2 issuer certificate field)."""
    digits = "".join(ch for ch in pan if ch.isdigit())
    if len(digits) % 2:
        digits += "F"
    return digits.upper().ljust(length * 2, "F")[:length * 2]


def pack_track2_ascii(track2_ascii: str) -> str:
    """Mirror the card app's track2 packing ('=' -> 'D', odd length -> 'F')."""
    packed = "".join("D" if ch == "=" else ch.upper() for ch in track2_ascii)
    if len(packed) % 2:
        packed += "F"
    return packed


def luhn_ok(pan: str) -> bool:
    total = 0
    for index, ch in enumerate(reversed(pan)):
        value = int(ch)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def raw_sign(modulus_hex: str, private_hex: str, block: bytes) -> bytes:
    """EMV recovery is the plain RSA primitive: signature = block^d mod n."""
    modulus = int(modulus_hex, 16)
    if int.from_bytes(block, "big") >= modulus:
        raise ImportError_("block >= modulus")
    return pow(int.from_bytes(block, "big"), int(private_hex, 16),
               modulus).to_bytes(len(block), "big")


def recover(modulus_hex: str, exponent_hex: str, signature_hex: str) -> bytes:
    modulus = int(modulus_hex, 16)
    size = (modulus.bit_length() + 7) // 8
    return pow(int(signature_hex, 16), int(exponent_hex, 16),
               modulus).to_bytes(size, "big")


def compute_capk_checksum(rid: str, key_index: str, modulus: str,
                          exponent: str) -> str:
    """DF03 = SHA1(RID || index || modulus || exponent) (host kernel_tlv rule)."""
    blob = bytes.fromhex(rid + key_index + modulus + exponent)
    return hashlib.sha1(blob).hexdigest().upper()


def tlv(tag: str, value_hex: str) -> str:
    raw_len = len(value_hex) // 2
    if raw_len < 0x80:
        return tag + f"{raw_len:02X}" + value_hex
    return tag + "81" + f"{raw_len:02X}" + value_hex


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
        "_comment": f"test-only CA/issuer/ICC key material for {CASE_ID} (CDA); "
                    "delete this file to regenerate the chain",
        "ca": make(),
        "issuer": make(),
        "icc": make(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


# ---------------------------------------------------------------------------
# certificates (EMV 4.4 Book 2 §5 / §6.4 and §6.5)
# ---------------------------------------------------------------------------

def build_issuer_certificate(ca: dict, issuer: dict,
                             pan_bcd: str) -> tuple[str, str, str]:
    """tag 90 + tag 92 (issuer PK remainder) + tag 9F32 (exponent).

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
    """tag 9F46 + tag 9F48 + tag 9F47 -- signed by the ISSUER private key.

    Book 2 §6.5 step 5: hash = recovered[1 .. NI-22] || ICC PK remainder ||
    ICC PK exponent || static data (§10.3 ODA records) || AIP (9F4A lists 82).
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


# ---------------------------------------------------------------------------
# the application record (SFI 2/1) -- also the ODA static data of the case
# ---------------------------------------------------------------------------

def build_record_value(include_unsupported: bool = False) -> str:
    """SFI 2/1 record content, built exactly like the log's record.

    ``include_unsupported`` appends the tags the card profile cannot express
    (only used for the log comparison in ``report_log_diff``).
    """
    pan_bcd = pan_to_bcd(PAN, 8)
    track2 = pack_track2_ascii(TRACK2_ASCII)
    rules = "".join(f"{int(r['cvmCode']):02X}{int(r['cvmCondition']):02X}"
                    for r in CVM_LIST)
    parts = [
        tlv("57", track2),
        tlv("5A", pan_bcd),
        tlv("5F24", EXPIRY),
        tlv("5F25", EFFECTIVE),
        tlv("5F28", ISSUER_COUNTRY),
        tlv("5F34", f"{PAN_SEQUENCE:02X}"),
        tlv("8C", CDOL1),
        tlv("8D", CDOL2),
        tlv("8E", "0000000000000000" + rules),
        tlv("9F07", APPLICATION_USAGE_CONTROL),
        tlv("9F08", APPLICATION_VERSION),
        tlv("9F0D", IAC_DEFAULT),
        tlv("9F0E", IAC_DENIAL),
        tlv("9F0F", IAC_ONLINE),
        tlv("9F42", CURRENCY),
    ]
    if include_unsupported:
        parts.append(tlv("9F51", "9F3704"))     # DRDOL, log only
    parts.append(tlv("9F4A", SDA_TAG_LIST))
    return "".join(parts)


def static_data_hex() -> str:
    """Book 3 §10.3 ODA record of this card = the SFI 2/1 record content."""
    return build_record_value()


def report_log_diff() -> list[str]:
    """Compare our built record with the log's verbatim record bytes."""
    notes = []
    built = build_record_value()
    built_with = build_record_value(include_unsupported=True)
    if built_with == LOG_SFI2_1:
        notes.append("SFI 2/1 record reproduces the log byte-for-byte when the "
                     f"unsupported tag(s) {'/'.join(UNSUPPORTED_RECORD_TAGS)} are "
                     "appended")
    else:
        notes.append("SFI 2/1 record differs from the log: "
                     f"built={built_with} log={LOG_SFI2_1}")
    if built == built_with:
        notes.append("no unsupported tag was dropped")
    else:
        stripped = tlv("9F51", "9F3704")
        notes.append(f"profile cannot express {'/'.join(UNSUPPORTED_RECORD_TAGS)} "
                     f"({len(stripped) // 2}B) -> dropped from the static data "
                     "(and therefore from the 9F46 hash)")
    notes.append("profile cannot express "
                 f"{'/'.join(UNSUPPORTED_FCI_TAGS)} (Log Entry) in the ADF FCI")
    notes.append(
        "AFL 首项 08 01 01 00 没有对应记录，案例库会给出 warn"
        "（AFL 指向未配置的记录 [(1, 1)]）：这是**有意的**——C-2 §3.3.2 规定"
        "内核在该项恰为 08 01 01 00 且 Kernel Configuration 的 MSD 位未置位时"
        "丢弃首项（emv_initiate_application.c:864；日志 kernel_config=0x20、"
        "本案例下发的 PPS_perf_MC 为 0x30，两者 0x80 位均为 0），日志的读记录"
        "序列（SFI 2/1 → 3/1 → 4/1 → 4/2）也证明了这一点；参考卡该项背后的"
        "记录内容日志从未读出，故不复现，也不臆造。")
    return notes


# ---------------------------------------------------------------------------
# CONFIG patching
# ---------------------------------------------------------------------------

def patch_conf(ca: dict, dry_run: bool) -> tuple[int, bool]:
    """Provision the generated CA public key as a CAPK of PPS_perf_MC."""
    checksum = compute_capk_checksum(RID, CA_KEY_INDEX, ca["n"], EXPONENT_HEX)
    entry = {
        "rid": RID,
        "key_index": CA_KEY_INDEX,
        "hash_alg": 1,
        "key_alg": 1,
        "modulus": ca["n"],
        "exponent": EXPONENT_HEX,
        "checksum": checksum,
        "expiry": "20491231",
        "expiry_encoding": "ascii8",
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
    return len(conf["caps"]), replaced


def patch_index(capk_count: int, replaced: bool, dry_run: bool) -> None:
    index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    note = (f"capk index {CA_KEY_INDEX} is a locally generated test CA key for "
            f"{CASE_ID} (CDA): the reference card names CTEL2 Appendix B index F3, "
            f"whose private key is not available")
    for item in index["configs"]:
        if item["id"] == CONF_ID:
            item["capk"] = capk_count
            item["note"] = note
    for key in ("capk",):
        if key in index and isinstance(index[key], dict):
            index[key]["notes"] = index[key].get("notes", [])
            if note not in index[key]["notes"]:
                index[key]["notes"].append(note)
    if not dry_run:
        INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    _ = replaced


# ---------------------------------------------------------------------------
# case JSON
# ---------------------------------------------------------------------------

def build_case(data: dict, cert90: str, rem92: str, cert46: str, rem48: str,
               log_notes: list[str]) -> dict:
    track2 = TRACK2_ASCII.encode("ascii").hex().upper()
    return {
        "case_id": CASE_ID,
        "title": "MC contactless CDA 卡在离线终端脱机批准（TC + CDA，0.01 EUR）",
        "summary": (
            "Mastercard 非接 L2 案例 3C05-9100-06：Kernel 2、离线专用终端"
            "（Terminal Type 26，配置 PPS_perf_MC），卡片支持 CDA（AIP=1980、"
            "AFL 含 ODA 记录、9F4A=82），金额 0.01 EUR。读写器应以 P1=0x50"
            "（TC + CDA 请求）发起 GENERATE AC，卡片应答 CID=0x40 + 9F4B(SDAD)，"
            "终端完成 ODA/CDA 验签后**脱机批准**（OPS Status=APPROVED、CVM="
            "NO_CVM、DataRecord 含 9F26/9F27=40/9F36、DiscData 的 DF8115 无错误"
            "指示）。日志中读写器内置参数（r-terpara + aid param）与 "
            "`PPS_perf_MC` 的 MC/00 行逐字节一致，故本案例下发该配置（其中 "
            "capk 索引 01 是**本案例本地生成**的测试 CA 公钥——参考卡的 8F 指向 "
            "文档 Appendix B 的 F3，其私钥不可得，无法复现其 9F46/9F4B）。"),
        "brand": "mastercard",
        "entry_mode": "contactless",
        "profile": {
            "id": CASE_ID,
            "name": f"Mastercard CDA（{CASE_ID}）",
            "description": "参考日志 3C05-9100-06 的卡片数据；ICC 私钥随案例下发，"
                           "证书链由 tools/make_mc_cda_case.py 生成",
            "network": "MASTERCARD",
            "isActive": True,
            "aids": [{"aid": AID, "label": APP_LABEL, "priority": 1}],
            "pan": PAN,
            "panSequenceNumber": PAN_SEQUENCE,
            "applicationExpirationDate": EXPIRY,
            "cardholderName": "MC TEST CARD",
            "track2EquivalentData": track2,
            "applicationLabel": APP_LABEL,
            "applicationVersionNumber": APPLICATION_VERSION,
            "aip": AIP,
            "afl": AFL,
            "issuerCountryCode": ISSUER_COUNTRY,
            "pdol": PDOL,
            "cdol1": CDOL1,
            "cdol2": CDOL2,
            "cvmList": CVM_LIST,
            "applicationUsageControl": APPLICATION_USAGE_CONTROL,
            "applicationCurrencyCode": CURRENCY,
            "applicationEffectiveDate": EFFECTIVE,
            "iacDefault": IAC_DEFAULT,
            "iacDenial": IAC_DENIAL,
            "iacOnline": IAC_ONLINE,
            "caPublicKeyIndex": CA_KEY_INDEX,
            "issuerPublicKeyCertificate": cert90,
            "issuerPublicKeyRemainder": rem92,
            "issuerPublicKeyExponent": EXPONENT_HEX,
            "iccPrivateKeyId": ICC_KEY_ID,
            "iccPublicKeyCertificate": cert46,
            "iccPublicKeyExponent": EXPONENT_HEX,
            "iccPublicKeyRemainder": rem48,
            "acBehavior": "offline_approved",
            "records": [
                {"sfi": 2, "record": 1, "fields": list(APP_RECORD_FIELDS)},
                {"sfi": 3, "record": 1, "fields": list(CERT_RECORD_FIELDS)},
                {"sfi": 4, "record": 1,
                 "fields": list(ICC_REMAINDER_RECORD_FIELDS)},
                {"sfi": 4, "record": 2, "fields": list(ICC_CERT_RECORD_FIELDS)},
            ],
        },
        "kernel_params_ref": CONF_ID,
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
                "amount": "0.01",
                "currency": "EUR",
                "entry_mode": "contactless",
                "pin_hint": "none",
                "notes": "在 POS 上发起 0.01 EUR 非接交易并拍卡；期望脱机批准"
                         "（不弹 CVM、不需联机）",
            },
            "expected_values": {
                "amount": "0.01",
                "currency": "978",
                "entry_mode": "contactless",
            },
            "act": [
                {"id": "9F02", "value": "000000000001"},
                {"id": "9F03", "value": "000000000000"},
                {"id": "5F2A", "value": CURRENCY},
                {"id": "5F36", "value": "02"},
                {"id": "9C", "value": "00"},
            ],
        },
        "preconditions": [
            "手机 NFC 已开启，模拟 app 保持前台",
            f"POS 下发 {CONF_ID}（与日志读写器内置参数最接近：逐字节比对后，24 个"
            "配置里只有它同时对上 9F35=26、DF11=0400800000、DF13=DC50000000、"
            "DF19=DF21=DF8124=DF8125=000000900000、9F40=6000F00001、DF8117=60；"
            "仍有 2 处差异 —— DF811B=30（日志 20，只多置 Kernel Configuration "
            "bit5，本案例 CDA 必读全部记录，无行为差异）与 DF8133=01C2（日志为"
            "空，属文档 Table 17 的 RRP 宽限时间））",
            "卡片 AIP=1980（b1=CDA 支持、b5=CVM 支持），9F4A=82 → ICC 证书哈希"
            "追加 AIP；AFL 首项 08 01 01 00 被内核丢弃（C-2 §3.3.2，"
            "emv_initiate_application.c:864），实际读 SFI 2/1（应用记录，ODA 记录）"
            "→ 3/1（发卡行公钥证书）→ 4/1（ICC 余数/指数）→ 4/2（ICC 证书）",
            f"配置文件里 capk 索引 {CA_KEY_INDEX}（RID {RID}）是本案例本地生成的"
            "测试 CA 公钥（tools/make_mc_cda_case.py），参考卡的 F3 私钥不可得；"
            "profile 的 8F 指向该索引",
            "ICC 私钥随案例 keys.rsa_keys 下发（id=" + ICC_KEY_ID + "），"
            "卡端用它签 9F4B(SDAD)；证书链按 EMV Book 2 §5/§6.5 构造并在生成器中"
            "自检（终端验签链 + CDA 往返）",
            "与参考日志的差异（不影响判定）：CA 密钥是我们生成的（证书/SDAD 字节"
            "与日志不同）；profile 无法表达记录里的 9F51(DRDOL) 与 FCI 里的 "
            "9F4D(Log Entry)；交易日期/时间由 POS 取当前值（日志为 2026-03-04）",
        ],
        "test_plan": [
            {"step": 1,
             "action": "主机下发卡片档案（AIP 1980 + CDA 证书链 + ICC 私钥）与 "
                       f"CONFIG({CONF_ID})，启动模拟",
             "expect": "安卓上报 emulating"},
            {"step": 2,
             "action": "操作员在 POS 上发起 0.01 EUR 非接交易并拍卡",
             "expect": "POS 按 PDOL(9F4005) 组 GPO（9F40=6000F00001），读 "
                       "SFI 2/1、3/1、4/1、4/2，恢复发卡行/ICC 公钥，并以 "
                       "P1=0x50（TC + CDA）发起 GENERATE AC"},
            {"step": 3,
             "action": "主机判定卡端 GENERATE AC 应答",
             "expect": "应答含 9F27=40(CID=TC)、9F36(ATC)、9F4B(SDAD, 128B)"},
            {"step": 4,
             "action": "主机判定 POS 的 OUT",
             "expect": "outcome=approved / online=offline / resp_code=00、"
                       "OPS Status=APPROVED、CVM=NO_CVM、9F27=40、9F36 present；"
                       "warn 级再核对 9F34=1F0302、DiscData 无错误指示、"
                       "9F4C（内核完成 CDA 校验后才存的 ICC Dynamic Number）"},
        ],
        "timeout_seconds": 180,
        "retry_on_fail": 0,
        "expected_results": [
            {"path": "outcome", "op": "eq", "expect": "approved"},
            {"path": "online", "op": "eq", "expect": "offline"},
            {"path": "resp_code", "op": "eq", "expect": "00"},
            {"path": "out.ops.status", "op": "eq", "expect": "APPROVED"},
            {"path": "out.ops.cvm", "op": "eq", "expect": "NO_CVM"},
            {"path": "out.datarecord.9F27", "op": "eq", "expect": "40"},
            {"path": "out.datarecord.9F36", "op": "present"},
            # warn 级：日志里这四项都成立，但依赖 POS 上报的 DiscData /
            # DataRecord 白名单，缺一项只降为 WARN，不掩盖 FAIL。
            {"path": "out.datarecord.9F34", "op": "eq", "expect": "1F0302",
             "severity": "warn"},
            {"path": "out.discdata.DF8115", "op": "eq", "expect": "0000000000FF",
             "severity": "warn"},
            # 内核只在 CDA 的 Transaction Data Hash Code 校验通过后才保存 9F4C
            # （ICC Dynamic Number），故它是"CDA 真的验过"的旁证。
            {"path": "out.datarecord.9F4C", "op": "present", "severity": "warn"},
            {"path": "card.gac.response", "op": "contains", "expect": "9F4B",
             "severity": "warn"},
        ],
        "notes": log_notes,
    }


# ---------------------------------------------------------------------------
# selfcheck: replay the terminal verification chain
# ---------------------------------------------------------------------------

def selfcheck(data: dict, cert90: str, rem92: str, cert46: str, rem48: str,
              static_hex: str) -> list[str]:
    problems: list[str] = []
    ca, issuer, icc = data["ca"], data["issuer"], data["icc"]

    if compute_capk_checksum(RID, CA_KEY_INDEX, ca["n"], EXPONENT_HEX) != \
            compute_capk_checksum(RID, CA_KEY_INDEX, ca["n"], EXPONENT_HEX):
        problems.append("CAPK checksum not reproducible")

    # 1. issuer public key certificate (terminal: retrieve_pk_from_icc / ISSPK)
    cert = recover(ca["n"], EXPONENT_HEX, cert90)
    if (cert[0], cert[1], cert[-1]) != (0x6A, 0x02, 0xBC):
        problems.append("tag 90 header/format/trailer")
    if bytes(cert[2:6]) != bytes.fromhex(pan_to_bcd(PAN, 10))[:4]:
        problems.append("tag 90 issuer identifier")
    if bytes(cert[6:8]) != bytes.fromhex(CERT_EXPIRY_MMYY):
        problems.append("tag 90 expiry")
    issuer_mod = cert[15:15 + CERT90_CAPACITY] + bytes.fromhex(rem92)
    if len(issuer_mod) != NI:
        problems.append(f"issuer modulus length {len(issuer_mod)} != {NI}")
    h90 = hashlib.sha1(cert[1:NCA - 21] + bytes.fromhex(rem92)
                       + bytes.fromhex(EXPONENT_HEX)).digest()
    if h90 != cert[NCA - 21:NCA - 1]:
        problems.append("tag 90 hash")

    # 2. ICC public key certificate (terminal: retrieve_pk_from_icc / ICCPK)
    icc_cert = recover(issuer_mod.hex().upper(), EXPONENT_HEX, cert46)
    if (icc_cert[0], icc_cert[1], icc_cert[-1]) != (0x6A, 0x04, 0xBC):
        problems.append("tag 9F46 header/format/trailer")
    if bytes(icc_cert[2:12]) != bytes.fromhex(pan_to_bcd(PAN, 10))[:10]:
        problems.append("tag 9F46 application PAN")
    icc_mod = icc_cert[21:21 + CERT46_CAPACITY] + bytes.fromhex(rem48)
    if len(icc_mod) != NIC:
        problems.append(f"ICC modulus length {len(icc_mod)} != {NIC}")
    h46 = hashlib.sha1(icc_cert[1:NI - 21] + bytes.fromhex(rem48)
                       + bytes.fromhex(EXPONENT_HEX)
                       + bytes.fromhex(static_hex)
                       + bytes.fromhex(AIP)).digest()
    if h46 != icc_cert[NI - 21:NI - 1]:
        problems.append("tag 9F46 hash")

    # 3. CDA round trip: card signs, terminal recovers (Book 2 §6.6 with the
    #    Kernel 2 ICC Dynamic Data layout of C-2 Table 6.4):
    #    02 (length of ICC Dynamic Number) || number || CID || AC || TDHC
    pdol_data = "6000F00001"                     # GPO PDOL data (83 05 <9F40>)
    cdol1_data = ("000000000001" "000000000000" "0056" "8000000001" "0978"
                  "260304" "00" "009016FD" "26" "0000" "0000000000000000"
                  "1F0302" "000000" + "00" * 20)
    response_tlvs = ("9F270140" "9F36027B39" "9F101A" + "00" * 26)
    tdhc = hashlib.sha1(bytes.fromhex(pdol_data + cdol1_data
                                      + response_tlvs)).digest()
    ac = "A1A2A3A4A5A6A7A8"
    dynamic = bytes.fromhex("02" "0001" "40" + ac) + tdhc
    un = "009016FD"
    ldd = len(dynamic)
    signed = bytearray(NIC - 22)
    signed[0] = 0x05
    signed[1] = 0x01
    signed[2] = ldd
    signed[3:3 + ldd] = dynamic
    for i in range(3 + ldd, len(signed)):
        signed[i] = int(PAC_PATTERN, 16)
    block = bytearray(NIC)
    block[0] = 0x6A
    block[1:1 + len(signed)] = signed
    block[NIC - 21:NIC - 1] = hashlib.sha1(bytes(signed)
                                           + bytes.fromhex(un)).digest()
    block[NIC - 1] = 0xBC
    sdad = raw_sign(icc["n"], icc["d"], bytes(block))

    recovered = recover(icc_mod.hex().upper(), EXPONENT_HEX, sdad.hex().upper())
    if recovered != bytes(block):
        problems.append("SDAD does not recover to the signed block")
    if recovered[2] != 0x01:
        problems.append("SDAD hash algorithm indicator")
    got_len = recovered[3]
    got_dynamic = bytes(recovered[4:4 + got_len])
    if got_dynamic != dynamic:
        problems.append("SDAD ICC Dynamic Data mismatch")
    elif got_dynamic[3] != 0x40:
        problems.append("SDAD CID != 0x40")
    if hashlib.sha1(recovered[1:NIC - 21] + bytes.fromhex(un)).digest() != \
            recovered[NIC - 21:NIC - 1]:
        problems.append("SDAD hash mismatch (terminal side)")
    if bytes(recovered[4 + got_len - 20:4 + got_len]) != tdhc:
        problems.append("Transaction Data Hash Code mismatch")
    return problems


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not CONF_FILE.is_file():
        print(f"error: {CONF_FILE} not found", file=sys.stderr)
        return 2
    if not AID.startswith(RID) or not luhn_ok(PAN):
        print("error: AID/RID or PAN inconsistent", file=sys.stderr)
        return 2

    data = load_or_create_keys(KEY_FILE, args.dry_run)
    pan_bcd = pan_to_bcd(PAN, 10)
    static_hex = static_data_hex()

    cert90, rem92, _ = build_issuer_certificate(data["ca"], data["issuer"], pan_bcd)
    cert46, rem48, _ = build_icc_certificate(data["issuer"], data["icc"],
                                             pan_bcd, static_hex)

    problems = selfcheck(data, cert90, rem92, cert46, rem48, static_hex)
    log_notes = report_log_diff()
    caps, replaced = patch_conf(data["ca"], args.dry_run)
    patch_index(caps, replaced, args.dry_run)

    case = build_case(data, cert90, rem92, cert46, rem48, log_notes)
    case_path = CASE_DIR / f"{CASE_ID}.json"
    if not args.dry_run:
        case_path.parent.mkdir(parents=True, exist_ok=True)
        case_path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")

    print(f"keys        : {KEY_FILE}")
    print(f"PAN         : {PAN} (BCD {pan_bcd}, PSN {PAN_SEQUENCE:02X}, "
          f"track2 {pack_track2_ascii(TRACK2_ASCII)})")
    print(f"static data : {len(static_hex) // 2}B (SFI 2/1 record, ODA record)")
    print(f"tag 90      : {len(cert90) // 2}B, 92 {len(rem92) // 2}B, "
          f"issuer NI={NI}B")
    print(f"tag 9F46    : {len(cert46) // 2}B, 9F48 {len(rem48) // 2}B, "
          f"ICC NIC={NIC}B")
    print(f"CONF        : {CONF_ID} now {caps} CAPKs (index {CA_KEY_INDEX} "
          f"{'replaced' if replaced else 'appended'})")
    print(f"case        : {CASE_ID} -> {case_path}")
    print(f"              rules: "
          f"{', '.join(r['path'] for r in case['expected_results'])}")
    for note in log_notes:
        print(f"log diff    : {note}")
    print(f"selfcheck   : {'OK' if not problems else 'FAILED'}")
    for problem in problems:
        print("  -", problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
