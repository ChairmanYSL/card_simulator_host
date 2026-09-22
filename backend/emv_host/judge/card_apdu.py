"""Build the ``card`` view consumed by ``card.*`` assertions (Q2=b, 2026-09-20).

The Android card side reports every APDU over the protocol-v2 trace channel::

    log{level:"trace", case_id, attempt_id,
        message:{event:"apdu", direction:"COMMAND"|"RESPONSE",
                 cla, ins, p1, p2, data_hex, sw?, no_response?, elapsed_ms, seq}}

``data_hex`` holds the **full APDU** for a COMMAND (``CLA INS P1 P2 [Lc Data]``)
and the **response data followed by SW1SW2** for a RESPONSE.  See the card side
``InMemoryApduLogger`` (``formatHex(command.getRaw())`` /
``formatHex(response.getRaw())``).

This module turns that flat stream into a judgeable view so cases can assert on
what the terminal actually put on the wire, e.g.::

    {"path": "card.gpo.pdol.9F35", "op": "eq", "expect": "E2"}
    {"path": "card.gac.cdol.9F35", "op": "eq", "expect": "22"}

Layout::

    {
      "apdu_count": 12,
      "select_aid": {"command": "<hex>", "response": "<hex>",
                     "pdol": {...}, "cdol1": {...}, "afl": "..."},
      "gpo": {"command": "<hex>", "response": "<hex>", "pdol": {"9F35": "E2", ...}},
      "gac": {"command": "<hex>", "response": "<hex>", "cdol": {"9F35": "22", ...}},
      "commands": [{"cla": "00", "ins": "A4", "data": "...", "raw": "..."}, ...],
    }

The PDOL (``9F38``) and CDOL1 (``8C``) templates come from the card's SELECT AID
response, so the GPO / GENERATE AC command data can be split tag-by-tag.
"""

from __future__ import annotations

from typing import Any

#: PPSE DF names — used to tell the PPSE SELECT apart from the AID SELECT.
_PPSE_NAMES = ("325041592E5359532E4444463031", "315041592E5359532E4444463031")


def _apdu_fields(raw_hex: str) -> tuple[str, str, str, str, str]:
    """``CLA INS P1 P2 [Lc Data]`` -> (cla, ins, p1, p2, data)."""
    raw = (raw_hex or "").upper()
    cla, ins, p1, p2 = (raw[i:i + 2] for i in range(0, 8, 2))
    data = ""
    if len(raw) > 10:
        lc = int(raw[8:10], 16)
        data = raw[10:10 + lc * 2]
    return cla, ins, p1, p2, data


def parse_tlv(tlv_hex: str) -> dict[str, str]:
    """TLV -> {tag: value}; descends into constructed tags (6F / A5 / 70 / 77 ...).

    The card's FCI nests the data objects (``6F`` -> ``A5`` -> ``9F38``), so a
    flat scan would step over the PDOL/CDOL1.  Primitive tags keep their value;
    constructed ones are merged with their children (their own raw value is kept
    under the same tag so callers may also inspect the wrapper).
    """
    raw = (tlv_hex or "").upper()
    out: dict[str, str] = {}

    def walk(blob: str) -> None:
        i = 0
        while i + 4 <= len(blob):
            tag = blob[i:i + 2]
            i += 2
            # EMV: a tag continues while (byte & 0x1F) == 0x1F (9F / DF / 5F ...).
            while (int(tag[-2:], 16) & 0x1F) == 0x1F:
                if i + 2 > len(blob):
                    return
                tag += blob[i:i + 2]
                i += 2
            if i + 2 > len(blob):
                return
            length = int(blob[i:i + 2], 16)
            i += 2
            if length == 0x81 or length == 0x82:  # long form (1-2 length bytes)
                n = 1 if length == 0x81 else 2
                length = int(blob[i:i + n * 2], 16)
                i += n * 2
            value = blob[i:i + length * 2]
            if len(value) < length * 2:
                return
            i += length * 2
            out[tag] = value
            # bit 6 of the first tag byte marks a constructed object.
            if (int(tag[:2], 16) & 0x20) and value:
                walk(value)

    walk(raw)
    return out


def split_template(template_hex: str) -> list[tuple[str, int]]:
    """A DOL template (``9F3501 9F3704 ...``) -> ordered [(tag, length)]."""
    raw = (template_hex or "").upper()
    out: list[tuple[str, int]] = []
    i = 0
    while i + 4 <= len(raw):
        tag = raw[i:i + 2]
        i += 2
        while (int(tag[-2:], 16) & 0x1F) == 0x1F:
            if i + 2 > len(raw):
                return out
            tag += raw[i:i + 2]
            i += 2
        length = int(raw[i:i + 2], 16)
        i += 2
        out.append((tag, length))
    return out


def split_by_template(template_hex: str, data_hex: str) -> dict[str, str]:
    """Split one DOL data blob against its template -> {tag: value}."""
    data = (data_hex or "").upper()
    pos = 0
    got: dict[str, str] = {}
    for tag, length in split_template(template_hex):
        chunk = data[pos:pos + length * 2]
        pos += length * 2
        got[tag] = chunk
    if pos < len(data):
        got["_leftover"] = data[pos:]
    return got


def _tlv_payload(command_data_hex: str) -> str:
    """GPO command data is ``83 <len> <pdol data>`` — unwrap it when present."""
    data = (command_data_hex or "").upper()
    if data.startswith("83") and len(data) >= 4:
        length = int(data[2:4], 16)
        return data[4:4 + length * 2]
    return data


def build_card_view(trace_messages: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Turn the per-attempt trace stream into the judgeable ``card`` view."""
    if not trace_messages:
        return None
    apdus = [m for m in trace_messages
             if isinstance(m, dict) and m.get("event") == "apdu"]
    if not apdus:
        return None

    view: dict[str, Any] = {"apdu_count": len(apdus), "commands": []}
    pending_command: dict[str, Any] | None = None
    entries: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for item in apdus:
        if str(item.get("direction", "")).upper() == "COMMAND":
            pending_command = item
            view["commands"].append({
                "cla": item.get("cla", ""), "ins": item.get("ins", ""),
                "p1": item.get("p1", ""), "p2": item.get("p2", ""),
                "data_hex": item.get("data_hex", ""),
            })
        else:
            entries.append((pending_command, item))
            pending_command = None

    def _find(ins: str, cla: str = "80") -> dict[str, Any] | None:
        for command, _response in entries:
            if command and str(command.get("cla", "")).upper() == cla \
                    and str(command.get("ins", "")).upper() == ins:
                return command
        return None

    def _response_for(command: dict[str, Any] | None) -> str:
        for cmd, resp in entries:
            if cmd is command and resp is not None:
                return str(resp.get("data_hex", ""))
        return ""

    # --- SELECT AID: take the SELECT whose payload is not a PPSE DF name -----
    select_cmd = None
    for command, _response in entries:
        if not command:
            continue
        cla, ins, p1, _p2, data = _apdu_fields(str(command.get("data_hex", "")))
        if cla == "00" and ins == "A4" and p1 == "04" and data not in _PPSE_NAMES:
            select_cmd = command
            break
    if select_cmd is not None:
        select_resp = _response_for(select_cmd)
        # Response = data + SW1SW2; drop the status word before TLV parsing.
        body = select_resp[:-4] if len(select_resp) >= 4 else select_resp
        tlvs = parse_tlv(body)
        view["select_aid"] = {
            "command": select_cmd.get("data_hex", ""),
            "response": select_resp,
            "pdol": tlvs.get("9F38", ""),
            "cdol1": tlvs.get("8C", ""),
            "afl": tlvs.get("94", ""),
            "response_tlvs": tlvs,
        }

    # --- READ RECORD (00 B2): CDOL1 (8C) / CDOL2 (8D) live in the record, ----
    # not in the SELECT AID FCI, so the GENERATE AC data cannot be split
    # without them.
    read_cmd = _find("B2", cla="00")
    if read_cmd is not None:
        read_resp = _response_for(read_cmd)
        body = read_resp[:-4] if len(read_resp) >= 4 else read_resp
        tlvs = parse_tlv(body)
        view["read_record"] = {
            "command": read_cmd.get("data_hex", ""),
            "response": read_resp,
            "cdol1": tlvs.get("8C", ""),
            "cdol2": tlvs.get("8D", ""),
            "response_tlvs": tlvs,
        }

    # --- GPO (80 A8): split its PDOL data against the card's 9F38 -----------
    gpo_cmd = _find("A8")
    if gpo_cmd is not None:
        _cla, _ins, _p1, _p2, data = _apdu_fields(str(gpo_cmd.get("data_hex", "")))
        pdol_template = (view.get("select_aid") or {}).get("pdol", "")
        view["gpo"] = {
            "command": gpo_cmd.get("data_hex", ""),
            "response": _response_for(gpo_cmd),
            "pdol_template": pdol_template,
            "pdol": split_by_template(pdol_template, _tlv_payload(data))
                    if pdol_template else {},
        }

    # --- GENERATE AC (80 AE): split CDOL1 data ------------------------------
    gac_cmd = _find("AE")
    if gac_cmd is not None:
        _cla, _ins, _p1, _p2, data = _apdu_fields(str(gac_cmd.get("data_hex", "")))
        cdol1_template = ((view.get("select_aid") or {}).get("cdol1")
                          or (view.get("read_record") or {}).get("cdol1")
                          or "")
        view["gac"] = {
            "command": gac_cmd.get("data_hex", ""),
            "response": _response_for(gac_cmd),
            "cdol_template": cdol1_template,
            "cdol": split_by_template(cdol1_template, data) if cdol1_template else {},
        }
    return view
