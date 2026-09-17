"""Mock POS transaction app (protocol v2, Mastercard CTEL2 TEI).

On ``test_info`` / ``config`` / ``act`` it replies the matching ``*_ack`` with
``ResponseCode=YES`` (as the real POS must).  After ``act`` it waits ``--delay``
seconds (or Enter with ``--interactive``) and reports the transaction outcome as
``msg`` (optional UIRD) + ``out`` (OPS + DataRecord + DiscData), as if the
operator had completed the transaction.

v2 removed all v1.2 POS frames: ``pos_config`` / ``pos_kernel_config`` /
``case_started`` / ``transaction_result`` / ``session_abort``.
"""

from __future__ import annotations

import asyncio

from mock_common import MockClient, common_args, main_async  # noqa: E402  (adds backend to path)

from emv_host.protocol.messages import (  # noqa: E402
    MSG_ACT,
    MSG_ACT_ACK,
    MSG_CONFIG,
    MSG_CONFIG_ACK,
    MSG_MSG,
    MSG_OUT,
    MSG_TEST_INFO,
    MSG_TEST_INFO_ACK,
    RESP_YES,
    make_frame,
)

# OPS byte layout (see docs/protocol-v2-design.md section 6.1):
#   byte1 b8-5 status (0001 approved / 0010 declined), byte5 b6=DataRecord,
#   b5=DiscData, byte6 AIP=N/A, byte7 FieldOff=N/A.
OPS_APPROVED = "1000F00030F0FF00"
OPS_DECLINED = "20F0F00030F0FF00"

OUTCOMES = {
    "approved_offline": {
        "ops": OPS_APPROVED,
        "datarecord": ("8A0230309F2701409F2608AABBCCDDEEFF0011"
                       "9F360200019F1007060A03A000005F2A020156"),
        "discdata": "DF81150600000000000F",
    },
    "declined_offline": {
        "ops": OPS_DECLINED,
        "datarecord": ("8A0230359F2701009F2608BBCCDDEEFF001122"
                       "9F360200029F1007060A03A000005F2A020156"),
        "discdata": "",
    },
    "approved_online": {
        "ops": OPS_APPROVED,
        "datarecord": ("8A0230309F2701809F2608AABBCCDDEEFF0011"
                       "9F360200039F1007060A03A000005F2A020156"
                       "91101122334455667788"),
        "discdata": "",
    },
}


def _ack(signal: str, frame: dict, code: str = RESP_YES) -> dict:
    return make_frame(signal, ref_msg_id=frame.get("msg_id"),
                      signalData=[{"id": "ResponseCode", "value": code}])


def _signal_counts(signal_data) -> str:
    counts: dict[str, int] = {}
    for item in signal_data or []:
        if isinstance(item, dict):
            counts[str(item.get("id"))] = counts.get(str(item.get("id")), 0) + 1
    return " ".join(f"{k}={v}" for k, v in sorted(counts.items()))


async def on_message(client: MockClient, frame: dict) -> None:
    msg_type = frame.get("type")

    if msg_type == MSG_TEST_INFO:
        await client.send(_ack(MSG_TEST_INFO_ACK, frame))
        client.log(f"test_info -> ack (TestId={_signal_item(frame, 'TestId')})")
        return

    if msg_type == MSG_CONFIG:
        await client.send(_ack(MSG_CONFIG_ACK, frame))
        client.config = frame.get("signalData")
        client.log(f"config -> ack ({_signal_counts(client.config)})")
        return

    if msg_type == MSG_ACT:
        await client.send(_ack(MSG_ACT_ACK, frame))
        client.act = frame.get("signalData")
        client.log(f"act -> ack (n={len(client.act or [])})")
        client.log("  (simulating the manual POS transaction...)")
        if client.interactive:
            await asyncio.get_running_loop().run_in_executor(
                None, input, "  press Enter to report the outcome > ")
        else:
            await asyncio.sleep(client.delay)
        await _report_outcome(client)
        return

    client.log("ignored frame type:", msg_type)


def _signal_item(frame: dict, item_id: str):
    for item in frame.get("signalData") or []:
        if isinstance(item, dict) and str(item.get("id")) == item_id:
            return item.get("value")
    return None


async def _report_outcome(client: MockClient) -> None:
    spec = OUTCOMES[client.outcome]
    uird = "1E040000000000000000000000"
    if client.uird:
        await client.send(make_frame(
            MSG_MSG, signalData=[{"id": "UIRD", "value": uird}]))
        client.log(f"msg UIRD={uird}")
    signal = [{"id": "OPS", "value": spec["ops"]}]
    if spec.get("datarecord"):
        signal.append({"id": "DataRecord", "value": spec["datarecord"]})
    if spec.get("discdata"):
        signal.append({"id": "DiscData", "value": spec["discdata"]})
    signal.append({"id": "UIRD", "value": uird if client.uird else None})
    out = await client.send_and_wait_ack(make_frame(MSG_OUT, signalData=signal))
    if out is None:
        client.log("WARNING: no ack for out (not persisted?)")
    client.log(f"out reported: OPS={spec['ops']} ({client.outcome})")


def make_client(args) -> MockClient:
    if args.outcome not in OUTCOMES:
        raise SystemExit(f"--outcome must be one of {sorted(OUTCOMES)}")
    client = MockClient(
        "pos", args.device_id or "mock-pos", on_message,
        host=args.host, port=args.port, token=args.token,
        reconnect=not args.no_reconnect)
    client.outcome = args.outcome
    client.delay = args.delay
    client.interactive = args.interactive
    client.uird = args.uird
    client.config = None
    client.act = None
    return client


if __name__ == "__main__":
    parser = common_args("mock POS transaction app (protocol v2 / CTEL2)")
    parser.add_argument("--outcome", default="approved_offline",
                        choices=sorted(OUTCOMES))
    parser.add_argument("--delay", type=float, default=2.0,
                        help="seconds between act_ack and the OUT report")
    parser.add_argument("--interactive", action="store_true",
                        help="wait for Enter instead of a timer")
    parser.add_argument("--uird", action="store_true",
                        help="also send one MSG (UIRD) before OUT")
    main_async(parser, make_client)
