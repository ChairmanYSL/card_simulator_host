"""Shared plumbing for the mock Android card simulator and mock POS client.

Both mocks speak the exact wire protocol documented in docs/protocol.md, so
they double as a reference implementation for the real clients.

Run from the repo root::

    python tools/mock_clients/mock_android.py [--host H] [--port P] [--token T]
    python tools/mock_clients/mock_pos.py [--outcome approved_offline]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from emv_host.protocol.frame import encode_frame, read_frame  # noqa: E402
from emv_host.protocol.messages import (  # noqa: E402
    MSG_ACK,
    MSG_HANDSHAKE_ACK,
    MSG_HEARTBEAT,
    MSG_HEARTBEAT_ACK,
    PROTOCOL_VERSION_STRING,
    make_frame,
)

DEFAULT_PORT = 9100


def load_token(client_type: str) -> str:
    tokens_path = Path(__file__).resolve().parents[2] / "tokens.json"
    if tokens_path.is_file():
        data = json.loads(tokens_path.read_text(encoding="utf-8"))
        if client_type in data:
            return data[client_type]
    return {"android": "dev-android-token-change-me",
            "pos": "dev-pos-token-change-me"}[client_type]


class MockClient:
    def __init__(self, client_type: str, device_id: str,
                 on_message, host: str = "127.0.0.1",
                 port: int = DEFAULT_PORT, token: str | None = None,
                 reconnect: bool = True):
        self.client_type = client_type
        self.device_id = device_id
        self.on_message = on_message  # async (frame) -> None
        self.host = host
        self.port = port
        self.token = token or load_token(client_type)
        self.reconnect = reconnect
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.session_id: str | None = None
        # v1.2: the host now acks every inbound frame (transaction_result is
        # acked only AFTER it is persisted), so the mocks can - and the POS
        # mock does - wait for it instead of firing and forgetting.
        self._pending_acks: dict[str, asyncio.Future] = {}
        self.last_ack: dict | None = None
        # F1: ONE task owns the socket reader.  Application frames are handed to
        # the consumer loop through this queue, so awaiting an ack inside
        # on_message() can never block the code that would deliver that ack.
        self._inbox: asyncio.Queue[dict | None] | None = None
        self._reader_task: asyncio.Task | None = None

    def log(self, *args) -> None:
        print(f"[{self.client_type}]", *args, flush=True)

    async def connect(self) -> bool:
        try:
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port)
        except OSError as exc:
            self.log("cannot reach host:", exc)
            return False
        handshake = make_frame(
            "handshake", client_type=self.client_type, token=self.token,
            device_id=self.device_id, app_version="mock-0.1",
            device_model="mock-device",
            # single source of truth: v1.2 is a hard switch, so the mock must
            # advertise exactly what the host requires
            protocol_version=PROTOCOL_VERSION_STRING)
        self.writer.write(encode_frame(handshake))
        await self.writer.drain()
        ack = await read_frame(self.reader)
        if ack["type"] == MSG_HANDSHAKE_ACK:
            self.session_id = ack.get("session_id")
            self.log("connected, session", self.session_id)
            return True
        self.log(f"handshake rejected: {ack.get('code')}: {ack.get('message')}")
        self.writer.close()
        return False

    async def send(self, frame: dict) -> None:
        if self.writer is None:
            return
        self.writer.write(encode_frame(frame))
        await self.writer.drain()

    async def send_ack(self, ack_msg_id: str) -> None:
        await self.send(make_frame(MSG_ACK, ack_msg_id=ack_msg_id, ok=True))

    async def send_and_wait_ack(self, frame: dict,
                                timeout: float = 15.0) -> dict | None:
        """Send a frame and wait for the host's ack (v1.2 ack matrix).

        Returns the ack frame, or None on timeout (logged, not raised, so a
        mock run keeps going like the real client's retry logic would).
        """
        msg_id = frame.get("msg_id", "")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_acks[msg_id] = fut
        try:
            await self.send(frame)
            ack = await asyncio.wait_for(fut, timeout)
            self.last_ack = ack
            self.log(f"ack for {frame.get('type')}: ok={ack.get('ok')} "
                     f"detail={ack.get('detail', '')}")
            return ack
        except asyncio.TimeoutError:
            self.log(f"ack timeout ({timeout}s) for {frame.get('type')}")
            return None
        finally:
            self._pending_acks.pop(msg_id, None)

    def _on_ack(self, frame: dict) -> None:
        fut = self._pending_acks.pop(frame.get("ack_msg_id"), None)
        if fut is not None and not fut.done():
            fut.set_result(frame)

    async def _read_frames(self) -> None:
        """Read the socket until EOF.

        Only this task touches ``self.reader``.  Heartbeats and acks are
        protocol plumbing and are handled inline; everything else goes to the
        inbox so a slow ``on_message`` (e.g. waiting for an ack) never stops
        the reader — that self-lock made the POS mock report
        "ack timeout ... (not persisted?)" even though the host had already
        persisted and acked (F1).
        """
        assert self.reader is not None and self._inbox is not None
        while True:
            frame = await read_frame(self.reader)
            msg_type = frame.get("type")
            if msg_type == MSG_HEARTBEAT:
                await self.send(make_frame(
                    MSG_HEARTBEAT_ACK, ref_msg_id=frame.get("msg_id")))
                continue
            if msg_type == MSG_ACK:
                self._on_ack(frame)
                continue
            await self._inbox.put(frame)

    async def _cancel_reader(self) -> None:
        """Stop the reader task and drain the inbox (used on reconnect)."""
        task, self._reader_task = self._reader_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # expected: we just cancelled it
            except Exception:  # noqa: BLE001 - reader errors are surfaced by run()
                pass
        if self._inbox is not None:
            self._inbox = asyncio.Queue()

    async def run(self) -> None:
        while True:
            try:
                if not await self.connect():
                    if not self.reconnect:
                        return
                    await asyncio.sleep(3)
                    continue
                await self._cancel_reader()
                self._inbox = asyncio.Queue()
                self._reader_task = asyncio.create_task(self._read_frames())
                while True:
                    frame = await self._inbox.get()
                    # a reader failure surfaces here instead of hanging the loop
                    if self._reader_task is not None and self._reader_task.done():
                        exc = self._reader_task.exception()
                        if exc is not None:
                            raise exc
                    await self.on_message(self, frame)
            except (asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
                self.log("connection lost:", exc)
                await self._cancel_reader()
                if not self.reconnect:
                    return
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                await self._cancel_reader()
                raise


def common_args(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--token", default=None, help="override tokens.json")
    parser.add_argument("--device-id", default=None)
    parser.add_argument("--no-reconnect", action="store_true")
    return parser


def main_async(parser: argparse.ArgumentParser, make_client) -> None:
    args = parser.parse_args()
    client = make_client(args)
    asyncio.run(client.run())
