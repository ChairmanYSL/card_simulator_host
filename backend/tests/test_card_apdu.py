"""Tests for the card APDU judging view (judge/card_apdu.py).

The tag rule is the one the kernel uses (emvtlv.c: has_subsequent on the first
byte, then b8-continuation), and the GPO/READ RECORD views are what the
GPO-PROC71/151 cases assert against.
"""

from __future__ import annotations

from emv_host.judge.card_apdu import (_read_tag, build_card_view, parse_tlv,
                                      split_template)

#: The PDOL of the GPO-PROC151-XP2 reference card (52 entries, 100 bytes).
PDOL_151 = ("9F6E049F35019F370495059F34039F02069F03069F50029F51019F52019F5301"
            "9F54019F55019F56019F57019F58019F59019F5A089F5B069F5C019F5D019F5E01"
            "9F5F019F60019F61019F62019F63019F64019F65019F66019F68019F69019F6A01"
            "9F6B019F6C019F6F049F70029F71039F72019F73019F74019F75019F76019F7801"
            "9F79089F7A019F7B019F7C019F7E019F7F01BF1001BF1101")


def tlv(tag: str, value_hex: str) -> str:
    raw = bytes.fromhex(value_hex)
    length = f"{len(raw):02X}" if len(raw) < 0x80 else "81" + f"{len(raw):02X}"
    return tag + length + value_hex


def command(cla: str, ins: str, p1: str, p2: str, data_hex: str = "") -> dict:
    lc = f"{len(data_hex) // 2:02X}" if data_hex else ""
    return {"event": "apdu", "direction": "COMMAND", "cla": cla, "ins": ins,
            "p1": p1, "p2": p2, "data_hex": cla + ins + p1 + p2 + lc + data_hex + "00"}


def response(data_hex: str) -> dict:
    return {"event": "apdu", "direction": "RESPONSE", "data_hex": data_hex + "9000"}


def test_read_tag_follows_the_kernel_rule():
    assert _read_tag("9F6E04", 0) == ("9F6E", 4)
    assert _read_tag("5F2D04", 0) == ("5F2D", 4)      # not "5F" + len 2D
    assert _read_tag("9F5F01", 0) == ("9F5F", 4)      # not the 3-byte 9F5F01
    assert _read_tag("BF0C01", 0) == ("BF0C", 4)
    assert _read_tag("DF8101FF", 0) == ("DF8101", 6)  # 3 bytes, b8-continuation


def test_split_template_handles_the_long_pdol():
    entries = split_template(PDOL_151)
    assert len(entries) == 52
    assert sum(length for _tag, length in entries) == 100
    for expected in (("9F5B", 6), ("9F5F", 1), ("9F79", 8), ("BF10", 1), ("BF11", 1)):
        assert expected in entries


def test_parse_tlv_descends_into_the_fci():
    a5 = tlv("A5", tlv("50", "48454C4C4F") + tlv("9F38", "9F350122"))
    fci = tlv("6F", tlv("84", "A000000025010403") + a5)
    tlvs = parse_tlv(fci)
    assert tlvs["84"] == "A000000025010403"
    assert tlvs["50"] == "48454C4C4F"
    assert tlvs["9F38"] == "9F350122"


def test_build_card_view_gpo_tlv_format_and_record_map():
    gpo_response = tlv("77", tlv("82", "0880") + tlv("94", "08010100"))
    record_2_1 = tlv("70", tlv("8F", "0A") + tlv("90", "AA" * 8))
    trace = [
        command("00", "A4", "04", "00", "A00000002501040300"),
        response(tlv("6F", tlv("84", "A000000025010403")
                     + tlv("A5", tlv("9F38", "9F6E049F3501")))),
        # GPO command data: 83 <len> <9F6E=18000003><9F35=22>
        command("80", "A8", "00", "00", "8305" + "1800000322"),
        response(gpo_response),
        command("00", "B2", "01", "14"),
        response(record_2_1),
    ]
    view = build_card_view(trace)
    assert view["gpo"]["aip"] == "0880"
    assert view["gpo"]["afl"] == "08010100"
    assert view["gpo"]["pdol"]["9F6E"] == "18000003"
    assert view["gpo"]["pdol"]["9F35"] == "22"
    assert view["read_record"]["records"]["2"]["1"]["8F"] == "0A"
    assert view["read_record"]["records"]["2"]["1"]["90"] == "AA" * 8


def test_build_card_view_gpo_raw_format():
    # Format 1: 80 <len> <AIP><AFL>, no TLV coding (Book 3 6.5.8.4).
    trace = [
        command("80", "A8", "00", "00", "8306" + "1800000322"),
        response("8006" + "0880" + "08010100"),
    ]
    view = build_card_view(trace)
    assert view["gpo"]["aip"] == "0880"
    assert view["gpo"]["afl"] == "08010100"
