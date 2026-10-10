"""The ATM cash case ``3M08-5132A`` (log ``3M08-5132A(PPS_MC_ATM).txt``).

The reference log is a TEI run without a CONFIG push (the reader used its
built-in ``PPS_MC_ATM``) and holds **one complete transaction plus the start of
the retry**:

* tap 1: the card does not answer GENERATE AC at all (``GAC SW = FFFFFFFF``,
  i.e. timeout) -> ``DF8115 = 01 00 00 00 00 21`` (L1 = timeout) + ``DF812D``
  -> ``outcome_try_again_end_application`` (the OUT bytes themselves are not
  printed by that firmware, only ``signal.data_count = 3``);
* tap 2: cut off after ``EMV_PreProcess`` -> its outcome is **not** in the log.

Per the delivery ruling the case covers both taps: tap 1 = TRY AGAIN (log
evidence), tap 2 = ONLINE REQUEST (host-defined: the reader requests ARQC,
P1=0x80, and TVR byte1 b8 hits the card's IAC-Online b8).  The nine
reader-side rules (GPO/GAC command data) are taken from the log byte-for-byte.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from emv_host.cases import load_library
from emv_host.cases.models import CardProfile, MatchRule, RecordConfig
from emv_host.judge.asserter import assert_result
from emv_host.judge.card_apdu import build_card_view
from emv_host.judge.outcome import build_result

REPO = Path(__file__).resolve().parents[2]
CASE_PATH = REPO / "cases" / "mastercard" / "contactless" / "cases" / "3M08-5132A.json"
CONF_PATH = REPO / "cases" / "mastercard" / "pos_configs" / "PPS_MC_ATM.json"
CASE_ID = "3M08-5132A"

# --- the reference log's wire data (verbatim) ------------------------------
PPSE = "6F20840E325041592E5359532E4444463031A50EBF0C0B61094F07A00000000410109000"
FCI = ("6F288407A0000000041010A51D870101500A4D4153544552434152449F380B"
       "9F02069F33039C019F03069000")
GPO_CMD = "80A800001283100000000015000000080100000000100000"
GPO_RESP = "771282021880940C0801010010010201180102009000"
SFI21 = ("7081BC8F01F19F32010392009081B08628B3C6BCB6204C958A1A3DA2418CBA269C412389"
         "B43BA6FDDCD5D8BD5D03D4525438A52BFD816F05D4E54DAF946279ADD379E43C061CFA51"
         "4A922CD3C49EFFA66418D0B106D25EB350210AD6D6554CC7455ED3357C0BC235EEB22C36"
         "DC64451950C64AE8A33FC29F5B577119701B274CC11DBB1BA24E310620049B0D25251CFD"
         "D534567EB1E31C57FF91E577B4F2AB71FB60A7963346686D38BB6EC027963CDA3F102336"
         "EBF8E025030AAEDCD1AAD19000")
SFI22 = "70009000"                      # the empty record (fields: [])
SFI31 = "700E5A0854133390000015135F3401009000"
SFI32 = ("707757115413339000001513D491220101234076455F24034912315F25031201018C249F"
         "02069F03069F1A0295055F2A029A039C019F33039F37049F35019F45029F34039F40058E"
         "0E00000000000000005E0342031F039F070202009F080200029F0D05F0508400009F0E05"
         "00000000009F0F05F070848000" "9000")
GAC_CMD = ("80AE80002B0000000015000000000010000056800000000109782601210100"
           "080857A80BCE1400001F0302800000000000")

#: The log does not print the OUT frames.  Tap 1's status is known from
#: ``outcome_try_again_end_application``; tap 2's shape mirrors the online
#: request measured in the 3B04-0020 reference (ARQC -> CID 80 -> resp_code 00).
OPS_TRY_AGAIN = "7010F0F010F0FF00"
OPS_ONLINE = "30F0F010B8F0FF00"
DATA_RECORD = ("9F0206000000001500" "9F0306000000001000" "9F2608B7BF643B823DADAE"
               "82021880" "9F36020105" "9F270180" "9F34031F0302"
               "8407A0000000041010")
DISC_DATA_TRY_AGAIN = "DF811506010000000021"
UIRD_ONLINE = "1B000000000000000000000000"


def _case() -> dict:
    return json.loads(CASE_PATH.read_text(encoding="utf-8"))


def _trace(gac_response: str | None) -> list[dict]:
    """The log's APDU stream; ``gac_response=None`` = the silent first tap."""
    pairs = [
        ("00", "A4", "04", "00", "00A404000E325041592E5359532E444446303100", PPSE),
        ("00", "A4", "04", "00", "00A4040007A000000004101000", FCI),
        ("80", "A8", "00", "00", GPO_CMD, GPO_RESP),
        ("00", "B2", "01", "14", "00B2011400", SFI21),
        ("00", "B2", "02", "14", "00B2021400", SFI22),
        ("00", "B2", "01", "1C", "00B2011C00", SFI31),
        ("00", "B2", "02", "1C", "00B2021C00", SFI32),
        ("80", "AE", "80", "00", GAC_CMD, gac_response),
    ]
    trace: list[dict] = []
    for cla, ins, p1, p2, command, response in pairs:
        entry = {"event": "apdu", "direction": "COMMAND", "cla": cla, "ins": ins,
                 "p1": p1, "p2": p2, "data_hex": command}
        if response is None:
            entry["no_response"] = True
        trace.append(entry)
        if response is not None:
            trace.append({"event": "apdu", "direction": "RESPONSE", "cla": cla,
                          "ins": ins, "p1": p1, "p2": p2,
                          "data_hex": response})
    return trace


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


def _verdict(outs: list[dict], card: dict | None = None) -> object:
    from emv_host.judge.outcome import compose_result

    rules = [MatchRule.model_validate(r) for r in _case()["expected_results"]]
    return assert_result(compose_result(outs, card=card), rules)


# ---------------------------------------------------------------------------
# the case file
# ---------------------------------------------------------------------------

def test_case_uses_the_atm_config_and_log_data():
    case = _case()
    assert case["case_id"] == CASE_ID
    assert case["kernel_params_ref"] == "PPS_MC_ATM"
    profile = case["profile"]
    assert profile["aip"] == "1880", "log GPO: 82=1880 (no SDA/DDA/CDA)"
    assert profile["afl"] == "080101001001020118010200"
    assert profile["pdol"] == "9F02069F33039C019F0306"
    assert profile["acBehavior"] == "online_request"
    assert [a["aid"] for a in profile["aids"]] == ["A0000000041010"]
    # tap 1 = the card is silent on GENERATE AC, tap 2 answers
    assert [(n["command"], n["times"]) for n in profile["noResponseCommands"]] == \
        [("GENERATE_AC", 1)]
    # track2 is hex-of-ASCII and repacks to the log's tag 57
    ascii_track2 = bytes.fromhex(profile["track2EquivalentData"]).decode()
    assert ascii_track2 == "5413339000001513=49122010123407645"
    packed = ascii_track2.replace("=", "D")
    assert packed == SFI32[8:8 + 34], "log tag 57"
    # the certificate is copied verbatim (176B) and the record map matches the log
    assert len(profile["issuerPublicKeyCertificate"]) // 2 == 176
    assert SFI21[30:30 + 352] == profile["issuerPublicKeyCertificate"]
    records = {(r["sfi"], r["record"]): r["fields"] for r in profile["records"]}
    assert set(records) == {(2, 1), (2, 2), (3, 1), (3, 2)}
    assert records[(2, 2)] == [], "log SFI 2/2 is an empty record (70 00)"
    assert records[(3, 1)] == ["pan", "panSequenceNumber"]
    assert "cdol1" in records[(3, 2)] and "cvmList" in records[(3, 2)]
    assert case["transaction"]["act"][-1] == {"id": "9F35", "value": "14"}
    assert case["transaction"]["act"][11] == {"id": "9C", "value": "01"}


def test_case_loads_and_the_config_carries_the_atm_deltas():
    library = load_library(REPO / "cases")
    assert not [i for i in library.issues if i.severity == "error"]
    loaded = library.get_case("mastercard", "contactless", CASE_ID)
    assert loaded is not None and loaded.kernel_params is not None
    conf = json.loads(CONF_PATH.read_text(encoding="utf-8"))
    row = next(row for row in conf["aids"]
               if row["aid"].upper() == "A0000000041010"
               and next(f["value"] for f in row["fields"] if f["tag"] == "9C") == "00")
    fields = {f["tag"]: f["value"] for f in row["fields"]}
    # doc PAGE 48: PPS_MC_ATM = PPS_MChip1 except ATC B1=80 and MC TT=14
    assert fields["9F40"] == "8000000000" and fields["9F35"] == "14"
    assert fields["DF811B"] == "20"


def test_empty_record_is_expressible():
    """``fields: []`` = an empty record (log SFI 2/2 `70 00`), relaxed today."""
    record = RecordConfig.model_validate({"sfi": 2, "record": 2, "fields": []})
    assert record.fields == []
    profile = CardProfile.model_validate(
        {"id": "x", "pan": "5413339000001513",
         "records": [{"sfi": 2, "record": 2, "fields": []}]})
    assert profile.records[0].fields == []
    with pytest.raises(Exception):
        RecordConfig.model_validate({"sfi": 0, "record": 1, "fields": []})


# ---------------------------------------------------------------------------
# reader-side evidence (nine rules straight from the log)
# ---------------------------------------------------------------------------

def test_card_view_reproduces_the_logs_commands():
    view = build_card_view(_trace(None))          # tap 1: no GAC response
    assert view["gpo"]["pdol"]["9F33"] == "000008"
    assert view["gpo"]["pdol"]["9C"] == "01"
    assert view["gpo"]["pdol"]["9F02"] == "000000001500"
    assert view["gpo"]["pdol"]["9F03"] == "000000001000"
    assert view["gpo"]["aip"] == "1880"
    assert view["gac"]["command"] == GAC_CMD
    assert view["gac"]["mode"] == "emv"
    assert view["gac"]["cdol"]["9F33"] == "000808", \
        "the GAC sets the no-CVM-required bit that the GPO did not"
    assert view["gac"]["cdol"]["9F35"] == "14"
    assert view["gac"]["cdol"]["9F40"] == "8000000000"
    assert view["gac"]["cdol"]["9C"] == "01"
    assert view["gac"]["response"] == "", "tap 1: the card stayed silent"
    assert view["read_record"]["records"]["2"]["2"] == {"70": ""}, \
        "log SFI 2/2 is an empty record (70 00)"


# ---------------------------------------------------------------------------
# pass criteria
# ---------------------------------------------------------------------------

def test_case_passes_on_the_logged_two_tap_sequence():
    trace = _trace(None)
    first = _out(OPS_TRY_AGAIN, discdata=DISC_DATA_TRY_AGAIN, trace=trace)
    # tap 2: the card answers ARQC (the reader asked P1=0x80)
    arqc = "7781979F2701809F360201059F10120110800009240000000011223344556611FF9000"
    trace2 = _trace(arqc)
    second = _out(OPS_ONLINE, datarecord=DATA_RECORD, uird=UIRD_ONLINE,
                  discdata="9F42020978DF8115060000000000FF", trace=trace2)
    verdict = _verdict([first["out"], second["out"]], card=first["card"])
    assert verdict.status == "PASS", [r.message for r in verdict.failed]


def test_case_fails_when_the_terminal_type_is_not_the_atm_value():
    """The ATM terminal type must reach the GENERATE AC CDOL1 data."""
    # ``card.gac`` exposes the *first* GENERATE AC of the attempt (tap 1 here),
    # so break that one to prove the rule is not vacuous.
    broken = [dict(entry) for entry in _trace(None)]
    for entry in broken:
        if entry.get("ins") == "AE" and entry.get("direction") == "COMMAND":
            command = entry["data_hex"]
            assert command[74:76] == "14", "sanity: 9F35 sits at offset 74"
            entry["data_hex"] = command[:74] + "22" + command[76:]
    view = build_card_view(broken)
    assert view["gac"]["cdol"]["9F35"] == "22", "the rewrite must be visible"
    first = _out(OPS_TRY_AGAIN, discdata=DISC_DATA_TRY_AGAIN, trace=broken)
    second = _out(OPS_ONLINE, datarecord=DATA_RECORD, uird=UIRD_ONLINE)
    verdict = _verdict([first["out"], second["out"]], card=first["card"])
    assert verdict.status == "FAIL"
    assert any(r.rule.path == "card.gac.cdol.9F35" and not r.ok
               for r in verdict.rules)


def test_case_fails_when_the_retry_is_declined():
    first = _out(OPS_TRY_AGAIN, discdata=DISC_DATA_TRY_AGAIN,
                 trace=_trace(None))
    second = _out("20F0F000B0F0FF00", datarecord=DATA_RECORD,
                  discdata="9F42020978DF8115060000000020FF")
    verdict = _verdict([first["out"], second["out"]], card=first["card"])
    assert verdict.status == "FAIL"
    failed = {r.rule.path for r in verdict.rules if not r.ok
              and r.rule.severity != "warn"}
    assert {"outcome", "resp_code", "outs.1.ops.status"} <= failed
