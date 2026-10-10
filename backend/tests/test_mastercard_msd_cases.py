"""The two MSD (mag-stripe) cases of log ``3G06-1241-Case07-08``.

The reference log holds **one ACT and two taps**: the first tap is case Case07,
the second Case08.  Both are Kernel 2 mag-stripe mode (AIP=0200 → the card does
not support EMV mode), so the terminal answers ``80 2A 8E 80`` (MSD AC) instead
of ``80 AE ...``; only the card's MSD AC response differs:

* Case07 -> ``77{9F36, DF4B=000100}``: DF4B asks for another tap -> the kernel
  ends the application with ``Start=B`` and an MSD data record;
* Case08 -> ``77{9F61=EB85, 9F36, DF4B=000000}``: no tap request, so the CVC3
  path runs and the amount (10.01) above the CVM required limit (10.00) raises
  ``L2 = CARD_DATA_ERROR``.

The card data is identical in both cases; only the MSD AC response differs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from emv_host.cases import load_library
from emv_host.cases.models import MatchRule
from emv_host.judge.asserter import assert_result
from emv_host.judge.card_apdu import build_card_view
from emv_host.judge.outcome import build_result

REPO = Path(__file__).resolve().parents[2]
CASE_DIR = REPO / "cases" / "mastercard" / "contactless" / "cases"
CASE_IDS = ("3G06-1241-Case07", "3G06-1241-Case08")

# --- the reference log's wire data -----------------------------------------
PPSE_RESPONSE = ("6F3D840E325041592E5359532E4444463031A52BBF0C28610C4F07"
                 "A0000000041010870101610C4F07A0000000043060870102610A4F05"
                 "B012345678870103" "9000")
SELECT_ADF_RESPONSE = ("6F1A8407A0000000041010A50F500A4D415354455243415244"
                       "870101" "9000")
GPO_RESPONSE = ("771682020200941008010100100101011801010020010200" "9000")
SFI1_RECORD = ("70249F6502000E9F66020E709F6B135413339000001513D491210190009900"
               "00000F9F670103" "9000")
MSD_AC_RESPONSE_C07 = "77" "0B" "9F36020105" "DF4B03" "000100" "9000"
MSD_AC_RESPONSE_C08 = "7710" "9F6102EB85" "9F36020105" "DF4B03" "000000" "9000"

OPS_C07 = "4010F03070F0FF00"
OPS_C08 = "40F0F0F090F0FF0D"
DATA_RECORD_C07 = ("500A4D415354455243415244" "8407A0000000041010"
                   "9F6D020001" "9F6B135413339000001513D49121019000990000000F")
DISC_DATA_C07 = "DF812B079000990000000F" "DF811506" "0000000000FF"
UIRD_C08 = "1C000000130000000000000000"
DISC_DATA_C08 = "DF812B079000990000000F" "DF811506" "00060000001C"


def _trace(msd_ac_response: str) -> list[dict]:
    """The APDU stream of one MSD transaction (from the reference log)."""
    pairs = [
        ("00", "A4", "04", "00", "00A404000E325041592E5359532E444446303100",
         PPSE_RESPONSE),
        ("00", "A4", "04", "00", "00A4040007A000000004101000",
         SELECT_ADF_RESPONSE),
        ("80", "A8", "00", "00", "80A8000002830000", GPO_RESPONSE),
        ("00", "B2", "01", "0C", "00B2010C00", SFI1_RECORD),
        ("80", "2A", "8E", "80", "802A8E80040000023100", msd_ac_response),
    ]
    trace: list[dict] = []
    for cla, ins, p1, p2, command, response in pairs:
        trace.append({"event": "apdu", "direction": "COMMAND", "cla": cla,
                      "ins": ins, "p1": p1, "p2": p2, "data_hex": command})
        trace.append({"event": "apdu", "direction": "RESPONSE", "cla": cla,
                      "ins": ins, "p1": p1, "p2": p2, "data_hex": response})
    return trace


def _case(case_id: str) -> dict:
    return json.loads((CASE_DIR / f"{case_id}.json").read_text(encoding="utf-8"))


def _out(ops: str, datarecord: str = "", discdata: str = "", uird: str = "",
         trace: list[dict] | None = None) -> dict:
    signal = [{"id": "OPS", "value": ops}]
    if datarecord:
        signal.append({"id": "DataRecord", "value": datarecord})
    if discdata:
        signal.append({"id": "DiscData", "value": discdata})
    if uird:
        signal.append({"id": "UIRD", "value": uird})
    return build_result(signal, card=build_card_view(trace) if trace else None)


def _verdict(case: dict, **kwargs) -> object:
    rules = [MatchRule.model_validate(r) for r in case["expected_results"]]
    return assert_result(_out(**kwargs), rules)


# ---------------------------------------------------------------------------
# the two case files
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case_id", CASE_IDS)
def test_case_loads_and_uses_the_log_config(case_id):
    case = _case(case_id)
    assert case["case_id"] == case_id
    assert case["kernel_params_ref"] == "PPS_MChip1"
    profile = case["profile"]
    assert profile["aip"] == "0200", "AIP byte2 bit8=0 -> MSD mode selected"
    assert profile["afl"] == "08010100100101011801010020010200"
    assert "pdol" not in profile, "the log's FCI carries no 9F38"
    assert [a["aid"] for a in profile["aids"]] == [
        "A0000000041010", "A0000000043060", "B012345678"]
    assert [a["priority"] for a in profile["aids"]] == [1, 2, 3]
    assert case["transaction"]["act"][1] == {"id": "9F02",
                                             "value": "000000001001"}
    # negotiation A (2026-10-10): the explicit MSD switch travels in the profile
    assert profile["msdMode"] == "msd"
    assert case["profile"]["msdMode"] == case["profile"]["msdMode"].lower()
    # track2 fields are hex-of-ASCII (the existing profile convention)
    assert bytes.fromhex(profile["msdTrack2Data"]).decode() == \
        "5413339000001513=49121019000990000000"
    assert profile["track2EquivalentData"] == profile["msdTrack2Data"]

    library = load_library(REPO / "cases")
    assert not [i for i in library.issues if i.severity == "error"]
    loaded = library.get_case("mastercard", "contactless", case_id)
    assert loaded is not None
    assert loaded.kernel_params_ref == "PPS_MChip1"
    # extras must survive the model dump, else msdMode/msd* never reach the card
    dump = loaded.profile.model_dump(mode="json")
    assert dump["msdMode"] == "msd" and dump["msdTrack2Data"] == \
        profile["msdTrack2Data"]

    # PPS_MChip1 must keep the two values that drive this scenario
    conf = json.loads((REPO / "cases" / "mastercard" / "pos_configs"
                       / "PPS_MChip1.json").read_text(encoding="utf-8"))
    row = next(row for row in conf["aids"]
               if row["aid"].upper() == "A0000000041010"
               and next(f["value"] for f in row["fields"]
                        if f["tag"] == "9C") == "00")
    fields = {f["tag"]: f["value"] for f in row["fields"]}
    assert fields["DF811B"] == "20", \
        "DF811B=20 keeps MSD mode available to the terminal"
    assert fields["DF21"] == "000000001000", \
        "the CVM required limit 10.00 is what Case08 exceeds with 10.01"
    assert fields["9F35"] == "22" and fields["DF19"] == "000000010000"


def test_the_two_cases_differ_only_in_the_msd_ac_response():
    first, second = (_case(cid)["profile"] for cid in CASE_IDS)
    assert first["msdPosInteractionInfo"] == "000100"
    assert second["msdPosInteractionInfo"] == "000000"
    assert first["msdCvc3Track2"] == ""
    assert second["msdCvc3Track2"] == "EB85"
    skip = {"id", "name", "description", "msdPosInteractionInfo",
            "msdCvc3Track2"}
    assert {k: v for k, v in first.items() if k not in skip} == \
           {k: v for k, v in second.items() if k not in skip}
    assert first["records"] == second["records"]


def test_msd_ac_order_is_locked_by_a_regex_rule():
    """Negotiation D: the TLV order is judged, the ATC stays free."""
    for case_id, pattern in (
            (CASE_IDS[0], r"^770B9F3602[0-9A-F]{4}DF4B030001009000$"),
            (CASE_IDS[1], r"^77109F6102EB859F3602[0-9A-F]{4}DF4B030000009000$")):
        rules = [MatchRule.model_validate(r)
                 for r in _case(case_id)["expected_results"]
                 if r["path"] == "card.gac.response"]
        assert len(rules) == 1 and rules[0].op == "regex"
        assert rules[0].expect == pattern
        assert rules[0].severity == "pass", "顺序是验收口径，不能只 warn"


def test_msd_ac_order_mismatch_fails():
    """The reordered response (my earlier notice text) must FAIL Case08."""
    reordered = "77109F36020105DF4B03000000"          # 9F36 before DF4B, no 9F61
    response = "77" + reordered[2:]
    verdict = _verdict(_case(CASE_IDS[1]), ops=OPS_C08, uird=UIRD_C08,
                       discdata=DISC_DATA_C08,
                       trace=_trace(response + "9000"))
    assert verdict.status == "FAIL"
    assert any(r.rule.path == "card.gac.response" and not r.ok
               for r in verdict.rules)


def test_msd_mode_whitelist_warns_on_unknown_values():
    from emv_host.cases.models import CardProfile
    from emv_host.cases.validation import validate_profile

    ok = validate_profile(CardProfile.model_validate(
        {"id": "x", "msdMode": "msd", "pan": "5413339000001513"}))
    assert not [i for i in ok.issues if "msdMode" in i.message]
    bad = validate_profile(CardProfile.model_validate(
        {"id": "x", "msdMode": "magnet", "pan": "5413339000001513"}))
    assert any(i.severity == "warning" and "msdMode" in i.message
               for i in bad.issues)


def test_ccc_is_a_registered_silence_command():
    """Negotiation B: ``CCC`` is canonical, ``MSD_AC`` the accepted alias."""
    from emv_host.cases.validation import _NO_RESPONSE_COMMANDS
    assert {"CCC", "MSD_AC"} <= _NO_RESPONSE_COMMANDS
    assert len(_NO_RESPONSE_COMMANDS) == 9


def test_msd_ac_is_visible_in_the_card_view():
    """Host side: ``card.gac`` also carries the MSD AC (INS 2A)."""
    view = build_card_view(_trace(MSD_AC_RESPONSE_C07))
    assert view["gac"]["mode"] == "msd"
    assert view["gac"]["command"] == "802A8E80040000023100"
    assert view["gac"]["response"] == MSD_AC_RESPONSE_C07
    assert view["gac"]["cdol"] == {}, "MSD has no CDOL1 data"
    assert view["select_aid"]["pdol"] == "", "the log's FCI has no PDOL"
    assert view["gpo"]["aip"] == "0200"
    assert view["read_record"]["records"]["1"]["1"]["9F6B"] == \
        "5413339000001513D49121019000990000000F"


# ---------------------------------------------------------------------------
# pass criteria against the log's OUTs
# ---------------------------------------------------------------------------

def test_case07_passes_on_the_log_out():
    verdict = _verdict(_case("3G06-1241-Case07"), ops=OPS_C07,
                       datarecord=DATA_RECORD_C07, discdata=DISC_DATA_C07,
                       trace=_trace(MSD_AC_RESPONSE_C07))
    assert verdict.status == "PASS", [r.message for r in verdict.failed]


def test_case08_passes_on_the_log_out():
    verdict = _verdict(_case("3G06-1241-Case08"), ops=OPS_C08, uird=UIRD_C08,
                       discdata=DISC_DATA_C08,
                       trace=_trace(MSD_AC_RESPONSE_C08))
    assert verdict.status == "PASS", [r.message for r in verdict.failed]


def test_case07_fails_without_the_second_tap_request():
    """A plain END APPLICATION (Start=A, no data record) must not pass."""
    verdict = _verdict(_case("3G06-1241-Case07"), ops="40F0F0F090F0FF00")
    assert verdict.status == "FAIL"
    failed = {r.rule.path for r in verdict.rules if not r.ok
              and r.rule.severity != "warn"}
    assert {"out.ops.start", "out.ops.data_record_present",
            "out.datarecord.9F6B"} <= failed


def test_case08_fails_when_the_terminal_reports_no_error():
    """END APPLICATION with a data record and no UI request is not the log."""
    verdict = _verdict(_case("3G06-1241-Case08"), ops="40F0F0F030F0FF00",
                       datarecord=DATA_RECORD_C07, discdata=DISC_DATA_C07)
    assert verdict.status == "FAIL"
    failed = {r.rule.path for r in verdict.rules if not r.ok
              and r.rule.severity != "warn"}
    assert {"out.ops.ui_request_on_outcome", "out.ops.data_record_present",
            "out.uird"} <= failed
