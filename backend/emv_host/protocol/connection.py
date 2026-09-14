"""One authenticated device connection (handshake, ack registry, framing)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .frame import MAX_BAD_FRAMES, FrameDecodeError, ProtocolError, encode_frame
from .messages import (
    ERR_PROTOCOL_MISMATCH,
    MSG_ACK,
    MSG_HANDSHAKE,
    MSG_HANDSHAKE_ACK,
    MSG_HEARTBEAT,
    MSG_HEARTBEAT_ACK,
    PROTOCOL_VERSION_STRING,
    make_ack,
    make_frame,
    version_tuple,
)

logger = logging.getLogger("emv_host.protocol")

HANDSHAKE_TIMEOUT = 10.0
ACK_TIMEOUT = 15.0


class ConnectionClosed(Exception):
    pass


class HandshakeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ClientConnection:
    """A single TCP connection that has completed (or is completing) handshake."""

    def __init__(self, server: "ProtocolServer", reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter, addr: tuple[str, int]):
        self.server = server
        self.reader = reader
        self.writer = writer
        self.addr = addr
        self.connected_at = time.time()
        self.last_recv = time.time()

        self.session_id: str | None = None
        self.client_type: str | None = None
        self.device_id: str | None = None
        self.app_version: str | None = None

        self._bad_frames = 0
        self._closed = False
        self._pending_acks: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._replacing: "ClientConnection | None" = None

    # -- identity -----------------------------------------------------------
    @property
    def label(self) -> str:
        device = self.device_id or self.addr[0]
        return f"{self.client_type or '?'}@{device}"

    # -- writing ------------------------------------------------------------
    async def send(self, frame: dict[str, Any]) -> None:
        if self._closed:
            raise ConnectionClosed(f"{self.label}: connection already closed")
        self.writer.write(encode_frame(frame))
        try:
            await self.writer.drain()
        except (ConnectionError, OSError) as exc:
            raise ConnectionClosed(f"{self.label}: write failed: {exc}") from exc

    async def send_and_wait_ack(self, frame: dict[str, Any], timeout: float = ACK_TIMEOUT,
                                event: asyncio.Event | None = None) -> dict[str, Any]:
        """Send a frame and wait for its ack.  ``event`` (when given) is set as
        soon as the frame is written, useful for abort flows."""
        msg_id = frame["msg_id"]
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending_acks[msg_id] = fut
        try:
            await self.send(frame)
            if event is not None:
                event.set()
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise ConnectionClosed(f"{self.label}: ack timeout for {msg_id}")
        finally:
            self._pending_acks.pop(msg_id, None)

    def _on_ack(self, frame: dict[str, Any]) -> None:
        fut = self._pending_acks.pop(frame.get("ack_msg_id"), None)
        if fut is not None and not fut.done():
            fut.set_result(frame)

    # -- receiving ----------------------------------------------------------
    async def run(self) -> None:
        """Drive the handshake, then the read loop until EOF/timeout.

        Does not close the writer: the server's driver sends the rejection
        error frame when the handshake fails and closes afterwards."""
        try:
            await self._handshake()
            await self._read_loop()
        except (HandshakeError, ConnectionClosed, ProtocolError):
            raise
        except asyncio.TimeoutError:
            raise ConnectionClosed(f"{self.label}: read timeout, treating as dead")
        except asyncio.IncompleteReadError:
            raise ConnectionClosed(f"{self.label}: peer closed connection")

    async def _handshake(self) -> None:
        frame = await asyncio.wait_for(self.reader_read_frame(), HANDSHAKE_TIMEOUT)
        if frame.get("type") != MSG_HANDSHAKE:
            raise HandshakeError("bad_frame", "first frame must be handshake")
        client_type = frame.get("client_type")
        token = frame.get("token")
        if client_type not in self.server.tokens:
            raise HandshakeError("auth_failed", f"unknown client_type {client_type!r}")
        if token != self.server.tokens[client_type]:
            raise HandshakeError("auth_failed", "invalid token")

        # v1.2: negotiate the protocol level.  This is a HARD switch — a client
        # that omits protocol_version, or advertises anything below 1.2, is a
        # deterministic failure (no reconnect will fix it).
        client_version = frame.get("protocol_version")
        if client_version is None:
            raise HandshakeError(
                ERR_PROTOCOL_MISMATCH,
                f"handshake must carry protocol_version={PROTOCOL_VERSION_STRING}")
        if version_tuple(str(client_version)) < version_tuple(PROTOCOL_VERSION_STRING):
            raise HandshakeError(
                ERR_PROTOCOL_MISMATCH,
                f"protocol {client_version} not supported (need {PROTOCOL_VERSION_STRING})")

        # v1.1: one connection per client_type, but a reconnect from the SAME
        # device replaces the old (possibly not-yet-timed-out) session instead
        # of being rejected with already_connected.
        existing = self.server.get_client(client_type)
        if existing is not None:
            same_device = bool(existing.device_id) and \
                existing.device_id == (frame.get("device_id") or "")
            if not same_device:
                raise HandshakeError("already_connected",
                                     f"a {client_type} client is already connected")
            self._replacing = existing
            logger.info("%s: same-device reconnect will replace old session %s",
                        self.addr, existing.session_id)

        self.client_type = client_type
        self.device_id = frame.get("device_id") or ""
        self.app_version = frame.get("app_version") or ""
        self.session_id = f"{client_type}-{int(self.connected_at * 1000)}-{self.addr[1]}"

        ack = make_frame(
            MSG_HANDSHAKE_ACK,
            session_id=self.session_id,
            protocol_version=PROTOCOL_VERSION_STRING,
            server_time=time.time(),
        )
        await self.send(ack)
        self.server.register(self)
        await self.server.emit("connected", self, frame)
        logger.info("handshake ok: %s (device=%s app=%s protocol=%s)",
                    self.label, self.device_id, self.app_version,
                    client_version)

        # new session is registered and connected; retire the replaced one now
        if self._replacing is not None:
            self.server.retire_connection(self._replacing)

    async def reader_read_frame(self) -> dict[str, Any]:
        from .frame import read_frame
        try:
            return await read_frame(self.reader)
        except (FrameDecodeError, asyncio.IncompleteReadError) as exc:
            self._bad_frames += 1
            if self._bad_frames >= MAX_BAD_FRAMES:
                raise ConnectionClosed(
                    f"{self.label}: {self._bad_frames} bad frames, dropping") from exc
            raise

    async def _read_loop(self) -> None:
        warned = False
        while not self._closed:
            try:
                frame = await asyncio.wait_for(
                    self.reader_read_frame(),
                    timeout=self.server.heartbeat_interval,
                )
            except asyncio.TimeoutError:
                idle = time.time() - self.last_recv
                if idle >= self.server.disconnect_timeout:
                    raise ConnectionClosed(
                        f"{self.label}: silent for {idle:.0f}s, dropping")
                if idle >= self.server.heartbeat_warn and not warned:
                    warned = True
                    logger.warning("%s: heartbeat overdue (%.0fs)", self.label, idle)
                    await self.server.emit("heartbeat_warn", self, None)
                continue
            except FrameDecodeError as exc:
                # v1.2: a single undecodable frame no longer kills the session.
                # ``read_frame`` has already consumed exactly ``length`` bytes,
                # so the stream boundary is intact and reading can continue;
                # only MAX_BAD_FRAMES bad frames drop the connection (that check
                # lives in ``reader_read_frame``).
                logger.warning("%s: skipping bad frame (%d/%d): %s", self.label,
                               self._bad_frames, MAX_BAD_FRAMES, exc)
                continue

            self.last_recv = time.time()
            warned = False
            msg_type = frame.get("type")
            if msg_type == MSG_HEARTBEAT:
                await self.send(make_frame(MSG_HEARTBEAT_ACK, ref_msg_id=frame.get("msg_id")))
                continue
            if msg_type == MSG_ACK:
                self._on_ack(frame)
                continue
            await self.server.emit("message", self, frame)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Fail in-flight ack waits with ConnectionClosed instead of cancelling
        # their futures: a cancelled future makes the awaiting coroutine raise
        # asyncio.CancelledError (a BaseException), which callers that only
        # catch Exception — e.g. the run loop's send_and_wait_ack(...) — do
        # NOT handle, silently killing the whole run task.
        pending = list(self._pending_acks.items())
        for msg_id, fut in pending:
            if not fut.done():
                fut.set_exception(ConnectionClosed(
                    f"{self.label}: connection closed before ack {msg_id}"))
        self._pending_acks.clear()
        logger.debug("%s: close failed %d pending ack(s) with ConnectionClosed",
                     self.label, len(pending))
        try:
            self.writer.close()
        except Exception:  # noqa: BLE001 - best effort
            pass
