"""TCP device server: one port for both client types, identity via handshake.

Design decisions (see docs/protocol.md):
* both the Android card simulator and the POS transaction app dial the same
  port; the handshake (client_type + token) establishes identity;
* at most one active connection per client type;
* heartbeat: server sends ``heartbeat`` every ``heartbeat_interval`` s;
  silence longer than ``heartbeat_warn`` -> warning event; silence longer
  than ``disconnect_timeout`` -> connection dropped;
* acks: ``ack`` frames resolve pending ``send_and_wait_ack`` futures.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from .connection import ClientConnection, ConnectionClosed, HandshakeError
from .messages import ERR_HANDSHAKE_TIMEOUT, MSG_HEARTBEAT, make_error, make_frame

logger = logging.getLogger("emv_host.protocol")

# callback: (event: str, conn: ClientConnection, frame: dict | None) -> Awaitable[None]
EventHandler = Callable[[str, ClientConnection, dict[str, Any] | None], Awaitable[None]]


class ProtocolServer:
    def __init__(self, tokens: dict[str, str], on_event: EventHandler,
                 heartbeat_interval: float = 10.0,
                 heartbeat_warn: float = 30.0,
                 disconnect_timeout: float = 60.0):
        self.tokens = dict(tokens)
        self.on_event = on_event
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_warn = heartbeat_warn
        self.disconnect_timeout = disconnect_timeout

        self._server: asyncio.Server | None = None
        self._connections: dict[str, ClientConnection] = {}
        self._heartbeat_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    # -- lifecycle ----------------------------------------------------------
    async def start(self, host: str, port: int) -> None:
        self._server = await asyncio.start_server(self._accept, host, port)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        for sock in self._server.sockets or []:
            logger.info("device TCP server listening on %s:%s", host, sock.getsockname()[1])
        logger.info("device protocol port: %s", port)

    async def stop(self) -> None:
        for conn in list(self._connections.values()):
            conn.close()
        self._connections.clear()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for task in list(self._tasks):
            task.cancel()

    # -- connection management ----------------------------------------------
    async def _accept(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        conn = ClientConnection(self, reader, writer, writer.get_extra_info("peername") or ("?", 0))
        task = asyncio.create_task(self._drive(conn))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _drive(self, conn: ClientConnection) -> None:
        try:
            await conn.run()
        except HandshakeError as exc:
            logger.warning("handshake rejected from %s:%s: %s", *conn.addr, exc.message)
            try:
                await conn.send(make_error(exc.code, exc.message))
            except Exception:  # noqa: BLE001
                pass
        except ConnectionClosed as exc:
            logger.info("connection closed: %s", exc)
        except Exception as exc:  # noqa: BLE001 - never kill the accept loop
            logger.exception("unexpected connection error for %s: %s", conn.addr, exc)
        finally:
            conn.close()
            if conn.session_id and self._connections.get(conn.session_id) is conn:
                del self._connections[conn.session_id]
            if conn.client_type:
                await self.emit("disconnected", conn, None)

    async def emit(self, event: str, conn: ClientConnection,
                   frame: dict[str, Any] | None) -> None:
        try:
            await self.on_event(event, conn, frame)
        except Exception:  # noqa: BLE001 - event sink must not break the loop
            logger.exception("event handler failed for %s", event)

    # -- registry -----------------------------------------------------------
    def register(self, conn: ClientConnection) -> None:
        assert conn.session_id and conn.client_type
        self._connections[conn.session_id] = conn
        logger.info("client connected: %s", conn.label)

    def get_client(self, client_type: str) -> ClientConnection | None:
        for conn in self._connections.values():
            if conn.client_type == client_type:
                return conn
        return None

    def retire_connection(self, conn: ClientConnection) -> None:
        """Close an old session that a same-device_id reconnect replaced.

        The replacement is already registered and 'connected', so the old
        connection's own 'disconnected' event is a no-op for the runner."""
        logger.info("retiring replaced session %s (device %s)",
                    conn.label, conn.device_id)
        conn.close()

    def active_types(self) -> list[str]:
        return sorted({c.client_type for c in self._connections.values()
                       if c.client_type})

    def all_clients(self) -> list[ClientConnection]:
        return list(self._connections.values())

    # -- messaging ----------------------------------------------------------
    async def send_to(self, client_type: str, frame: dict[str, Any]) -> bool:
        conn = self.get_client(client_type)
        if conn is None:
            return False
        try:
            await conn.send(frame)
            return True
        except ConnectionClosed:
            return False

    async def send_to_and_ack(self, client_type: str, frame: dict[str, Any],
                              timeout: float = 15.0) -> dict[str, Any] | None:
        """Send to one client type and wait for its ack.  Returns the ack frame
        or None when no such client is connected."""
        conn = self.get_client(client_type)
        if conn is None:
            return None
        return await conn.send_and_wait_ack(frame, timeout=timeout)

    async def send_to_and_ack_signal(self, client_type: str, frame: dict[str, Any],
                                     expect_type: str, timeout: float = 15.0
                                     ) -> dict[str, Any] | None:
        """Send a CTEL2 request to one client and wait for its ACK signal type."""
        conn = self.get_client(client_type)
        if conn is None:
            return None
        return await conn.send_and_wait_signal(frame, expect_type, timeout=timeout)

    # -- heartbeat ----------------------------------------------------------
    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            for conn in list(self._connections.values()):
                if conn.client_type:
                    try:
                        await conn.send(make_frame(MSG_HEARTBEAT))
                    except ConnectionClosed:
                        continue
