"""CTEL2 OUT decoding (protocol v2): OPS (DF8129) + DataRecord/DiscData.

Builds the judgeable result dict consumed by ``judge.asserter.assert_result``:

    {
      "out": {
        "ops": {"raw": "<8B>", "status": "APPROVED", "outcome": "approved",
                "start": "A", "online_response_data": "N/A", "cvm": "NO_CVM",
                "ui_request_on_outcome": 0, "ui_request_on_restart": 0,
                "data_record_present": 1, "discretionary_data_present": 0,
                "receipt": 0, "alternate_interface_preference": "N/A",
                "field_off_request": 0, "removal_timeout": 0},
        "datarecord": {"9F26": "...", ...},
        "discdata": {...},
        "uird": "<13B hex>" | None,
        "kernelid": "..." | None,
      },
      "outcome": "approved",   # derived convenience
      "online": "offline",
      "resp_code": "00",
      "card": {},              # filled by the APDU judge (P2)
    }
"""

from __future__ import annotations

from typing import Any

from ..protocol.kernel_tlv import decode_tlv_map

_OPS_STATUS = {
    0b0001: "APPROVED", 0b0010: "DECLINED", 0b0011: "ONLINE REQUEST",
    0b0100: "END APPLICATION", 0b0101: "SELECT NEXT",
    0b0110: "TRY ANOTHER INTERFACE", 0b0111: "TRY AGAIN", 0b1111: "N/A",
}
_OPS_START = {0b0000: "A", 0b0001: "B", 0b0010: "C", 0b0011: "D", 0b1111: "N/A"}
_OPS_CVM = {
    0b0000: "NO_CVM", 0b0001: "OBTAIN_SIGNATURE", 0b0010: "ONLINE_PIN",
    0b0011: "CONFIRMATION_CODE_VERIFIED", 0b1111: "N/A",
}

#: DataRecord tag used for resp_code when present (Authorization Response Code).
RESP_CODE_TAG = "8A"
#: DataRecord tag for the CID (Cryptogram Information Data), used for online.
CID_TAG = "9F27"


def _nibble(value: int) -> str:
    return format(value & 0x0F, "04b")


def decode_ops(raw_hex: str) -> dict[str, Any]:
    """Decode OPS (Outcome Parameter Set, DF8129, 8B) per the vendor bit table."""
    raw = (raw_hex or "").strip().upper().replace(" ", "")
    try:
        data = bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError(f"OPS 不是合法 hex：{raw_hex!r}") from exc
    if len(data) != 8:
        raise ValueError(f"OPS 须为 8 字节，实际 {len(data)}")
    status_n = (data[0] >> 4) & 0x0F
    start_n = (data[1] >> 4) & 0x0F
    online_n = (data[2] >> 4) & 0x0F
    cvm_n = (data[3] >> 4) & 0x0F
    b5 = data[4]
    status = _OPS_STATUS.get(status_n, f"RFU({_nibble(status_n)})")
    return {
        "raw": raw,
        "status": status,
        "status_bits": _nibble(status_n),
        "outcome": _derive_outcome(status),
        "start": _OPS_START.get(start_n, f"RFU({_nibble(start_n)})"),
        "online_response_data": "N/A" if online_n == 0b1111
                                else f"RFU({_nibble(online_n)})",
        "cvm": _OPS_CVM.get(cvm_n, f"RFU({_nibble(cvm_n)})"),
        "ui_request_on_outcome": (b5 >> 7) & 1,
        "ui_request_on_restart": (b5 >> 6) & 1,
        "data_record_present": (b5 >> 5) & 1,
        "discretionary_data_present": (b5 >> 4) & 1,
        "receipt": (b5 >> 3) & 1,
        "alternate_interface_preference": "N/A" if (data[5] >> 4) & 0x0F == 0b1111
                                         else f"RFU({_nibble((data[5] >> 4) & 0x0F)})",
        "field_off_request": data[6],
        "removal_timeout": data[7],
    }


def _derive_outcome(status: str) -> str:
    if status == "APPROVED":
        return "approved"
    if status == "DECLINED":
        return "declined"
    return status.lower().replace(" ", "_")


def _parse_tlv_dict(raw: str, what: str) -> dict[str, str]:
    raw = (raw or "").strip().upper().replace(" ", "")
    if not raw:
        return {}
    try:
        return decode_tlv_map(raw)
    except ValueError as exc:
        raise ValueError(f"{what} TLV 解析失败：{exc}") from exc


def _signal(signal_data: Any, item_id: str) -> Any:
    if not isinstance(signal_data, list):
        return None
    for item in signal_data:
        if isinstance(item, dict) and str(item.get("id", "")).upper() == item_id.upper():
            return item.get("value")
    return None


def build_result(signal_data: list[dict[str, Any]],
                 card: dict[str, Any] | None = None) -> dict[str, Any]:
    """Turn one OUT ``signalData`` into the judgeable result dict."""
    ops_value = _signal(signal_data, "OPS")
    if ops_value is None:
        raise ValueError("OUT 缺少 OPS（CTEL2 必填）")
    ops = decode_ops(str(ops_value))
    datarecord = _parse_tlv_dict(str(_signal(signal_data, "DataRecord") or ""),
                                 "DataRecord")
    discdata = _parse_tlv_dict(str(_signal(signal_data, "DiscData") or ""),
                               "DiscData")
    uird = _signal(signal_data, "UIRD")
    kernelid = _signal(signal_data, "KernelId")

    resp_code = datarecord.get(RESP_CODE_TAG)
    if not resp_code:
        resp_code = {"APPROVED": "00", "DECLINED": "05",
                     "ONLINE REQUEST": "00"}.get(ops["status"], "")
    online = "offline"
    cid = datarecord.get(CID_TAG)
    if cid:
        try:
            online = "online" if (int(cid[:2], 16) >> 6) == 0b10 else "offline"
        except (ValueError, IndexError):
            online = "offline"
    elif ops["status"] == "ONLINE REQUEST":
        online = "online"

    return {
        "out": {
            "ops": ops,
            "datarecord": datarecord,
            "discdata": discdata,
            "uird": uird,
            "kernelid": kernelid,
        },
        "outcome": ops["outcome"],
        "online": online,
        "resp_code": resp_code,
        "card": dict(card or {}),
    }
