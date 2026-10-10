"""Import the Mastercard CTEL2 reader configuration data set as this host's
named POS config library (``cases/mastercard/pos_configs/``).

Source split (rulings 2026-09-25)
--------------------------------
* **Value authority: Mastercard Contactless Testing Environment (CTEL2)
  Version 4.1 Level 2 Reader Testing**, 2025-06-17 -- Table 15/16 (default
  configuration data), Table 17/18 (test configuration deltas), Appendix B
  (CA Public Keys).  Every correction this tool applies to the reference data
  cites the document page it comes from, and every correction is listed in the
  generated ``index.json`` (``import_report``) so the delivered values can be
  audited back to the document.
* **Structure template: the reference library's runtime artifact**
  ``<ref-dir>/Config/<id>.json`` (``AIDParam`` rows keyed by
  ``9F06`` x ``9C`` x ``9F2A`` Kernel Identifier, plus ``TermParam``).  This is
  what the reference host actually loads; the sibling ``AID/*.xml`` +
  ``SimData/*.xml`` pair is an older snapshot.  Rows that exist only in the XML
  (notably ``Maestro x Refund('20')`` in several configs) are merged back in, so
  every AID keeps the transaction type coverage Table 15/16 requires -- except
  for ``PPS_Select1``/``PPS_SPI_Select``, where the document's own application
  selection matrix (Tables 19-22) governs the row set.

Deliberate absences (also recorded in the generated ``index.json``/``README.md``)

* no ``drl`` -- ruling: a Mastercard CONF carries no DRL (CTEL2 Table 13 is
  therefore not imported);
* no ``exception_file`` -- CTEL2 defines none;
* no DEDS (Data Exchange) family and no DEDS-only data objects;
* the CAPK set is exactly Appendix B (12 keys).  The wider Mastercard ICS list
  prints further indexes for the same RIDs; they are not part of this document.
  ``PPS_perf_MC`` additionally carries **one locally generated test CA key**
  (index 01) once ``tools/make_mc_cda_case.py`` has run: case ``3C05-9100-06``
  needs a CDA chain whose private keys we own -- re-run that tool after this one
  if ``PPS_perf_MC.json`` is regenerated here (this importer writes the
  document-pure set back).

Usage::

    python tools/import_mastercard_ctel2.py ^
        --ref-dir "E:/Work/VS_Project/MastercardHost/Config" ^
        --out-dir cases/mastercard/pos_configs --brand mastercard --force

The host model is authoritative for the JSON shape
(``backend/emv_host/cases/models.py``: ``KernelParams``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

#: Revision stamped on every generated file (host-side metadata only).
VERSION = "ctel2-4.1"

# ---------------------------------------------------------------------------
# AIDs and transaction types
# ---------------------------------------------------------------------------

AID_MASTERCARD = "A0000000041010"   # CNF-02, PAGE 30
AID_MAESTRO = "A0000000043060"
AID_TEST = "B012345678"             # RID-length "Test" AID
AID_CIRRUS = "A0000000046000"       # only required where the document names it

#: CNF-03 (PAGE 31): 00 payment, 01 cash, 09 cashback, 17 cash disbursement,
#: 20 refund; 93 (contactless ATM) appears only in PPS_MC_ATM, 21/02 only in
#: PPS_TrxTypeOther.
TXN_TYPES = ("00", "01", "09", "17", "20")

#: Canonical order of ``aids[].fields``: 9C first, then the kernel parameters.
AID_FIELD_ORDER = (
    "9C", "9F2A",
    "9F09", "9F1B", "9F1D", "9F35",
    "DF11", "DF12", "DF13",
    "DF19", "DF20", "DF21",
    "DF32", "DF33", "DF34", "DF35", "DF36",
    "DF811B", "DF811E", "DF812C",
    "DF8118", "DF8119", "DF811F",
    "DF8124", "DF8125", "DF8136",
    "9F7E", "9F40", "9F33",
)

#: Canonical order of the global ``term`` TLV list.
TERM_ORDER = (
    "9F01", "9F1E", "9F15", "9F16", "9F4E", "9F1A", "9F1C", "9F7C",
    "5F2A", "5F36", "9F66", "9F53",
    "DF811A", "DF810C", "9F6D",
    "DF8117", "DF8118", "DF8119", "DF811C", "DF811D", "DF811F",
    "DF812D", "DF8132", "DF8133", "DF8134", "DF8135", "DF8136",
    "9F33", "9F35", "9F40", "9F05",
    "DF8308", "DF8309", "DF830A",
)

#: DEDS-only objects (ruling): never imported in either position.
DEDS_TAGS = {
    "DF8110", "DF8112", "FF8102", "FF8103", "DF8127",
    "DF8108", "DF60", "DF8109", "DF62", "DF810A", "DF63", "DF810D",
}

#: Reference-private convention for the document's proprietary-tag statements
#: ("tag X present with zero length" / "tag X must not be present" / "tag X
#: present with value V").  The document names none of these tags; they are the
#: reference host's own mechanism and are kept verbatim.
PROPRIETARY_ACTIONS = {
    "DF8308": "tags to add with zero length (reference-private)",
    "DF8309": "tags to remove from the kernel TLV database (reference-private)",
    "DF830A": "complete TLV(s) to add to the kernel TLV database (reference-private)",
}

#: ``row`` is ``[aid, txn, 9F2A]``; ``9F2A`` is the Kernel Identifier-Terminal
#: (02 = Kernel 2, 08 = Kernel 8).
Row = dict  # {"aid": str, "txn": str, "kernel": str, "values": dict[str, str]}

_WILDCARD = "*"

#: TermParam entries the reference stores as ASCII text (the XML's ``an``/
#: ``ans`` formats).  Everything else in TermParam is already hex.
ASCII_TERM_TAGS = {"9F1E", "9F16", "9F4E", "9F1C"}


def amount(value: str) -> str:
    """CTEL2 amounts are n12 (amount x 100, currency exponent 2).

    The document writes them both ways: ``"100"`` (major units -> 100.00) and
    ``"51.50"`` (already two decimals).  Both land on the same 12-digit
    numeric: ``100`` -> ``000000010000``, ``51.50`` -> ``000000005150``.
    """
    text = value.strip()
    if "." in text:
        whole, frac = text.split(".", 1)
        frac = (frac + "00")[:2]
    else:
        whole, frac = text, "00"
    digits = (whole or "0") + frac
    if not digits.isdigit():
        raise ImportError_(f"bad amount {value!r}")
    return digits.rjust(12, "0")


# ---------------------------------------------------------------------------
# Appendix B -- CA Public Keys (CTEL2 PAGE 66-71)
#
# Extracted from the document; ``verify_capk`` re-checks every entry with the
# formula the appendix prints: DF03 == SHA1(9F06 || 9F22 || DF02 || DF04).
# The appendix also fixes DF06 = 01 and DF07 = 01 for every key.
# ---------------------------------------------------------------------------

CAPK_HASH_ALG = 1
CAPK_KEY_ALG = 1
#: DF05 expiry: Appendix B prints none, so the ruling supplies 20491231
#: (the host writes it ascii8, see protocol/kernel_tlv.py).
CAPK_EXPIRY = "20491231"

CAPK_APPENDIX_B: list[dict] = [
    {"rid": "A000000004", "key_index": "00",
     "modulus": "9C6BE5ADB10B4BE3DCE2099B4B210672B89656EBA091204F613ECC623BEDC9C6D77B660E8BAEEA7F7CE30F1B153879A4E36459343D1FE47ACDBD41FCD710030C2BA1D9461597982C6E1BDD08554B726F5EFF7913CE59E79E357295C321E26D0B8BE270A9442345C753E2AA2ACFC9D30850602FE6CAC00C6DDF6B8D9D9B4879B2826B042A07F0E5AE526A3D3C4D22C72B9EAA52EED8893866F866387AC05A1399",
     "exponent": "03", "checksum": "EC0A59D35D19F031E9E8CBEC56DB80E22B1DE130"},
    {"rid": "A000000004", "key_index": "EF",
     "modulus": "A191CB87473F29349B5D60A88B3EAEE0973AA6F1A082F358D849FDDFF9C091F899EDA9792CAF09EF28F5D22404B88A2293EEBBC1949C43BEA4D60CFD879A1539544E09E0F09F60F065B2BF2A13ECC705F3D468B9D33AE77AD9D3F19CA40F23DCF5EB7C04DC8F69EBA565B1EBCB4686CD274785530FF6F6E9EE43AA43FDB02CE00DAEC15C7B8FD6A9B394BABA419D3F6DC85E16569BE8E76989688EFEA2DF22FF7D35C043338DEAA982A02B866DE5328519EBBCD6F03CDD686673847F84DB651AB86C28CF1462562C577B853564A290C8556D818531268D25CC98A4CC6A0BDFFFDA2DCCA3A94C998559E307FDDF915006D9A987B07DDAEB3B",
     "exponent": "03", "checksum": "21766EBB0EE122AFB65D7845B73DB46BAB65427A"},
    {"rid": "A000000004", "key_index": "F1",
     "modulus": "A0DCF4BDE19C3546B4B6F0414D174DDE294AABBB828C5A834D73AAE27C99B0B053A90278007239B6459FF0BBCD7B4B9C6C50AC02CE91368DA1BD21AAEADBC65347337D89B68F5C99A09D05BE02DD1F8C5BA20E2F13FB2A27C41D3F85CAD5CF6668E75851EC66EDBF98851FD4E42C44C1D59F5984703B27D5B9F21B8FA0D93279FBBF69E090642909C9EA27F898959541AA6757F5F624104F6E1D3A9532F2A6E51515AEAD1B43B3D7835088A2FAFA7BE7",
     "exponent": "03", "checksum": "D8E68DA167AB5A85D8C3D55ECB9B0517A1A5B4BB"},
    {"rid": "A000000004", "key_index": "F3",
     "modulus": "98F0C770F23864C2E766DF02D1E833DFF4FFE92D696E1642F0A88C5694C6479D16DB1537BFE29E4FDC6E6E8AFD1B0EB7EA0124723C333179BF19E93F10658B2F776E829E87DAEDA9C94A8B3382199A350C077977C97AFF08FD11310AC950A72C3CA5002EF513FCCC286E646E3C5387535D509514B3B326E1234F9CB48C36DDD44B416D23654034A66F403BA511C5EFA3",
     "exponent": "03", "checksum": "A69AC7603DAF566E972DEDC2CB433E07E8B01A9A"},
    {"rid": "A000000004", "key_index": "F4",
     "modulus": "9E2F74BF4AB521019735BFC7E4CBC56B6F64AFF1ED7B79998EE5B3DFFE23DFC8E2DD0025575AF94DE814264528AF6F8005A538B3D6AE881B350F89595588E51F7423E711109DEC169FDD560602D80EF46E582C8C546C8930394BD534412A88CC9FF4DFC08AE716A595EF1AF7C32EDFCF996433EB3C36BCE093E44E0BDE228E0299A0E358BF28308DB4739815DD09F1E89654CC7CC193E2AC17C4DA335D904B8EC06ACFBDE083F76933C969672E9AFEA3",
     "exponent": "03", "checksum": "BF6B5B9C47134E494571732A4903C935874682B9"},
    {"rid": "A000000004", "key_index": "F5",
     "modulus": "A6E6FB72179506F860CCCA8C27F99CECD94C7D4F3191D303BBEE37481C7AA15F233BA755E9E4376345A9A67E7994BDC1C680BB3522D8C93EB0CCC91AD31AD450DA30D337662D19AC03E2B4EF5F6EC18282D491E19767D7B24542DFDEFF6F62185503532069BBB369E3BB9FB19AC6F1C30B97D249EEE764E0BAC97F25C873D973953E5153A42064BBFABFD06A4BB486860BF6637406C9FC36813A4A75F75C31CCA9F69F8DE59ADECEF6BDE7E07800FCBE035D3176AF8473E23E9AA3DFEE221196D1148302677C720CFE2544A03DB553E7F1B8427BA1CC72B0F29B12DFEF4C081D076D353E71880AADFF386352AF0AB7B28ED49E1E672D11F9",
     "exponent": "010001", "checksum": "C2239804C8098170BE52D6D5D4159E81CE8466BF"},
    {"rid": "A000000004", "key_index": "F6",
     "modulus": "A25A6BD783A5EF6B8FB6F83055C260F5F99EA16678F3B9053E0F6498E82C3F5D1E8C38F13588017E2B12B3D8FF6F50167F46442910729E9E4D1B3739E5067C0AC7A1F4487E35F675BC16E233315165CB142BFDB25E301A632A54A3371EBAB6572DEEBAF370F337F057EE73B4AE46D1A8BC4DA853EC3CC12C8CBC2DA18322D68530C70B22BDAC351DD36068AE321E11ABF264F4D3569BB71214545005558DE26083C735DB776368172FE8C2F5C85E8B5B890CC682911D2DE71FA626B8817FCCC08922B703869F3BAEAC1459D77CD85376BC36182F4238314D6C4212FBDD7F23D3",
     "exponent": "03", "checksum": "502909ED545E3C8DBD00EA582D0617FEE9F6F684"},
    {"rid": "A000000004", "key_index": "F9",
     "modulus": "A99A6D3E071889ED9E3A0C391C69B0B804FC160B2B4BDD570C92DD5A0F45F53E8621F7C96C40224266735E1EE1B3C06238AE35046320FD8E81F8CEB3F8B4C97B940930A3AC5E790086DAD41A6A4F5117BA1CE2438A51AC053EB002AED866D2C458FD73359021A12029A0C043045C11664FE0219EC63C10BF2155BB2784609A106421D45163799738C1C30909BB6C6FE52BBB76397B9740CE064A613FF8411185F08842A423EAD20EDFFBFF1CD6C3FE0C9821479199C26D8572CC8AFFF087A9C3",
     "exponent": "03", "checksum": "336712DCC28554809C6AA9B02358DE6F755164DB"},
    {"rid": "B012345678", "key_index": "00",
     "modulus": "9C6BE5ADB10B4BE3DCE2099B4B210672B89656EBA091204F613ECC623BEDC9C6D77B660E8BAEEA7F7CE30F1B153879A4E36459343D1FE47ACDBD41FCD710030C2BA1D9461597982C6E1BDD08554B726F5EFF7913CE59E79E357295C321E26D0B8BE270A9442345C753E2AA2ACFC9D30850602FE6CAC00C6DDF6B8D9D9B4879B2826B042A07F0E5AE526A3D3C4D22C72B9EAA52EED8893866F866387AC05A1399",
     "exponent": "03", "checksum": "5D2970E64675727E60460765A8DB75342AE14783"},
    {"rid": "B012345678", "key_index": "02",
     "modulus": "A99A6D3E071889ED9E3A0C391C69B0B804FC160B2B4BDD570C92DD5A0F45F53E8621F7C96C40224266735E1EE1B3C06238AE35046320FD8E81F8CEB3F8B4C97B940930A3AC5E790086DAD41A6A4F5117BA1CE2438A51AC053EB002AED866D2C458FD73359021A12029A0C043045C11664FE0219EC63C10BF2155BB2784609A106421D45163799738C1C30909BB6C6FE52BBB76397B9740CE064A613FF8411185F08842A423EAD20EDFFBFF1CD6C3FE0C9821479199C26D8572CC8AFFF087A9C3",
     "exponent": "03", "checksum": "294BE20239AB15245A63BEA46CC6C175A25562D1"},
    {"rid": "B012345678", "key_index": "F1",
     "modulus": "A0DCF4BDE19C3546B4B6F0414D174DDE294AABBB828C5A834D73AAE27C99B0B053A90278007239B6459FF0BBCD7B4B9C6C50AC02CE91368DA1BD21AAEADBC65347337D89B68F5C99A09D05BE02DD1F8C5BA20E2F13FB2A27C41D3F85CAD5CF6668E75851EC66EDBF98851FD4E42C44C1D59F5984703B27D5B9F21B8FA0D93279FBBF69E090642909C9EA27F898959541AA6757F5F624104F6E1D3A9532F2A6E51515AEAD1B43B3D7835088A2FAFA7BE7",
     "exponent": "03", "checksum": "FDEE82458A34C0DE4AC2AFC98B2774E7383B062F"},
    {"rid": "B012345678", "key_index": "F3",
     "modulus": "94EA62F6D58320E354C022ADDCF0559D8CF206CD92E869564905CE21D720F971B7AEA374830EBE1757115A85E088D41C6B77CF5EC821F30B1D890417BF2FA31E5908DED5FA677F8C7B184AD09028FDDE96B6A6109850AA800175EABCDBBB684A96C2EB6379DFEA08D32FE2331FE103233AD58DCDB1E6E077CB9F24EAEC5C25AF",
     "exponent": "010001", "checksum": "5694B0D278481814A05E12B558CEC1234865AA5D"},
]

APPENDIX_B_OMITS = ("A000000004:05", "A000000004:F7", "A000000004:F8",
                    "A000000004:FA", "B012345678:05", "B012345678:F7",
                    "B012345678:F8")

# ---------------------------------------------------------------------------
# Document corrections
#
# ``RULES`` entries are ``(tag, aid, txn, value)`` where ``aid``/``txn`` accept
# "*" as a wildcard and ``value=None`` means "remove the object".  Rules are
# applied in list order, so specific rules follow wildcard ones.
# ---------------------------------------------------------------------------

#: Table 16 "Default configuration data - EMV" (PAGE 38-40) plus the Table 14
#: transaction-related defaults (PAGE 35).  Card Data Input Capability is
#: terminal-level in this library, so it is set in the term rules.
BASE_EMV_ROWS = [
    ("DF811B", "*", "*", "20"),          # Kernel Configuration (EMV mode)
    ("DF811B", AID_MAESTRO, "*", "A0"),  # Maestro, MagStripe implemented
    ("DF811E", "*", "*", "10"),          # Mag-stripe CVM Capability - CVM Required
    ("DF811E", AID_MASTERCARD, "17", "20"),
    ("DF811E", AID_MAESTRO, "17", "F0"),
    ("DF812C", "*", "*", "00"),          # Mag-stripe CVM Capability - No CVM Req.
    ("DF812C", AID_MAESTRO, "17", "F0"),
    ("9F7E", "*", "*", None),            # Mobile Support Indicator: not present
    ("9F1D", "*", "*", None),            # Terminal Risk Management: only below
    ("9F1D", AID_MASTERCARD, "00", "6CFF000000000000"),
    ("9F1D", AID_MASTERCARD, "93", "6CFF000000000000"),
    ("9F1D", AID_MAESTRO, "*", "44FF800000000000"),
    ("9F09", "*", "*", "0002"),          # Application Version Number (Reader)
    ("9F40", "*", "*", "0000000000"),    # Additional Terminal Capabilities
    ("DF8118", "*", "*", "60"),          # CVM Capability - CVM Required
    ("DF8119", "*", "*", "08"),          # CVM Capability - No CVM Required
    ("DF811F", "*", "*", "08"),          # Security Capability (CDA)
    ("DF11", "*", "*", "0000000000"),    # Terminal Action Code Default
    ("DF12", "*", "*", "0000000000"),    # Terminal Action Code Online
    ("DF13", "*", "*", "0000000000"),    # Terminal Action Code Denial
    ("DF19", "*", "*", amount("100")),   # Reader Contactless Floor Limit 100.00
    ("DF8124", "*", "*", amount("300")),  # Reader CTL (No On-device CVM)
    ("DF8125", "*", "*", amount("500")),  # Reader CTL (On-device CVM)
    ("9F35", "*", "*", "22"),            # Terminal Type (00/09/20/93)
    ("9F35", "*", "01", "14"),
    ("9F35", "*", "17", "11"),
    # Reader CVM Required Limit: wildcards first, then the 00/93 exceptions
    ("DF21", AID_MASTERCARD, "*", amount("200")),
    ("DF21", AID_MAESTRO, "*", amount("300")),
    ("DF21", AID_TEST, "*", amount("100")),
    ("DF21", AID_MASTERCARD, "00", amount("10")),
    ("DF21", AID_MASTERCARD, "93", amount("10")),
    ("DF21", AID_MAESTRO, "00", amount("300")),
    ("DF21", AID_MAESTRO, "93", amount("300")),
    ("DF21", AID_TEST, "00", amount("10")),
    ("DF21", AID_TEST, "93", amount("10")),
]

BASE_EMV_TERM_SET = {
    "9F4E": "",             # Merchant Name and location: no value (IsEmpty=True)
    "DF8117": "00",         # Card Data Input Capability
    "9F1A": "0056",         # Terminal Country Code
    "9F15": "7032",         # Merchant Category Code (any non-zero value)
    "DF810C": "02",         # Kernel ID
    "9F6D": "0001",         # Mag-Stripe Application Version Number
    "DF811A": "9F6A04",     # Default UDOL
}
BASE_EMV_TERM_ABSENT = {
    "9F01",                 # Acquirer Identifier: tag not present
    "9F16",                 # Merchant Identifier: tag not present
    "9F1C",                 # Terminal Identification: tag not present
}

#: Table 15 "Default configuration data - Mag-Stripe" (PAGE 36-37).
BASE_MAG_ROWS = [
    ("DF811B", "*", "*", "60"),          # Kernel Configuration (mag-stripe mode)
    ("DF811E", "*", "*", "10"),
    ("DF811E", AID_MASTERCARD, "17", "20"),
    ("DF811E", AID_MAESTRO, "17", "F0"),
    ("DF812C", "*", "*", "00"),
    ("DF812C", AID_MAESTRO, "17", "F0"),
    ("9F7E", "*", "*", None),
    ("DF8124", "*", "*", amount("300")),
    ("DF8125", "*", "*", amount("500")),
    ("DF21", AID_MASTERCARD, "*", amount("200")),
    ("DF21", AID_MAESTRO, "*", amount("300")),
    ("DF21", AID_TEST, "*", amount("100")),
    ("DF21", AID_MASTERCARD, "00", amount("10")),
    ("DF21", AID_MASTERCARD, "93", amount("10")),
    ("DF21", AID_MAESTRO, "00", amount("300")),
    ("DF21", AID_MAESTRO, "93", amount("300")),
    ("DF21", AID_TEST, "00", amount("10")),
    ("DF21", AID_TEST, "93", amount("10")),
]

BASE_MAG_TERM_SET = {
    "9F4E": "",
    "9F1A": "0056",
    "9F15": "7032",
    "DF810C": "02",
    "9F6D": "0001",
    "DF811A": "9F6A04",
}
BASE_MAG_TERM_ABSENT = set(BASE_EMV_TERM_ABSENT)

#: Table 17 (Mag-Stripe, PAGE 41-44) and Table 18 (EMV, PAGE 44-56) deltas.
CONFIG_ROWS: dict[str, list[tuple]] = {
    # -- EMV ---------------------------------------------------------------
    "PPS_MChip1_ReadAll": [           # PAGE 44
        ("DF811B", AID_MASTERCARD, "*", "24"),   # 20 | bit3 (read all records)
        ("DF811B", AID_MAESTRO, "*", "A4"),      # A0 | bit3
        ("DF811B", AID_TEST, "*", "24"),
        ("DF811F", AID_TEST, "*", "00"),         # Security Capability '00'
    ],
    "PPS_CVM_1": [                    # PAGE 44
        ("DF8124", "*", "*", amount("1000")),
        ("DF8125", "*", "*", amount("1000")),
    ],
    "PPS_CVM_2": [                    # PAGE 46 (delta lists 00/01/09/17 only)
        ("DF8118", AID_MASTERCARD, "20", "60"),
        ("DF8119", AID_MASTERCARD, "20", "08"),
    ],
    "PPS_Limit_2": [                  # PAGE 47
        ("DF8124", AID_TEST, "*", amount("100")),
    ],
    "PPS_Limit_3": [                  # PAGE 47-48 (only these two differ)
        ("DF8125", AID_MAESTRO, "*", amount("10")),
        ("DF811B", AID_TEST, "*", "A0"),         # 'A0' if MagStripe implemented
    ],
    "PPS_MC_ATM": [                   # PAGE 48
        ("9F40", "*", "*", "8000000000"),        # Additional Terminal Capabilities B1=80
        ("9F35", AID_MASTERCARD, "*", "14"),
        ("9F35", AID_MAESTRO, "*", "15"),
        ("9F35", AID_TEST, "*", "16"),
    ],
    "PPS_MC_notATM": [                # PAGE 48
        ("9F40", AID_MASTERCARD, "*", "FF00000000"),
        ("9F40", AID_MAESTRO, "*", "FF00000000"),
        ("9F40", AID_TEST, "*", "7F00000000"),
        ("9F35", AID_MASTERCARD, "*", "11"),
        ("9F35", AID_MAESTRO, "*", "24"),
        ("9F35", AID_TEST, "*", "14"),
    ],
    "PPS_MC_prop": [                  # PAGE 49 (action tags are term-level)
    ],
    "PPS_MCerrKernelConf": [          # PAGE 49 (both modes unsupported -> E0)
        ("DF811B", AID_TEST, "*", "E0"),
    ],
    "PPS_MChip2": [                   # PAGE 49
        ("DF811B", AID_MASTERCARD, "*", "30"),   # RRP bit
        ("DF12", "*", "*", "7CD8FCF8F0"),
        ("9F35", AID_MASTERCARD, "*", "12"),
        ("9F35", AID_MAESTRO, "*", "12"),
        ("9F35", AID_TEST, "*", "13"),
    ],
    "PPS_MChip3": [                   # PAGE 49
        ("DF13", "*", "*", "7CD8FCF8F0"),
        ("9F35", AID_MASTERCARD, "*", "14"),
        ("9F35", AID_MAESTRO, "*", "15"),
        ("9F35", AID_TEST, "*", "16"),
    ],
    "PPS_MChip4": [                   # PAGE 50
        ("DF12", "*", "*", "0020000000"),
        ("9F35", AID_MASTERCARD, "*", "21"),
        ("9F35", AID_MAESTRO, "*", "22"),
        ("9F35", AID_TEST, "*", "23"),
    ],
    "PPS_MChip5": [                   # PAGE 50
        ("DF13", "*", "*", "0020000000"),
        ("9F35", AID_MASTERCARD, "*", "24"),
        ("9F35", AID_MAESTRO, "*", "25"),
        ("9F35", AID_TEST, "*", "26"),
    ],
    "PPS_MChip6": [                   # PAGE 50
        ("DF11", "*", "*", "0040000000"),
        ("9F35", AID_MASTERCARD, "*", "34"),
        ("9F35", AID_MAESTRO, "*", "35"),
        ("9F35", AID_TEST, "*", "36"),
        ("DF811B", AID_TEST, "*", "30"),         # RRP bit (Test AID)
    ],
    "PPS_MChip7": [                   # PAGE 50
        ("9F35", AID_MASTERCARD, "*", "11"),
        ("9F35", AID_MAESTRO, "*", "12"),
        ("9F35", AID_TEST, "*", "13"),
    ],
    "PPS_MChip8": [                   # PAGE 50 ("plus"; DF8117 is term-level)
    ],
    "PPS_MChip9": [                   # PAGE 51
        ("DF811B", "*", "*", "30"),              # RRP bit, all AIDs
        ("DF811B", AID_MAESTRO, "*", "B0"),
        ("DF12", "*", "*", "0000000002"),
        ("9F35", AID_MASTERCARD, "*", "12"),
        ("9F35", AID_MAESTRO, "*", "22"),
        ("9F35", AID_TEST, "*", "23"),
    ],
    "PPS_MChip10": [                  # PAGE 51
        ("DF12", "*", "*", "0000000001"),
        ("9F35", AID_MASTERCARD, "*", "12"),
        ("9F35", AID_MAESTRO, "*", "22"),
        ("9F35", AID_TEST, "*", "23"),
    ],
    "PPS_MChip11": [                  # PAGE 51
        ("DF811B", "*", "*", "30"),
        ("DF811B", AID_MAESTRO, "*", "B0"),
        ("DF12", "*", "*", "FFFFFFFFFF"),
        ("9F35", AID_MASTERCARD, "*", "36"),
        ("9F35", AID_MAESTRO, "*", "13"),
        ("9F35", AID_TEST, "*", "36"),
        ("DF11", AID_MASTERCARD, "*", "0000000001"),
        ("DF11", AID_MAESTRO, "*", "0000000002"),
    ],
    "PPS_MCnoMSI": [                  # PAGE 53
        ("9F09", AID_MASTERCARD, "*", "0003"),
        ("9F7E", AID_MAESTRO, "*", "02"),
    ],
    "PPS_TrxTypeOther": [             # PAGE 56 (new transaction-type datasets)
        ("DF21", AID_MASTERCARD, "21", amount("10")),   # dataset 21 == dataset 00
        ("DF21", AID_MAESTRO, "02", amount("250")),     # dataset 02 + 250.00
    ],
    "PPS_MConlineonly": [             # PAGE 54
        ("DF811F", AID_MAESTRO, "*", "00"),
    ],
    "PPS_perf_MC": [                  # PAGE 54
        ("DF811B", "*", "*", "30"),
        ("DF811B", AID_MAESTRO, "*", "B0"),
        ("9F40", "*", "*", "6000F00001"),
        ("DF11", "*", "*", "0400800000"),
        ("DF13", "*", "*", "DC50000000"),
        ("DF19", "*", "*", amount("9000")),
        ("DF21", "*", "*", amount("9000")),
        ("DF8124", "*", "*", amount("9000")),
        ("DF8125", "*", "*", amount("9000")),
        ("9F35", "*", "*", "26"),
    ],
    "PPS_RRP": [                      # PAGE 54
        ("DF811B", "*", "*", "30"),
        ("DF811B", AID_MAESTRO, "*", "B0"),
    ],
    "TermSettingC-2": [               # PAGE 56 ("Same as PPS_perf_M plus")
        ("DF811B", "*", "*", "30"),
        ("DF811B", AID_MAESTRO, "*", "B0"),
        ("9F40", "*", "*", "6000F00001"),
        ("DF11", "*", "*", "0400800000"),
        ("DF13", "*", "*", "DC50000000"),
        ("DF19", "*", "*", amount("9000")),
        ("DF21", "*", "*", amount("9000")),
        ("DF8124", "*", "*", amount("9000")),
        ("DF8125", "*", "*", amount("9000")),
        ("9F35", "*", "*", "26"),
    ],
    # -- Mag-Stripe --------------------------------------------------------
    "PPS_MS_HT0": [                   # PAGE 41 (term-level, see CONFIG_TERM)
    ],
    "PPS_MS_Limit_1": [               # PAGE 41
        ("DF8124", AID_MASTERCARD, "*", amount("9000")),
        ("DF8125", AID_MASTERCARD, "*", amount("6000")),
        ("DF21", AID_MASTERCARD, "*", amount("51.50")),
        ("DF19", AID_MASTERCARD, "*", amount("100")),
        ("DF8124", AID_MAESTRO, "*", amount("9000")),
        ("DF8125", AID_MAESTRO, "*", amount("10")),
        ("DF21", AID_MAESTRO, "*", amount("75")),
        ("DF19", AID_MAESTRO, "*", amount("200")),
        ("DF8124", AID_TEST, "*", amount("9000")),
        ("DF8125", AID_TEST, "*", amount("7000")),
        ("DF21", AID_TEST, "*", amount("102.50")),
        ("DF19", AID_TEST, "*", amount("150")),
    ],
    "PPS_MS_Limit_2": [               # PAGE 41-42
        ("DF8124", AID_MASTERCARD, "*", amount("2")),
        ("DF8125", AID_MASTERCARD, "*", amount("6000")),
        ("DF21", AID_MASTERCARD, "*", amount("51.50")),
        ("DF19", AID_MASTERCARD, "*", amount("100")),
        ("DF8124", AID_MAESTRO, "*", amount("2")),
        ("DF8125", AID_MAESTRO, "*", amount("10")),
        ("DF21", AID_MAESTRO, "*", amount("75")),
        ("DF19", AID_MAESTRO, "*", amount("200")),
        ("DF8124", AID_TEST, "*", amount("2")),
        ("DF8125", AID_TEST, "*", amount("7000")),
        ("DF21", AID_TEST, "*", amount("102.50")),
        ("DF19", AID_TEST, "*", amount("150")),
    ],
    "PPS_MS_Limit_3": [               # PAGE 42
        ("DF8124", AID_MASTERCARD, "*", amount("20")),
        ("DF21", AID_MASTERCARD, "*", amount("51.50")),
        ("DF19", AID_MASTERCARD, "*", amount("100")),
        ("DF8124", AID_MAESTRO, "*", amount("300")),
        ("DF21", AID_MAESTRO, "*", amount("75")),
        ("DF19", AID_MAESTRO, "*", amount("200")),
        ("DF8124", AID_TEST, "*", amount("400")),
        ("DF21", AID_TEST, "*", amount("102.50")),
        ("DF19", AID_TEST, "*", amount("150")),
    ],
    "PPS_MS_Limit_3b": [              # PAGE 42 (same as _3, MC mag-stripe only)
        ("DF8124", AID_MASTERCARD, "*", amount("20")),
        ("DF21", AID_MASTERCARD, "*", amount("51.50")),
        ("DF19", AID_MASTERCARD, "*", amount("100")),
        ("DF8124", AID_MAESTRO, "*", amount("300")),
        ("DF21", AID_MAESTRO, "*", amount("75")),
        ("DF19", AID_MAESTRO, "*", amount("200")),
        ("DF8124", AID_TEST, "*", amount("400")),
        ("DF21", AID_TEST, "*", amount("102.50")),
        ("DF19", AID_TEST, "*", amount("150")),
        ("DF811B", AID_MASTERCARD, "*", "40"),   # mag-stripe mode only
    ],
    "PPS_MS_prop": [                  # PAGE 43 (action tags are term-level)
        ("9F1C", "*", "*", None),                # Terminal Identification absent
    ],
}

#: Rows the document requires that neither reference artifact carries:
#: ``(aid, txn, kernel, source_txn)`` clones the source row and changes 9C.
#: CNF-03 (PAGE 31) and Table 12 (PAGE 31) list transaction type '93'
#: (contactless ATM) for every AID, and PPS_MC_ATM (PAGE 48) gives each AID its
#: Terminal Type; only the Mastercard AID has a '93' row in the runtime data.
CONFIG_EXTRA_ROWS: dict[str, list[tuple[str, str, str, str]]] = {
    "PPS_MC_ATM": [(AID_MAESTRO, "93", "02", "00"),
                   (AID_TEST, "93", "02", "00")],
}

#: Term-level overrides/additions: ``(tag, value)``; ``None`` removes the tag.
CONFIG_TERM: dict[str, list[tuple[str, str | None]]] = {
    "PPS_MChip8": [("DF8117", "60")],            # PAGE 50
    "PPS_perf_MC": [("DF8117", "60"),            # PAGE 54
                    ("DF8133", "01C2")],         # RRPrMaxGrace
    "TermSettingC-2": [("DF8117", "60"),         # PAGE 56
                       ("DF8133", "01C2")],
    "PPS_RRP2": [("DF8134", "0FA0")],            # PAGE 54 RRPrExpectedCAPDU
    "PPS_RRP3": [("DF8135", "0FA0")],            # RRPrExpectedRAPDU
    "PPS_RRP4": [("DF8132", "0FA0")],            # RRPrMinGrace
    "PPS_RRP5": [("DF8133", "0960")],            # RRPrMaxGrace
    "PPS_RRP6": [("DF8136", "0FA0")],            # RRPrAccuracyThreshold
    "PPS_RRP7": [("DF8135", "0000")],            # RRPrExpectedRAPDU
    # PAGE 49 (EMV) / PAGE 43 (MAG): reference-private action tags carry the
    # document's proprietary-tag statements -- 9F01 not present, 9F05/DF30/9F17
    # present with no value, 81/CF present with a value.
    "PPS_MC_prop": [("DF8309", "9F01"),
                    ("DF8308", "9F05DF30"),
                    ("DF830A", "810400000000CF03778899")],
    "PPS_MS_prop": [("DF8308", "9F059F17"),
                    ("DF830A", "810400000000"),
                    ("9F1C", None)],
    "PPS_MS_HT0": [("DF812D", "000000")],        # PAGE 41 Message Hold Time
    "PPS_MCnoDefault_1": [("DF811A", None),      # PAGE 52: Always-class objects
                          ("DF810C", None),
                          ("9F6D", None),
                          ("9F1A", None)],
    "PPS_MCnoDefault_2": [("DF811A", None), ("DF810C", None), ("9F6D", None),
                          ("9F1A", None), ("9F1E", None)],
    "PPS_MCnoDefault_3": [("DF811A", None), ("DF810C", None), ("9F6D", None),
                          ("9F1A", None)],
    "PPS_MCnoDefault_4": [("DF811A", None), ("DF810C", None), ("9F6D", None),
                          ("9F1A", None)],
    "PPS_MCnoDefault_5": [("DF811A", None), ("DF810C", None), ("9F6D", None),
                          ("9F1A", None), ("9F1E", None)],
    # Table 17 PAGE 43-44: PPS_NoDefault_1 forbids the same Always-class
    # objects; PPS_NoDefault_2 re-adds the documented ones (see CONFIG_ROWS).
    "PPS_NoDefault_1": [("DF811A", None), ("DF810C", None), ("9F6D", None),
                        ("9F1A", None), ("DF812D", None)],
    "PPS_NoDefault_2": [("DF811A", None), ("DF810C", None), ("9F6D", None),
                        ("9F1A", None), ("9F1E", None), ("DF812D", None)],
}

#: ``PPS_MCnoDefault_*`` / ``PPS_NoDefault_*`` deliberately start from an almost
#: empty row: the document forbids the Always-class objects, and the reference
#: host drops the remaining EMV-class objects too (that class's table is not
#: printed in CTEL2 v4.1 -- see DOC_CONFLICTS).  These configs therefore skip
#: the base rules and only carry what the reference keeps plus the documented
#: re-adds.
NO_BASE_CONFIGS = {"PPS_MCnoDefault_1", "PPS_MCnoDefault_2",
                   "PPS_MCnoDefault_3", "PPS_MCnoDefault_4",
                   "PPS_MCnoDefault_5", "PPS_NoDefault_1", "PPS_NoDefault_2"}

#: Values re-added by the noDefault deltas (PAGE 52-53): these objects are back
#: in the TLV database with the value below.
CONFIG_ROWS.update({
    "PPS_MCnoDefault_2": [
        ("DF8124", "*", "*", amount("100")),
        ("DF8125", "*", "*", amount("100")),
        ("DF21", AID_MAESTRO, "*", amount("50")),
        ("DF21", AID_TEST, "*", amount("50")),
    ],
    "PPS_MCnoDefault_3": [
        ("DF8124", "*", "*", amount("100")),
        ("DF8125", "*", "*", amount("100")),
        ("DF21", AID_MAESTRO, "*", amount("50")),
        ("DF21", AID_TEST, "*", amount("50")),
        ("DF811F", AID_MAESTRO, "*", "08"),
        ("DF811F", AID_TEST, "*", "08"),
        ("DF13", "*", "*", "0000000000"),
    ],
    "PPS_MCnoDefault_4": [
        ("DF8124", "*", "*", amount("100")),
        ("DF8125", "*", "*", amount("100")),
        ("9F35", "*", "*", "23"),
        ("DF21", AID_MAESTRO, "*", amount("50")),
        ("DF21", AID_TEST, "*", amount("50")),
        ("DF811F", AID_MAESTRO, "*", "08"),
        ("DF811F", AID_TEST, "*", "08"),
        ("DF13", AID_MASTERCARD, "*", "0000000000"),
        ("DF13", AID_MAESTRO, "*", "0000000000"),
    ],
    "PPS_MCnoDefault_5": [
        ("DF8124", "*", "*", amount("100")),
        ("DF8125", "*", "*", amount("100")),
        ("DF21", AID_MAESTRO, "*", amount("50")),
        ("DF21", AID_TEST, "*", amount("50")),
        ("DF812C", AID_MAESTRO, "*", "10"),
        ("DF812C", AID_TEST, "*", "10"),
    ],
    # Table 17 PAGE 43 (PPS_NoDefault_1 forbids the Always-class objects) and
    # PAGE 43-44 (PPS_NoDefault_2 re-adds them with these values).
    "PPS_NoDefault_1": [
        (tag, "*", "*", None) for tag in
        ("DF811B", "DF811E", "DF812C", "DF8124", "DF8125", "DF21")
    ],
    "PPS_NoDefault_2": [
        (tag, "*", "*", None) for tag in ("DF811E", "DF812C")
    ] + [
        ("DF8124", "*", "*", amount("100")),
        ("DF8125", "*", "*", amount("100")),
        ("DF21", AID_MAESTRO, "*", amount("50")),
        ("DF21", AID_TEST, "*", amount("50")),
        ("DF811B", AID_MAESTRO, "*", "60"),
    ],
})

#: Row sets the document fixes itself (Tables 19-22, PAGE 56-57): the reference
#: runtime rows are used verbatim and row completion is skipped.
SELECTION_CONFIGS = {"PPS_Select1", "PPS_SPI_Select"}

# ---------------------------------------------------------------------------
# Config specs
# ---------------------------------------------------------------------------

BASE_EMV = "PPS_MChip1"
BASE_MAG = "PPS_MStripe1"

CONFIG_SPECS: dict[str, dict] = {
    "PPS_MChip1": {"base": "emv", "doc": "Table 16 PAGE 38-40, Table 18 PAGE 44"},
    "PPS_MChip1_ReadAll": {"base": "emv", "doc": "Table 18 PAGE 44"},
    "PPS_CVM_1": {"base": "emv", "doc": "Table 18 PAGE 44-46"},
    "PPS_CVM_2": {"base": "emv", "doc": "Table 18 PAGE 46"},
    "PPS_Limit_1": {"base": "emv", "doc": "Table 18 PAGE 46"},
    "PPS_Limit_1b": {"base": "emv", "doc": "Table 18 PAGE 47"},
    "PPS_Limit_2": {"base": "emv", "doc": "Table 18 PAGE 47"},
    "PPS_Limit_3": {"base": "emv", "doc": "Table 18 PAGE 47-48"},
    "PPS_MC_ATM": {"base": "emv", "doc": "Table 18 PAGE 48, CNF-03 PAGE 31"},
    "PPS_MC_notATM": {"base": "emv", "doc": "Table 18 PAGE 48"},
    "PPS_MC_prop": {"base": "emv", "doc": "Table 18 PAGE 49"},
    "PPS_MCerrKernelConf": {"base": "emv", "doc": "Table 18 PAGE 49"},
    "PPS_MChip2": {"base": "emv", "doc": "Table 18 PAGE 49"},
    "PPS_MChip3": {"base": "emv", "doc": "Table 18 PAGE 49"},
    "PPS_MChip4": {"base": "emv", "doc": "Table 18 PAGE 50"},
    "PPS_MChip5": {"base": "emv", "doc": "Table 18 PAGE 50"},
    "PPS_MChip6": {"base": "emv", "doc": "Table 18 PAGE 50"},
    "PPS_MChip7": {"base": "emv", "doc": "Table 18 PAGE 50"},
    "PPS_MChip8": {"base": "emv", "doc": "Table 18 PAGE 50"},
    "PPS_MChip9": {"base": "emv", "doc": "Table 18 PAGE 51"},
    "PPS_MChip10": {"base": "emv", "doc": "Table 18 PAGE 51"},
    "PPS_MChip11": {"base": "emv", "doc": "Table 18 PAGE 51"},
    "PPS_MCnoDefault_1": {"base": "emv", "doc": "Table 18 PAGE 52, Table 17 PAGE 43"},
    "PPS_MCnoDefault_2": {"base": "emv", "doc": "Table 18 PAGE 52"},
    "PPS_MCnoDefault_3": {"base": "emv", "doc": "Table 18 PAGE 52-53"},
    "PPS_MCnoDefault_4": {"base": "emv", "doc": "Table 18 PAGE 53"},
    "PPS_MCnoDefault_5": {"base": "emv", "doc": "Table 18 PAGE 53",
                          "note": "the document marks this row MAG; the "
                                  "Mag-stripe CVM Capability - No CVM Required "
                                  "value is '10'"},
    "PPS_MCnoMSI": {"base": "emv", "doc": "Table 18 PAGE 53"},
    "PPS_MConlineonly": {"base": "emv", "doc": "Table 18 PAGE 54"},
    "PPS_perf_MC": {"base": "emv", "doc": "Table 18 PAGE 54"},
    "PPS_RRP": {"base": "emv", "doc": "Table 18 PAGE 54"},
    "PPS_RRP2": {"base": "emv", "doc": "Table 18 PAGE 54"},
    "PPS_RRP3": {"base": "emv", "doc": "Table 18 PAGE 54"},
    "PPS_RRP4": {"base": "emv", "doc": "Table 18 PAGE 54"},
    "PPS_RRP5": {"base": "emv", "doc": "Table 18 PAGE 54"},
    "PPS_RRP6": {"base": "emv", "doc": "Table 18 PAGE 54"},
    "PPS_RRP7": {"base": "emv", "doc": "Table 18 PAGE 54"},
    "PPS_Select1": {"base": "emv", "doc": "Table 18 PAGE 55, Tables 19-22 PAGE 56-57",
                    "note": "row set follows the document's application "
                            "selection matrix (Kernel 2 / Kernel 8 per AID and "
                            "transaction type), which the reference runtime "
                            "already encodes as 9F2A rows including Cirrus"},
    "PPS_SPI": {"base": "emv", "doc": "Table 18 PAGE 55"},
    "PPS_SPI_Dup": {"base": "emv", "doc": "Table 18 PAGE 55"},
    "PPS_SPI_NoTermCat": {"base": "emv", "doc": "Table 18 PAGE 55"},
    "PPS_SPI_Select": {"base": "emv", "doc": "Table 18 PAGE 56, Tables 19-22 PAGE 56-57",
                       "note": "inherits PPS_Select1's selection matrix "
                               "(Cirrus AID required)"},
    "PPS_TrxTypeOther": {"base": "emv", "doc": "Table 18 PAGE 56"},
    "TermSettingC-2": {"base": "emv", "doc": "Table 18 PAGE 56",
                       "note": "EMVCo session scope only ('Same as PPS_perf_M "
                               "plus')"},
    "PPS_MStripe1": {"base": "mag", "doc": "Table 15 PAGE 36-37, Table 17 PAGE 41"},
    "PPS_MS_HT0": {"base": "mag", "doc": "Table 17 PAGE 41"},
    "PPS_MS_Limit_1": {"base": "mag", "doc": "Table 17 PAGE 41"},
    "PPS_MS_Limit_2": {"base": "mag", "doc": "Table 17 PAGE 41-42"},
    "PPS_MS_Limit_3": {"base": "mag", "doc": "Table 17 PAGE 42"},
    "PPS_MS_Limit_3b": {"base": "mag", "doc": "Table 17 PAGE 42"},
    "PPS_MS_prop": {"base": "mag", "doc": "Table 17 PAGE 43"},
    "PPS_NoDefault_1": {"base": "mag", "doc": "Table 17 PAGE 43"},
    "PPS_NoDefault_2": {"base": "mag", "doc": "Table 17 PAGE 43-44"},
}

#: Document statements this host cannot carry as kernel TLV data, recorded in
#: the generated index so nothing disappears silently.
DOC_CONFLICTS = [
    "Table 17 PAGE 41 (PPS_MStripe1): 'All other data items not listed in table "
    "Default Configuration Data - Mag-Stripe must be absent'. The same table's "
    "deltas then change the Terminal/Reader Contactless floor limit, an object "
    "the Mag-Stripe default table does not list; the reference runtime keeps "
    "9F09/9F1B/9F1D/DF11-DF13/DF19/DF20/DF32-DF36 on Mag-Stripe configs, and "
    "they are kept here (rubric: keep reference-only objects unless the "
    "document states a value).",
    "PPS_MCnoDefault_1..5 (PAGE 52): the referenced table 'Configuration Data "
    "in TLV Database that Require Default Value' (with its Implementations "
    "column) is not printed in CTEL2 v4.1; only the Mag-Stripe 'Always' rows "
    "(PAGE 43) are available. The Always-class objects are removed as the "
    "document requires; the 'EMV' class follows the reference runtime, which "
    "drops them too.",
    "PPS_MCnoDefault_2..5 re-add Reader CTL / Reader CVM Required Limit objects "
    "that PPS_MCnoDefault_1 forbids; the per-config delta wins (documented as "
    "an intentional override, not a contradiction to resolve here).",
    "9F33 Terminal Capability ('Tag not present' in Table 16 PAGE 39) is kept "
    "where the reference runtime carries it, per the ruling to keep "
    "reference-only objects.",
    "PPS_perf_MC / TermSettingC-2 (PAGE 54): the C-8 kernel test configuration "
    "'C8_prof01' lives in EMVCo's ReaderConfs.xmd and has no kernel data object "
    "in this library.",
]


class ImportError_(RuntimeError):
    """Bad source data or an unsafe destination."""


# ---------------------------------------------------------------------------
# reference runtime baseline
# ---------------------------------------------------------------------------

def load_baseline(path: Path) -> tuple[list[Row], dict[str, str]]:
    """Read one runtime reference JSON into rows + term map."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise ImportError_(f"{path}: cannot read: {exc}") from exc
    rows: list[Row] = []
    for raw in data.get("AIDParam") or []:
        aid = str(raw.get("9F06") or "").strip().upper()
        if not aid:
            raise ImportError_(f"{path}: a row has no 9F06")
        values = {tag: ("" if value is None else str(value).strip().upper())
                  for tag, value in raw.items() if tag not in ("9F06", "9C")}
        rows.append({
            "aid": aid,
            "txn": str(raw.get("9C") or "").strip().upper(),
            "kernel": str(raw.get("9F2A") or "02").strip().upper(),
            "values": values,
        })
    term = {tag: ("" if value is None else str(value).strip().upper())
            for tag, value in (data.get("TermParam") or {}).items()}
    return rows, term


def merge_rows(rows: list[Row], extra: list[Row]) -> list[Row]:
    """Add rows present only in the older XML snapshot (same AID x txn)."""
    known = {(r["aid"], r["txn"], r["kernel"]) for r in rows}
    have_pair = {(r["aid"], r["txn"]) for r in rows}
    merged = list(rows)
    for row in extra:
        key = (row["aid"], row["txn"], row["kernel"])
        if key in known:
            continue
        if (row["aid"], row["txn"]) in have_pair:
            continue
        merged.append(row)
        known.add(key)
        have_pair.add((row["aid"], row["txn"]))
    return merged


def _matches(rule_aid: str, rule_txn: str, aid: str, txn: str) -> bool:
    return (rule_aid in (_WILDCARD, aid)) and (rule_txn in (_WILDCARD, txn))


def _apply_rules(values: dict[str, str], rules, aid: str, txn: str,
                 applied: list[str], where: str) -> None:
    for tag, rule_aid, rule_txn, value in rules:
        if not _matches(rule_aid, rule_txn, aid, txn):
            continue
        before = values.get(tag)
        if value is None:
            if before is None:
                continue
            values.pop(tag, None)
            applied.append(f"{where}: remove {tag} (doc)")
        else:
            if before == value:
                continue
            values[tag] = value
            applied.append(f"{where}: {tag} {before or '-'} -> {value} (doc)")


def build_config(config_id: str, spec: dict, ref_dir: Path,
                 report: list[str]) -> dict:
    """Build one ``KernelParams`` document: reference structure, doc values."""
    cfg_path = ref_dir / "Config" / f"{config_id}.json"
    xml_aid = ref_dir / "AID" / f"{config_id}.xml"
    if not cfg_path.is_file():
        raise ImportError_(f"missing runtime reference config: {cfg_path}")

    rows, term = load_baseline(cfg_path)
    if config_id not in SELECTION_CONFIGS and xml_aid.is_file():
        extra_rows, _ = load_baseline_legacy(xml_aid)
        completed = merge_rows(rows, extra_rows)
        if len(completed) != len(rows):
            report.append(f"{config_id}: +{len(completed) - len(rows)} "
                          f"(AID x transaction type) rows present only in "
                          f"AID/{config_id}.xml")
        rows = completed
    elif config_id in SELECTION_CONFIGS:
        report.append(f"{config_id}: row set kept exactly as the runtime "
                      f"reference's application selection matrix (Tables 19-22)")
    for aid, txn, kernel, source_txn in CONFIG_EXTRA_ROWS.get(config_id, []):
        if any(r["aid"] == aid and r["txn"] == txn for r in rows):
            continue
        template = next((r for r in rows
                         if r["aid"] == aid and r["txn"] == source_txn), None)
        if template is None:
            raise ImportError_(f"{config_id}: cannot build {aid}/{txn} from "
                               f"{aid}/{source_txn}")
        rows.append({"aid": aid, "txn": txn, "kernel": kernel,
                     "values": dict(template["values"])})
        report.append(f"{config_id}: +row {aid}/{txn}/{kernel} (clone of "
                      f"{aid}/{source_txn}) per CNF-03 PAGE 31")

    term_set = dict(BASE_MAG_TERM_SET if spec["base"] == "mag"
                    else BASE_EMV_TERM_SET)
    term_absent = set(BASE_MAG_TERM_ABSENT if spec["base"] == "mag"
                      else BASE_EMV_TERM_ABSENT)
    if config_id in NO_BASE_CONFIGS:
        # The noDefault family must not gain the base objects (the document
        # forbids them); keep the reference's term and apply the removals.
        term_set.clear()
    for tag, value in CONFIG_TERM.get(config_id, []):
        if value is None:
            term_absent.add(tag)
            term_set.pop(tag, None)
        else:
            term_set[tag] = value
            term_absent.discard(tag)
            report.append(f"{config_id}: term {tag} = {value} (doc)")

    base_rules = BASE_MAG_ROWS if spec["base"] == "mag" else BASE_EMV_ROWS
    config_rules = CONFIG_ROWS.get(config_id, [])
    skip_base = config_id in NO_BASE_CONFIGS

    aids: list[dict] = []
    for row in sorted(rows, key=lambda r: (r["aid"], r["txn"], r["kernel"])):
        where = f"{config_id} {row['aid']}/{row['txn'] or '-'}/{row['kernel']}"
        values = {tag: value for tag, value in row["values"].items()
                  if tag not in DEDS_TAGS and value}
        for tag in DEDS_TAGS:
            if tag in row["values"]:
                report.append(f"{where}: {tag} dropped (DEDS)")
        if not row["txn"]:
            raise ImportError_(f"{where}: row has no 9C")
        if skip_base:
            # noDefault family: keep only the objects the runtime reference
            # keeps, then let the config deltas add the documented ones back.
            for tag in list(values):
                if tag not in ("9F1D", "9F40", "DF32", "DF33", "DF34", "DF35",
                               "DF36", "9F7E"):
                    values.pop(tag, None)
            report.append(f"{where}: base EMV rules skipped (noDefault family)")
        else:
            _apply_rules(values, base_rules, row["aid"], row["txn"], report, where)
        _apply_rules(values, config_rules, row["aid"], row["txn"], report, where)
        values["9C"] = row["txn"]
        values["9F2A"] = row["kernel"]
        values = {tag: value for tag, value in values.items() if value != ""}
        fields = [{"tag": tag, "value": values[tag]}
                  for tag in _ordered(values, AID_FIELD_ORDER)]
        aids.append({"aid": row["aid"], "fields": fields})

    term_values: dict[str, str] = {}
    for tag, value in term.items():
        if tag in DEDS_TAGS:
            if value:
                report.append(f"{config_id} term: {tag} dropped (DEDS)")
            continue
        if tag in term_absent:
            report.append(f"{config_id} term: {tag} removed (doc: tag not "
                          f"present)")
            continue
        if tag in term_set:
            continue
        if value:
            term_values[tag] = value
    for tag, value in term_set.items():
        term_values[tag] = value
    term_values = {tag: value for tag, value in term_values.items()
                   if not (value == "" and tag not in ("9F4E", "9F05"))}
    for tag in list(term_values):
        value = term_values[tag]
        if tag in ASCII_TERM_TAGS and value:
            term_values[tag] = value.encode("ascii").hex().upper()
            continue
        if value and (len(value) % 2 or not re.fullmatch(r"[0-9A-F]+", value)):
            raise ImportError_(f"{config_id} term: {tag} value {value!r} is not "
                               f"whole-byte hex")
    term_hex = "".join(_tlv(tag, term_values[tag])
                       for tag in _ordered(term_values, TERM_ORDER))

    caps = [dict(entry, hash_alg=CAPK_HASH_ALG, key_alg=CAPK_KEY_ALG,
                 expiry=CAPK_EXPIRY) for entry in CAPK_APPENDIX_B]
    return {
        "aids": aids,
        "caps": caps,
        "drl": [],
        "exception_file": [],
        "term": term_hex,
        "version": VERSION,
    }


def load_baseline_legacy(path: Path) -> tuple[list[Row], dict[str, str]]:
    """Read the older ``AID/*.xml`` snapshot (used only to complete rows)."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ImportError_(f"{path}: cannot parse XML: {exc}") from exc
    rows: list[Row] = []
    for node in root.findall("KernelAidCombination"):
        values: dict[str, str] = {}
        for cfg in node.findall("Cfg"):
            tag = (cfg.get("label") or "").strip().upper()
            if not tag:
                continue
            values[tag] = _encode_value(cfg.get("Format") or "", cfg.text or "")
        aid = values.pop("9F06", "")
        if not aid:
            raise ImportError_(f"{path}: a KernelAidCombination has no 9F06")
        rows.append({"aid": aid, "txn": values.get("9C", ""),
                     "kernel": values.get("9F2A", "02"), "values": values})
    return rows, {}


def _encode_value(fmt: str, text: str) -> str:
    """Encode one legacy XML ``Cfg`` value following its ``Format``."""
    kind = (fmt or "").strip().lower()
    value = (text or "").strip()
    if not value:
        return ""
    if kind.startswith("b"):
        hexed = re.sub(r"\s+", "", value).upper()
        if len(hexed) % 2 or not re.fullmatch(r"[0-9A-F]+", hexed):
            raise ImportError_(f"Format {fmt!r} value {value!r} is not hex")
        return hexed
    if kind.startswith("n"):
        digits = re.sub(r"\s+", "", value).upper()
        if not digits.isdigit():
            raise ImportError_(f"Format {fmt!r} value {value!r} is not numeric")
        return ("0" + digits) if len(digits) % 2 else digits
    if kind.startswith("a"):
        return value.encode("ascii").hex().upper()
    return re.sub(r"\s+", "", value).upper()


def _ordered(tags, order) -> list[str]:
    known = [tag for tag in order if tag in tags]
    extra = sorted(tag for tag in tags if tag not in order)
    return known + extra


def _tlv(tag: str, value: str) -> str:
    raw = bytes.fromhex(value)
    if len(raw) <= 0x7F:
        length = bytes([len(raw)])
    elif len(raw) <= 0xFF:
        length = bytes([0x81, len(raw)])
    elif len(raw) <= 0xFFFF:
        length = bytes([0x82, (len(raw) >> 8) & 0xFF, len(raw) & 0xFF])
    else:  # pragma: no cover
        raise ImportError_(f"TLV {tag}: value too long ({len(raw)} bytes)")
    return tag + length.hex().upper() + value


def verify_capk() -> None:
    """DF03 must equal SHA1(9F06 || 9F22 || DF02 || DF04) for every key."""
    for entry in CAPK_APPENDIX_B:
        material = (bytes.fromhex(entry["rid"]) + bytes.fromhex(entry["key_index"])
                    + bytes.fromhex(entry["modulus"])
                    + bytes.fromhex(entry["exponent"]))
        computed = hashlib.sha1(material).hexdigest().upper()
        if computed != entry["checksum"]:
            raise ImportError_(
                f"CAPK {entry['rid']}/{entry['key_index']}: DF03 "
                f"{entry['checksum']} != SHA1(...) {computed}")


def write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def build_index(configs: dict[str, dict], report: list[str]) -> dict:
    entries = []
    for config_id in sorted(configs, key=lambda c: list(CONFIG_SPECS).index(c)):
        spec = CONFIG_SPECS[config_id]
        doc = configs[config_id]
        txns = sorted({f["value"] for a in doc["aids"] for f in a["fields"]
                       if f["tag"] == "9C"})
        kernels = sorted({f["value"] for a in doc["aids"] for f in a["fields"]
                          if f["tag"] == "9F2A"})
        entries.append({
            "id": config_id,
            "kind": "conf",
            "file": f"{config_id}.json",
            "base": BASE_EMV if spec["base"] == "emv" else BASE_MAG,
            "source": spec["doc"],
            "reference": f"Config/{config_id}.json (+ AID/{config_id}.xml for "
                         f"row completion)",
            "aids": len(doc["aids"]),
            "aid_list": sorted({a["aid"] for a in doc["aids"]}),
            "txn_types": txns,
            "kernel_ids": kernels,
            "capk": len(doc["caps"]),
            "drl": 0,
            "exception_file": 0,
            "term": bool(doc["term"]),
            "note": spec.get("note", ""),
        })
    return {
        "brand": "mastercard",
        "version": VERSION,
        "source_document": "Mastercard Contactless Testing Environment (CTEL2) "
                           "Version 4.1 Level 2 Reader Testing, 2025-06-17",
        "source_document_file": "m_ctel2_en-us-2025-06-17.pdf",
        "structure_source": "MastercardHost/Config runtime artifact "
                            "(Config/<id>.json: AIDParam rows keyed by 9F06 x 9C "
                            "x 9F2A, plus TermParam); AID/<id>.xml only completes "
                            "rows that the runtime artifact is missing",
        "tag_mapping": {
            "Terminal Action Code Default": "DF11",
            "Terminal Action Code Online": "DF12",
            "Terminal Action Code Denial": "DF13",
            "Reader Contactless Floor Limit": "DF19",
            "Contactless Transaction Limit": "DF20",
            "Reader CVM Required Limit": "DF21",
            "Reader CTL (No On-device CVM)": "DF8124",
            "Reader CTL (On-device CVM)": "DF8125",
            "Kernel Configuration": "DF811B",
            "Card Data Input Capability": "DF8117",
            "CVM Capability - CVM Required": "DF8118",
            "CVM Capability - No CVM Required": "DF8119",
            "Security Capability": "DF811F",
            "Mag-stripe CVM Capability - CVM Required": "DF811E",
            "Mag-stripe CVM Capability - No CVM Required": "DF812C",
            "Mobile Support Indicator": "9F7E",
            "Default UDOL": "DF811A",
            "Mag-Stripe Application Version Number": "9F6D",
            "Application Version Number (Reader)": "9F09",
            "Terminal Risk Management": "9F1D",
            "Terminal Type": "9F35",
            "Additional Terminal Capabilities": "9F40",
            "Terminal Capabilities": "9F33",
            "RRPrAccuracyThreshold": "DF8136",
            "RRPrExpectedCAPDU": "DF8134",
            "RRPrExpectedRAPDU": "DF8135",
            "RRPrMinGrace": "DF8132",
            "RRPrMaxGrace": "DF8133",
            "Message Hold Time": "DF812D",
        },
        "capk": {
            "source": "Appendix B PAGE 66-71 (Table 29 RID A000000004, Table 30 "
                      "RID B012345678)",
            "entries": len(CAPK_APPENDIX_B),
            "rids": sorted({c["rid"] for c in CAPK_APPENDIX_B}),
            "hash_alg": CAPK_HASH_ALG,
            "key_alg": CAPK_KEY_ALG,
            "expiry": CAPK_EXPIRY,
            "expiry_encoding": "ascii8",
            "note": "DF03 is the checksum printed in Appendix B, re-verified as "
                    "SHA1(9F06||9F22||DF02||DF04) at import time. The wider ICS "
                    "key list prints further indexes for these RIDs "
                    f"({', '.join(APPENDIX_B_OMITS)}); this document defines none "
                    "of them, so they are not imported.",
        },
        "deliberately_absent": {
            "drl": "ruling: a Mastercard CONF carries no DRL (CTEL2 Table 13 is "
                   "not imported)",
            "exception_file": "CTEL2 defines no exception file",
            "deds": "ruling: the DEDS (Data Exchange) family and DEDS-only data "
                    "objects are out of scope",
        },
        "proprietary_action_tags": PROPRIETARY_ACTIONS,
        "doc_conflicts": DOC_CONFLICTS,
        "import_report": report,
        "configs": entries,
    }


README_TEMPLATE = """\
# Mastercard POS config library（CTEL2 v4.1）

本目录由 `tools/import_mastercard_ctel2.py` 生成，来源分工：

| 角色 | 来源 |
|---|---|
| 取值权威 | Mastercard Contactless Testing Environment（CTEL2）Version 4.1 Level 2 Reader Testing，2025-06-17：Table 14（交易数据默认值）、Table 15/16（默认配置数据）、Table 17/18（测试配置差异）、Appendix B（CA Public Key） |
| 结构模板 | 参考实现运行态 `MastercardHost/Config/Config/<ID>.json`（`AIDParam` 行按 `9F06` × `9C` × `9F2A` 组织 + `TermParam`）；`AID/<ID>.xml` 仅用于补齐运行态缺失的行 |

工具把每一条对参考数据的改动都记在 `index.json` 的 `import_report` 与 `doc_conflicts` 里，
可以逐条回溯到文档页码。

## 结构

* `aids[]`：每个 (AID, 交易类型, Kernel ID) 一条；`9C`（交易类型）与 `9F2A`（Kernel ID：02=Kernel 2、08=Kernel 8）
  是 `fields` 的前两项。同一 AID 按交易类型/Kernel 重复出现是文档的写法，
  host 判重已按 `(9F06, 9C, 9F2A)` 组合判重（`cases/validation.py`）。
* `caps[]`：Appendix B 的 {capk_count} 个 CA 公钥，`DF06=01`、`DF07=01`、`DF05={expiry}`（ascii8）。
  `DF03` 用文档打印值，并在导入时按 `SHA1(9F06‖9F22‖DF02‖DF04)` 复核。
* `drl[]` / `exception_file[]`：**空**（裁定：Mastercard CONF 不带 DRL；CTEL2 未定义 exception file）。
* `term`：全局终端参数（`TermParam` + 文档要求补入的项，如 `DF8117`）。
* `version`：`{version}`（host 侧元数据，随 CONFIG 的 `VERSION` 下发）。

## 文档与参考实现的冲突

见 `index.json` 的 `doc_conflicts`，要点：

1. Table 17 对 `PPS_MStripe1` 的兜底句（"其余对象必须不存在"）与该表自身引用
   "Terminal/Reader Contactless floor limit" 矛盾；本库按"保留参考独有对象"的裁定保留
   MAG 侧的 `9F09/9F1B/9F1D/DF11-DF13/DF19/DF20/DF32-DF36`。
2. `PPS_MCnoDefault_1..5` 引用的 "Implementations" 表在 CTEL2 v4.1 中未印出；
   Always 类对象按文档删除，`EMV` 类跟随参考实现（同样删除）。
3. `PPS_MCnoDefault_2..5` 又把 `_1` 禁止的 Reader CTL / Reader CVM Required Limit 加回，
   按各配置 delta 处理。
4. `9F33`（Table 16 记 "Tag not present"）在参考实现带值处保留。

## 有意不导入

* DEDS（Data Exchange / DE-DS）配置族与 DEDS 专用对象（`DF8108`/`DF60`/`DF8109`/`DF62`/
  `DF810A`/`DF63`/`DF810D`/`DF8110`/`DF8112`/`FF8102`/`FF8103`/`DF8127`）。
* 文档未打印的 ICS 扩展 CAPK 索引：{capk_omits}。
* DRL（CTEL2 Table 13 的撤销列表）。

## 参考实现私有约定

`DF8308` / `DF8309` / `DF830A` 不是 EMVCo 内核数据对象，而是参考实现用来表达文档中
"某专有 tag 以零长度存在 / 必须不存在 / 以某值存在"的私有约定（如 `PPS_MC_prop` 的
`9F05`/`DF30`/`81`/`CF`），按"沿用参考写法"的裁定原样保留。

## 生成

```powershell
python tools/import_mastercard_ctel2.py `
    --ref-dir "E:/Work/VS_Project/MastercardHost/Config" `
    --out-dir cases/mastercard/pos_configs --brand mastercard --force
```
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import the Mastercard CTEL2 configuration data set as this "
                    "host's named POS config library.")
    parser.add_argument("--ref-dir", required=True,
                        help="MastercardHost/Config reference dir (Config/*.json, "
                             "AID/*.xml)")
    parser.add_argument("--out-dir", required=True, help="output dir (flat)")
    parser.add_argument("--brand", default="mastercard")
    parser.add_argument("--force", action="store_true",
                        help="overwrite files in a non-empty output dir")
    parser.add_argument("--report", default="",
                        help="also write the import report to this path")
    parser.add_argument("--only", default="",
                        help="comma-separated config ids (debugging aid)")
    args = parser.parse_args(argv)

    ref_dir = Path(args.ref_dir)
    out_dir = Path(args.out_dir)
    if not (ref_dir / "Config").is_dir():
        print(f"error: {ref_dir}/Config not found", file=sys.stderr)
        return 2
    if out_dir.exists() and any(out_dir.glob("*.json")) and not args.force:
        print(f"error: {out_dir} already holds configs; pass --force",
              file=sys.stderr)
        return 2

    try:
        verify_capk()
    except ImportError_ as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    wanted = ([x.strip() for x in args.only.split(",") if x.strip()]
              or list(CONFIG_SPECS))
    unknown = [c for c in wanted if c not in CONFIG_SPECS]
    if unknown:
        print(f"error: unknown config id(s): {unknown}", file=sys.stderr)
        return 2

    report: list[str] = []
    configs: dict[str, dict] = {}
    errors: list[str] = []
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        for config_id in wanted:
            try:
                configs[config_id] = build_config(
                    config_id, CONFIG_SPECS[config_id], ref_dir, report)
            except ImportError_ as exc:
                errors.append(str(exc))
        for config_id, doc in configs.items():
            write_json(out_dir / f"{config_id}.json", doc)
        write_json(out_dir / "index.json", build_index(configs, report))
        (out_dir / "README.md").write_text(
            README_TEMPLATE.format(capk_count=len(CAPK_APPENDIX_B),
                                   expiry=CAPK_EXPIRY, version=VERSION,
                                   capk_omits=", ".join(APPENDIX_B_OMITS)),
            encoding="utf-8")
    except (ImportError_, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.report:
        Path(args.report).write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"wrote {len(configs)} configs + index.json + README.md to {out_dir}")
    print(f"CAPK: {len(CAPK_APPENDIX_B)} keys from Appendix B (DF03 "
          f"re-verified), expiry {CAPK_EXPIRY}, no DRL")
    print(f"import report entries: {len(report)} (document corrections, DEDS "
          f"removals, row completion)")
    if errors:
        print(f"\n{len(errors)} config(s) skipped:", file=sys.stderr)
        for line in errors:
            print(f"  {line}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
