"""Raw POS client for L4 section 5.3 second half: retry after persist_failed.

Single case (VISA-CL-001).  Reports the result; if the host answers
`detail=persist_failed`, it waits `--retry-delay` and resends the SAME logical
result with a NEW msg_id (the plan's "重传同一结果（新 msg_id）").

Uses a dedicated reader task so it can actually read the acks (the repository
mock cannot -- see L4_2 / M_pos_ack_deadlock).

Prints one `SUMMARY {...}` JSON line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
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


class RetryClient:
    def __init__(self, port, token, device_id, retry_delay, max_attempts,
                 initial_delay=0.0):
        self.port, self.token, self.device_id = port, token, device_id
        self.retry_delay, self.max_attempts = retry_delay, max_attempts
        self.initial_delay = initial_delay
        self.pending: dict[str, asyncio.Future] = {}
        self.attempts: list[dict] = []
        self.writer = None
        self.reader = None
        self.done = asyncio.Event()

    def log(self, *a):
        print("[rawretry]", *a, flush=True)

    async def send(self, frame):
        self.writer.write(encode_frame(frame))
        await self.writer.drain()

    async def connect(self) -> bool:
        self.reader, self.writer = await asyncio.open_connection(
            "127.0.0.1", self.port)
        await self.send(make_frame(
            "handshake", client_type="pos", token=self.token,
            device_id=self.device_id, app_version="rawretry-0.1",
            device_model="rawretry", protocol_version=PROTOCOL_VERSION_STRING))
        ack = await read_frame(self.reader)
        self.log("handshake ->", ack.get("type"), ack.get("protocol_version"))
        return ack.get("type") == "handshake_ack"

    async def wait_ack(self, msg_id, timeout=20.0):
        fut = asyncio.get_running_loop().create_future()
        self.pending[msg_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self.log(f"ack TIMEOUT for {msg_id}")
            return None
        finally:
            self.pending.pop(msg_id, None)

    async def reader_loop(self):
        while True:
            frame = await read_frame(self.reader)
            t = frame.get("type")
            if t == MSG_HEARTBEAT:
                await self.send(make_frame(MSG_HEARTBEAT_ACK,
                                           ref_msg_id=frame.get("msg_id")))
            elif t == MSG_ACK:
                fut = self.pending.pop(frame.get("ack_msg_id"), None)
                if fut is not None and not fut.done():
                    fut.set_result(frame)
            elif t == MSG_CONFIG_PUSH:
                await self.send(make_frame(MSG_ACK,
                                           ack_msg_id=frame.get("msg_id"), ok=True))
                self.log("config_push", frame.get("config_type"))
            elif t == MSG_CASE_STARTED:
                await self.send(make_frame(MSG_ACK,
                                           ack_msg_id=frame.get("msg_id"), ok=True))
                asyncio.create_task(self.handle_case(frame))

    async def handle_case(self, frame):
        case_id = frame.get("case_id")
        attempt_id = frame.get("attempt_id")
        self.log(f"case_started {case_id}")
        if self.initial_delay:
            self.log(f"waiting {self.initial_delay}s before the first attempt")
            await asyncio.sleep(self.initial_delay)
        for n in range(1, self.max_attempts + 1):
            # NEW msg_id each time: this is a retry, not a duplicate
            out = make_frame(MSG_TRANSACTION_RESULT, case_id=case_id,
                             attempt_id=attempt_id, device_id=self.device_id,
                             result=RESULT)
            await self.send(out)
            ack = await self.wait_ack(out["msg_id"])
            detail = ack.get("detail") if ack else None
            self.attempts.append({"n": n, "msg_id": out["msg_id"],
                                  "ok": ack.get("ok") if ack else None,
                                  "detail": detail})
            self.log(f"attempt {n}: ok={ack.get('ok') if ack else None} "
                     f"detail={detail}")
            if detail != "persist_failed":
                break
            await asyncio.sleep(self.retry_delay)
        self.done.set()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--token", required=True)
    ap.add_argument("--device-id", default="rawretry")
    ap.add_argument("--retry-delay", type=float, default=6.0)
    ap.add_argument("--max-attempts", type=int, default=4)
    ap.add_argument("--initial-delay", type=float, default=0.0)
    ap.add_argument("--lifetime", type=float, default=70.0)
    args = ap.parse_args()

    c = RetryClient(args.port, args.token, args.device_id, args.retry_delay,
                    args.max_attempts, args.initial_delay)
    if not await c.connect():
        print("SUMMARY " + json.dumps({"handshake": False}))
        return 1
    task = asyncio.create_task(c.reader_loop())
    try:
        await asyncio.wait_for(c.done.wait(), timeout=args.lifetime)
    except asyncio.TimeoutError:
        c.log("lifetime elapsed")
    except (asyncio.IncompleteReadError, ConnectionError) as exc:
        c.log("connection lost:", type(exc).__name__)
    task.cancel()
    print("SUMMARY " + json.dumps({"handshake": True, "attempts": c.attempts}))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
