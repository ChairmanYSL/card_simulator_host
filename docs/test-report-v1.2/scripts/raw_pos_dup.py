"""Raw POS client for L4 section 5.2 (D2 duplicate transaction_result).

Why this exists: the repository POS mock awaits the host's result ack *inside*
MockClient.run()'s on_message, which blocks the very reader loop that would
deliver that ack, so it can never observe the ack detail.  This client keeps a
dedicated reader task, so it CAN read acks and report their `detail` values.

Behaviour: handshake as `pos`; ack every config push and case_started; report
VISA-CL-001's result; 1.0s later resend the IDENTICAL frame (same msg_id); then
report VISA-CL-002's result after `--second-delay` so the run stays alive while
the resend lands (otherwise the session is already torn down and the frame would
be quarantined rather than deduplicated).

Prints one `SUMMARY {...}` JSON line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

BACKEND = Path(r"E:\Work\ZT\card_simulator_host\backend")
sys.path.insert(0, str(BACKEND))

from emv_host.protocol.frame import encode_frame, read_frame  # noqa: E402
from emv_host.protocol.messages import (  # noqa: E402
    MSG_ACK,
    MSG_CASE_STARTED,
    MSG_CONFIG_PUSH,
    MSG_HEARTBEAT,
    MSG_HEARTBEAT_ACK,
    MSG_TRANSACTION_RESULT,
    PROTOCOL_VERSION_STRING,
    make_frame,
)

RESULT = {
    "resp_code": "00", "outcome": "approved", "online": "offline",
    "approval_code": "112233", "cvm": "no_cvm",
    "tags": {"gen_ac": {"9F26": "AABBCCDDEEFF0011", "9F27": "8000000000"}},
    "amount": "100.00", "currency": "156", "entry_mode": "contactless",
}


class Client:
    def __init__(self, port: int, token: str, device_id: str,
                 resend_delay: float, second_delay: float,
                 single_case: bool = False):
        self.port = port
        self.token = token
        self.device_id = device_id
        self.resend_delay = resend_delay
        self.second_delay = second_delay
        self.single_case = single_case
        self.pending: dict[str, asyncio.Future] = {}
        self.acks: list[dict] = []
        self.writer: asyncio.StreamWriter | None = None
        self.seen: list[str] = []
        self.resend_detail = None
        self.first_detail = None
        self.quarantine_detail = None
        self.done = asyncio.Event()

    def log(self, *a):
        print("[rawpos]", *a, flush=True)

    async def send(self, frame: dict):
        assert self.writer is not None
        self.writer.write(encode_frame(frame))
        await self.writer.drain()

    async def connect(self) -> bool:
        self.reader, self.writer = await asyncio.open_connection(
            "127.0.0.1", self.port)
        hs = make_frame("handshake", client_type="pos", token=self.token,
                        device_id=self.device_id, app_version="rawpos-0.1",
                        device_model="rawpos",
                        protocol_version=PROTOCOL_VERSION_STRING)
        await self.send(hs)
        ack = await read_frame(self.reader)
        self.log("handshake ->", ack.get("type"),
                 ack.get("protocol_version"))
        return ack.get("type") == "handshake_ack"

    async def wait_ack(self, msg_id: str, timeout: float = 20.0):
        fut = asyncio.get_running_loop().create_future()
        self.pending[msg_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self.log(f"ack TIMEOUT for {msg_id}")
            return None
        finally:
            self.pending.pop(msg_id, None)

    async def reader_loop(self, reader):
        while True:
            frame = await read_frame(reader)
            t = frame.get("type")
            self.seen.append(t)
            if t == MSG_HEARTBEAT:
                await self.send(make_frame(
                    MSG_HEARTBEAT_ACK, ref_msg_id=frame.get("msg_id")))
            elif t == MSG_ACK:
                fut = self.pending.pop(frame.get("ack_msg_id"), None)
                if fut is not None and not fut.done():
                    fut.set_result(frame)
            elif t == MSG_CONFIG_PUSH:
                await self.send(make_frame(
                    MSG_ACK, ack_msg_id=frame.get("msg_id"), ok=True))
                self.log("config_push", frame.get("config_type"))
            elif t == MSG_CASE_STARTED:
                await self.send(make_frame(
                    MSG_ACK, ack_msg_id=frame.get("msg_id"), ok=True))
                asyncio.create_task(self.handle_case(frame))
            else:
                self.log("other frame:", t)

    async def handle_case(self, frame: dict):
        case_id = frame.get("case_id")
        attempt_id = frame.get("attempt_id")
        self.log(f"case_started {case_id}")
        frame_out = make_frame(
            MSG_TRANSACTION_RESULT, case_id=case_id, attempt_id=attempt_id,
            device_id=self.device_id, result=RESULT)
        msg_id = frame_out["msg_id"]

        if case_id == "VISA-CL-001":
            await self.send(frame_out)
            ack1 = await self.wait_ack(msg_id)
            self.first_detail = ack1.get("detail") if ack1 else None
            self.log(f"FIRST  ack ok={ack1.get('ok') if ack1 else None} "
                     f"detail={self.first_detail}")

            await asyncio.sleep(self.resend_delay)
            # byte-identical resend: same msg_id
            await self.send(frame_out)
            ack2 = await self.wait_ack(msg_id)
            self.resend_detail = ack2.get("detail") if ack2 else None
            self.log(f"RESEND ack ok={ack2.get('ok') if ack2 else None} "
                     f"detail={self.resend_detail}")
            self.acks.append({"send": "first", "detail": self.first_detail})
            self.acks.append({"send": "resend", "detail": self.resend_detail})
            if self.single_case:
                self.done.set()
        elif self.single_case:
            self.log(f"ignoring case {case_id} (--single-case)")
        else:
            await asyncio.sleep(self.second_delay)
            await self.send(frame_out)
            ack = await self.wait_ack(msg_id)
            self.quarantine_detail = ack.get("detail") if ack else None
            self.log(f"SECOND case ack detail={self.quarantine_detail}")
            self.done.set()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--token", required=True)
    ap.add_argument("--device-id", default="rawpos")
    ap.add_argument("--resend-delay", type=float, default=1.0)
    ap.add_argument("--second-delay", type=float, default=8.0)
    ap.add_argument("--lifetime", type=float, default=45.0)
    ap.add_argument("--single-case", action="store_true",
                    help="only handle the first case, then exit")
    args = ap.parse_args()

    reader = None
    c = Client(args.port, args.token, args.device_id, args.resend_delay,
               args.second_delay, args.single_case)
    if not await c.connect():
        print("SUMMARY " + json.dumps({"handshake": False}))
        return 1
    task = asyncio.create_task(c.reader_loop(c.reader))
    try:
        await asyncio.wait_for(c.done.wait(), timeout=args.lifetime)
        c.log("both cases reported; exiting")
    except asyncio.TimeoutError:
        c.log("lifetime elapsed")
    except (asyncio.IncompleteReadError, ConnectionError) as exc:
        c.log("connection lost:", type(exc).__name__)
    task.cancel()

    summary = {
        "handshake": True,
        "first_detail": c.first_detail,
        "resend_detail": c.resend_detail,
        "second_case_detail": c.quarantine_detail,
        "seen_types": sorted(set(c.seen)),
    }
    print("SUMMARY " + json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
