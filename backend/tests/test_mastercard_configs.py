"""The in-repo Mastercard CTEL2 config library (``cases/mastercard/pos_configs``).

Guards the delivered values, not just the file shapes:

* every config parses as ``KernelParams`` and passes ``validate_kernel_params``
  with no issues (this covers the 3-byte Mastercard tags DF811B/DF8124/... and
  the (9F06, 9C, 9F2A) row key);
* the CAPK set is exactly document Appendix B and every DF03 re-computes to
  SHA1(9F06||9F22||DF02||DF04);
* a set of document-stated values (CTEL2 Table 15/16 defaults and the
  Table 17/18 deltas that the reference library got wrong) is asserted
  verbatim, so a regeneration cannot silently lose a correction.

Citations are CTEL2 v4.1 (m_ctel2_en-us-2025-06-17.pdf) page numbers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from emv_host.cases.models import KernelParams
from emv_host.cases.validation import validate_kernel_params
from emv_host.protocol.kernel_tlv import compute_capk_checksum, decode_tlv_map

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "cases" / "mastercard" / "pos_configs"

MC = "A0000000041010"
MAESTRO = "A0000000043060"
TEST = "B012345678"

#: CTEL2 Table 18 EMV (44) + Table 17 Mag-Stripe (9) test configuration ids.
EMV_IDS = [
    "PPS_MChip1", "PPS_MChip1_ReadAll", "PPS_CVM_1", "PPS_CVM_2",
    "PPS_Limit_1", "PPS_Limit_1b", "PPS_Limit_2", "PPS_Limit_3",
    "PPS_MC_ATM", "PPS_MC_notATM", "PPS_MC_prop", "PPS_MCerrKernelConf",
    "PPS_MChip2", "PPS_MChip3", "PPS_MChip4", "PPS_MChip5", "PPS_MChip6",
    "PPS_MChip7", "PPS_MChip8", "PPS_MChip9", "PPS_MChip10", "PPS_MChip11",
    "PPS_MCnoDefault_1", "PPS_MCnoDefault_2", "PPS_MCnoDefault_3",
    "PPS_MCnoDefault_4", "PPS_MCnoDefault_5", "PPS_MCnoMSI", "PPS_MConlineonly",
    "PPS_perf_MC", "PPS_RRP", "PPS_RRP2", "PPS_RRP3", "PPS_RRP4", "PPS_RRP5",
    "PPS_RRP6", "PPS_RRP7", "PPS_Select1", "PPS_SPI", "PPS_SPI_Dup",
    "PPS_SPI_NoTermCat", "PPS_SPI_Select", "PPS_TrxTypeOther",
    "TermSettingC-2",
]
MAG_IDS = ["PPS_MStripe1", "PPS_MS_HT0", "PPS_MS_Limit_1", "PPS_MS_Limit_2",
           "PPS_MS_Limit_3", "PPS_MS_Limit_3b", "PPS_MS_prop",
           "PPS_NoDefault_1", "PPS_NoDefault_2"]
ALL_IDS = EMV_IDS + MAG_IDS


def load(config_id: str) -> dict:
    return json.loads((CONFIG_DIR / f"{config_id}.json").read_text(encoding="utf-8"))


def rows(doc: dict) -> list[tuple[str, str, str, dict[str, str]]]:
    out = []
    for entry in doc["aids"]:
        values = {f["tag"]: f["value"] for f in entry["fields"]}
        out.append((entry["aid"], values.get("9C", ""),
                    values.get("9F2A", ""), values))
    return out


def pick(doc: dict, aid: str, txn: str, kernel: str | None = None) -> dict[str, str]:
    """One row's values; with ``kernel=None`` the (AID, txn) pair must be unique."""
    hits = pick_all(doc, aid, txn, kernel)
    if not hits:
        raise AssertionError(f"no row {aid}/{txn}"
                             + (f"/{kernel}" if kernel else ""))
    if len(hits) > 1 and kernel is None:
        raise AssertionError(f"{len(hits)} rows for {aid}/{txn}; name the kernel")
    return hits[0]


def pick_all(doc: dict, aid: str, txn: str,
             kernel: str | None = None) -> list[dict[str, str]]:
    """Every row for one (AID, transaction type), Kernel 2 and 8 alike."""
    return [values for row_aid, row_txn, row_kernel, values in rows(doc)
            if (row_aid, row_txn) == (aid, txn)
            and (kernel is None or row_kernel == kernel)]


def term_map(doc: dict) -> dict[str, str]:
    return decode_tlv_map(doc["term"])


# ---------------------------------------------------------------------------
# library shape
# ---------------------------------------------------------------------------

def test_all_53_configs_exist():
    assert len(ALL_IDS) == 53
    assert len(set(ALL_IDS)) == 53
    for config_id in ALL_IDS:
        assert (CONFIG_DIR / f"{config_id}.json").is_file(), config_id


@pytest.mark.parametrize("config_id", ALL_IDS)
def test_each_config_is_a_valid_kernel_params(config_id):
    params = KernelParams.model_validate(load(config_id))
    report = validate_kernel_params(params, config_id, trust_checksum=True)
    assert not report.issues, [i.message for i in report.issues]


#: The case-wide CDA key: ``3C05-9100-06`` pushes PPS_perf_MC with a locally
#: generated test CA public key under index 01 (the reference card names CTEL2
#: Appendix B index F3, whose private key is not available) -- see
#: ``tools/make_mc_cda_case.py`` and the index.json note.
CDA_CONFIG_EXTRA_CAPK = {"PPS_perf_MC": 1}


@pytest.mark.parametrize("config_id", ALL_IDS)
def test_no_drl_no_exception_file_and_full_capk_set(config_id):
    doc = load(config_id)
    assert doc["drl"] == []            # ruling: Mastercard CONFs carry no DRL
    assert doc["exception_file"] == []
    expected = 12 + CDA_CONFIG_EXTRA_CAPK.get(config_id, 0)
    assert len(doc["caps"]) == expected  # CTEL2 Appendix B, PAGE 66-71
    assert doc["version"] == "ctel2-4.1"
    if config_id in CDA_CONFIG_EXTRA_CAPK:
        extra = [c for c in doc["caps"] if c["key_index"] == "01"]
        assert len(extra) == 1, "the CDA CA key must be the only index-01 key"
        assert extra[0]["rid"] == "A000000004"


def test_only_the_cda_config_carries_a_generated_key():
    """Every other config must stay exactly the CTEL2 Appendix B key set."""
    for config_id in ALL_IDS:
        if config_id in CDA_CONFIG_EXTRA_CAPK:
            continue
        assert not [c for c in load(config_id)["caps"]
                    if c["key_index"] == "01"], config_id


def test_capk_checksums_match_appendix_b():
    doc = load("PPS_MChip1")
    keys = {(c["rid"], c["key_index"]) for c in doc["caps"]}
    assert keys == {("A000000004", "00"), ("A000000004", "EF"),
                    ("A000000004", "F1"), ("A000000004", "F3"),
                    ("A000000004", "F4"), ("A000000004", "F5"),
                    ("A000000004", "F6"), ("A000000004", "F9"),
                    ("B012345678", "00"), ("B012345678", "02"),
                    ("B012345678", "F1"), ("B012345678", "F3")}
    for cap in doc["caps"]:
        assert cap["hash_alg"] == 1 and cap["key_alg"] == 1
        assert cap["expiry"] == "20491231"
        assert cap["checksum"] == compute_capk_checksum(
            cap["rid"], cap["key_index"], cap["modulus"], cap["exponent"])


def test_rows_are_keyed_by_aid_txn_and_kernel_id():
    doc = load("PPS_MChip1")
    keys = [(aid, txn, kernel) for aid, txn, kernel, _ in rows(doc)]
    assert len(keys) == len(set(keys))                 # no duplicate triples
    assert {(aid, txn) for aid, txn, _, _ in rows(doc)} >= {
        (MC, "00"), (MC, "09"), (MAESTRO, "20"), (TEST, "20")}
    # Kernel 8 rows exist for the combinations the document lists (Table 20).
    assert (MC, "09", "08") in keys and (TEST, "09", "08") in keys
    assert (MAESTRO, "09", "08") not in keys


# ---------------------------------------------------------------------------
# Table 16 defaults (PAGE 38-40) -- corrections the reference library missed
# ---------------------------------------------------------------------------

def test_mc_defaults_match_table_16():
    doc = load("PPS_MChip1")
    for txn in ("00", "01", "09", "17", "20"):
        for values in pick_all(doc, MC, txn):
            assert values["DF19"] == "000000010000"     # floor limit 100.00
            assert values["DF20"] == "000000030000"     # contactless txn limit 300.00
            assert values["DF8124"] == "000000030000"   # Reader CTL (no CDCVM)
            assert values["DF8125"] == "000000050000"   # Reader CTL (CDCVM)
    # Reader CVM Required Limit: 10.00 for 00/93, 200.00 otherwise (MC)
    assert pick(doc, MC, "00")["DF21"] == "000000001000"
    for txn in ("01", "09", "17", "20"):
        for values in pick_all(doc, MC, txn):
            assert values["DF21"] == "000000020000", txn
    # Terminal Type: 22 for 00/09/20, 14 for 01, 11 for 17 -- all AIDs
    for aid in (MC, MAESTRO, TEST):
        for row in pick_all(doc, aid, "17"):
            assert row["9F35"] == "11", aid
        for row in pick_all(doc, aid, "01"):
            assert row["9F35"] == "14", aid
        for row in pick_all(doc, aid, "00"):
            assert row["9F35"] == "22", aid
    # Mag-stripe CVM capability: txn 17 belongs to Maestro, not Mastercard
    for row in pick_all(doc, MC, "17"):
        assert row["DF812C"] == "00"
        assert row["DF811E"] == "20"
    for row in pick_all(doc, MAESTRO, "17"):
        assert row["DF812C"] == "F0"
        assert row["DF811E"] == "F0"
    # Terminal Risk Management (9F1D): Maestro keeps its own value
    assert pick(doc, MAESTRO, "00")["9F1D"] == "44FF800000000000"
    assert pick(doc, MC, "00")["9F1D"] == "6CFF000000000000"
    assert "9F1D" not in pick_all(doc, MC, "09")[0]
    # Card Data Input Capability '00' is terminal level, not per row
    term = term_map(doc)
    assert term["DF8117"] == "00"
    assert term["9F1A"] == "0056"
    assert term["DF810C"] == "02"
    assert term["9F6D"] == "0001"
    assert term["DF811A"] == "9F6A04"
    assert term["9F4E"] == ""            # Merchant Name: no value, IsEmpty
    assert "9F01" not in term and "9F16" not in term and "9F1C" not in term


# ---------------------------------------------------------------------------
# Table 18 deltas (PAGE 44-56)
# ---------------------------------------------------------------------------

def test_cvm_1_sets_both_reader_limits_to_1000():
    doc = load("PPS_CVM_1")     # PAGE 44
    for aid in (MC, MAESTRO, TEST):
        for txn in ("00", "01", "09", "17", "20"):
            for values in pick_all(doc, aid, txn):
                assert values["DF8124"] == "000000100000", (aid, txn)
                assert values["DF8125"] == "000000100000", (aid, txn)


def test_limit_2_test_aid_transaction_limit():
    doc = load("PPS_Limit_2")   # PAGE 47
    for txn in ("00", "01", "09", "17", "20"):
        for values in pick_all(doc, TEST, txn):
            assert values["DF8124"] == "000000010000", txn


def test_limit_3_maestro_on_device_limit_and_test_kernel_config():
    doc = load("PPS_Limit_3")   # PAGE 47-48
    assert pick(doc, MAESTRO, "00")["DF8125"] == "000000001000"
    assert pick(doc, TEST, "00")["DF811B"] == "A0"   # EMV only if MagStripe


def test_mc_atm_covers_transaction_type_93_for_every_aid():
    doc = load("PPS_MC_ATM")    # PAGE 48 + CNF-03 PAGE 31
    for aid in (MC, MAESTRO, TEST):
        values = pick(doc, aid, "93")
        assert values["9F40"] == "8000000000"        # Additional Terminal Cap. B1=80
        assert values["9F35"] == {"A0000000041010": "14",
                                  "A0000000043060": "15",
                                  "B012345678": "16"}[aid]


def test_mc_chip6_sets_rrp_bit_for_test_aid():
    doc = load("PPS_MChip6")    # PAGE 50
    assert pick(doc, TEST, "00")["DF811B"] == "30"    # 20 | RRP bit
    assert pick(doc, TEST, "00")["DF11"] == "0040000000"
    assert pick(doc, MC, "00")["9F35"] == "34"


def test_mc_chip11_mastercard_terminal_type_36():
    doc = load("PPS_MChip11")   # PAGE 51
    assert pick(doc, MC, "00")["9F35"] == "36"
    assert pick(doc, MC, "00")["DF12"] == "FFFFFFFFFF"
    assert pick(doc, MC, "00")["DF11"] == "0000000001"
    assert pick(doc, MAESTRO, "00")["DF11"] == "0000000002"


def test_mc_prop_carries_proprietary_action_tags():
    doc = load("PPS_MC_prop")   # PAGE 49
    term = term_map(doc)
    assert term["DF8309"] == "9F01"                  # 9F01 not present
    assert term["DF8308"] == "9F05DF30"              # 9F05/DF30 present, no value
    assert "810400000000" in term["DF830A"]          # proprietary '81', any value
    assert "CF03778899" in term["DF830A"]            # CF = 778899


def test_perf_mc_carries_rrp_bit_and_max_grace():
    doc = load("PPS_perf_MC")   # PAGE 54
    assert pick(doc, MC, "00")["DF811B"] == "30"
    assert pick(doc, MAESTRO, "00")["DF811B"] == "B0"
    for txn in ("00", "01", "09", "17", "20"):
        for values in pick_all(doc, MC, txn):
            assert values["DF19"] == "000000900000"
            assert values["DF21"] == "000000900000"
            assert values["9F35"] == "26"
            assert values["9F40"] == "6000F00001"
    assert term_map(doc)["DF8133"] == "01C2"        # RRPrMaxGrace


@pytest.mark.parametrize(("config_id", "tag", "value"), [
    ("PPS_RRP2", "DF8134", "0FA0"),                 # RRPrExpectedCAPDU
    ("PPS_RRP3", "DF8135", "0FA0"),                 # RRPrExpectedRAPDU
    ("PPS_RRP4", "DF8132", "0FA0"),                 # RRPrMinGrace
    ("PPS_RRP5", "DF8133", "0960"),                 # RRPrMaxGrace
    ("PPS_RRP6", "DF8136", "0FA0"),                 # RRPrAccuracyThreshold
    ("PPS_RRP7", "DF8135", "0000"),
])
def test_rrp_family_thresholds(config_id, tag, value):
    assert term_map(load(config_id))[tag] == value   # PAGE 54


def test_select1_and_spi_select_include_cirrus():
    for config_id in ("PPS_Select1", "PPS_SPI_Select"):
        aids = {aid for aid, _, _, _ in rows(load(config_id))}
        assert "A0000000046000" in aids, config_id


def test_trx_type_other_adds_types_21_and_02():
    doc = load("PPS_TrxTypeOther")  # PAGE 56
    assert pick(doc, MC, "21")["DF19"] == pick(doc, MC, "00")["DF19"]
    maestro_02 = pick(doc, MAESTRO, "02")
    assert maestro_02["DF21"] == "000000025000"     # 250.00


# ---------------------------------------------------------------------------
# Table 17 deltas (PAGE 41-44)
# ---------------------------------------------------------------------------

def test_ms_ht0_message_hold_time():
    assert term_map(load("PPS_MS_HT0"))["DF812D"] == "000000"   # PAGE 41


def test_ms_limit_1_amounts():
    doc = load("PPS_MS_Limit_1")    # PAGE 41
    expectations = {
        MC: ("000000900000", "000000600000", "000000005150", "000000010000"),
        MAESTRO: ("000000900000", "000000001000", "000000007500",
                  "000000020000"),
        TEST: ("000000900000", "000000700000", "000000010250",
               "000000015000"),
    }
    for aid, (ctl, cdcvm, cvm_req, floor) in expectations.items():
        values = pick(doc, aid, "00")
        assert values["DF8124"] == ctl, aid
        assert values["DF8125"] == cdcvm, aid
        assert values["DF21"] == cvm_req, aid
        assert values["DF19"] == floor, aid


def test_ms_limit_3b_mastercard_magstripe_only():
    doc = load("PPS_MS_Limit_3b")   # PAGE 42
    assert pick(doc, MC, "00")["DF811B"] == "40"
    assert pick(doc, MC, "00")["DF8124"] == "000000002000"


def test_ms_prop_proprietary_tags():
    doc = load("PPS_MS_prop")       # PAGE 43
    term = term_map(doc)
    assert term["DF8308"] == "9F059F17"     # 9F05 / 9F17 present, no value
    assert term["DF830A"] == "810400000000"  # proprietary '81', any value
    assert "9F1C" not in term               # Terminal Identification absent


def test_no_default_1_drops_the_always_class_objects():
    doc = load("PPS_NoDefault_1")   # PAGE 43
    term = term_map(doc)
    for tag in ("DF811A", "DF810C", "9F6D", "9F1A", "DF811B", "DF811E",
                "DF812C", "DF812D", "DF8124", "DF8125", "DF21"):
        assert tag not in term, tag
    for _, _, _, values in rows(doc):
        for tag in ("DF811B", "DF811E", "DF812C", "DF8124", "DF8125", "DF21"):
            assert tag not in values, tag


def test_no_default_2_readds_the_documented_objects():
    doc = load("PPS_NoDefault_2")   # PAGE 43-44
    for aid in (MC, MAESTRO, TEST):
        values = pick(doc, aid, "00")
        assert values["DF8124"] == "000000010000", aid
        assert values["DF8125"] == "000000010000", aid
    assert pick(doc, MAESTRO, "00")["DF21"] == "000000005000"
    assert pick(doc, MAESTRO, "00")["DF811B"] == "60"
    assert "9F1E" not in term_map(doc)   # Interface Device Serial Number absent
