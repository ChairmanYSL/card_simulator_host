"""Handshake/auth/registry behavior over a real TCP socket pair (protocol v1.2)."""

import asyncio
import json
import struct

import pytest

from emv_host.protocol.connection import (
    ConnectionClosed,
    HANDSHAKE_TIMEOUT,
)
from emv_host.protocol.frame import encode_frame, read_frame
from emv_host.protocol.messages import (
    MSG_HANDSHAKE_ACK,
    MSG_HEARTBEAT,
    MSG_HEARTBEAT_ACK,
    PROTOCOL_VERSION_STRING,
    make_frame,
)
from emv_host.protocol.server import ProtocolServer

TOKENS = {"android": "secret-a", "pos": "secret-p"}


class Sink:
    def __init__(self):
        self.events = []
        self.messages = []

    async def __call__(self, event, conn, frame):
        self.events.append(event)
        if event == "message":
            self.messages.append(frame)


async def connect_client(port, client_type, token, device_id="t",
                         protocol_version=PROTOCOL_VERSION_STRING):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    handshake = make_frame("handshake", client_type=client_type, token=token,
                           device_id=device_id, protocol_version=protocol_version)
    writer.write(encode_frame(handshake))
    await writer.drain()
    ack = await read_frame(reader)
    return reader, writer, ack


async def _server(port, sink):
    server = ProtocolServer(TOKENS, sink, heartbeat_interval=0.5,
                            heartbeat_warn=2.0, disconnect_timeout=3.0)
    await server.start("127.0.0.1", port)
    return server


@pytest.mark.asyncio
async def test_handshake_auth_and_different_device_rejected(unused_tcp_port):
    sink = Sink()
    server = await _server(unused_tcp_port, sink)
    try:
        reader, writer, ack = await connect_client(
            unused_tcp_port, "android", "secret-a", device_id="a")
        assert ack["type"] == MSG_HANDSHAKE_ACK
        assert ack["session_id"].startswith("android-")
        assert ack.get("protocol_version") == PROTOCOL_VERSION_STRING

        # wrong token -> rejected with error frame
        r2, w2, ack2 = await connect_client(unused_tcp_port, "pos", "wrong")
        assert ack2["code"] == "auth_failed"
        w2.close()

        # different device_id of the same type -> already_connected
        r3, w3, ack3 = await connect_client(
            unused_tcp_port, "android", "secret-a", device_id="b")
        assert ack3["code"] == "already_connected"
        w3.close()

        assert server.get_client("android") is not None
        assert server.get_client("pos") is None
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.2)
        assert server.get_client("android") is None
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_same_device_reconnect_replaces_session(unused_tcp_port):
    sink = Sink()
    server = await _server(unused_tcp_port, sink)
    try:
        r1, w1, ack1 = await connect_client(
            unused_tcp_port, "android", "secret-a", device_id="dev-1")
        first_session = ack1["session_id"]
        assert server.get_client("android").session_id == first_session

        # same device_id reconnects -> accepted, old session retired
        r2, w2, ack2 = await connect_client(
            unused_tcp_port, "android", "secret-a", device_id="dev-1")
        assert ack2["type"] == MSG_HANDSHAKE_ACK
        second_session = ack2["session_id"]
        assert second_session != first_session
        assert server.get_client("android").session_id == second_session

        # the retired connection is closed server-side
        w1.close()
        await asyncio.sleep(0.3)
        # ...and its disconnect must NOT drop the replacement session
        assert server.get_client("android") is not None
        assert server.get_client("android").session_id == second_session

        w2.close()
        await asyncio.sleep(0.2)
        assert server.get_client("android") is None  # second closed too now
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_old_protocol_version_rejected(unused_tcp_port):
    sink = Sink()
    server = await _server(unused_tcp_port, sink)
    try:
        _, writer, ack = await connect_client(
            unused_tcp_port, "android", "secret-a", protocol_version="1.0")
        assert ack["code"] == "protocol_mismatch"
        writer.close()
        assert server.get_client("android") is None
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_v11_protocol_version_rejected(unused_tcp_port):
    """v1.2 is a hard switch: the previous negotiated level is refused."""
    sink = Sink()
    server = await _server(unused_tcp_port, sink)
    try:
        _, writer, ack = await connect_client(
            unused_tcp_port, "android", "secret-a", protocol_version="1.1")
        assert ack["code"] == "protocol_mismatch"
        writer.close()
        assert server.get_client("android") is None
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_newer_protocol_version_accepted(unused_tcp_port):
    """A client above the server level is fine (forward-compatible minor)."""
    sink = Sink()
    server = await _server(unused_tcp_port, sink)
    try:
        _, writer, ack = await connect_client(
            unused_tcp_port, "pos", "secret-p", protocol_version="1.3")
        assert ack["type"] == MSG_HANDSHAKE_ACK
        assert ack.get("protocol_version") == PROTOCOL_VERSION_STRING
        writer.close()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_missing_protocol_version_rejected(unused_tcp_port):
    """v1.2: a handshake without protocol_version is refused (no tolerance)."""
    sink = Sink()
    server = await _server(unused_tcp_port, sink)
    try:
        _, writer, ack = await connect_client(
            unused_tcp_port, "pos", "secret-p", protocol_version=None)
        assert ack["type"] == "error"
        assert ack["code"] == "protocol_mismatch"
        assert PROTOCOL_VERSION_STRING in ack["message"]
        writer.close()
        await asyncio.sleep(0.2)
        assert server.get_client("pos") is None
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_bad_frames_are_skipped_until_five(unused_tcp_port):
    """v1.2 §1.8: one undecodable frame is skipped, the session survives; the
    connection drops only on the 5th (MAX_BAD_FRAMES)."""
    sink = Sink()
    server = await _server(unused_tcp_port, sink)
    try:
        reader, writer, _ = await connect_client(unused_tcp_port, "pos", "secret-p")

        def bad_frame() -> bytes:
            # valid length prefix, invalid JSON body -> FrameDecodeError
            body = b"{not json"
            return struct.pack(">I", len(body)) + body

        # 1st bad frame: skipped, and a following good frame is still handled
        writer.write(bad_frame())
        writer.write(encode_frame(make_frame("log", level="info", message="alive")))
        await writer.drain()
        await asyncio.sleep(0.3)
        assert server.get_client("pos") is not None
        assert any(m.get("message") == "alive" for m in sink.messages)

        # frames 2..4: still alive
        for _ in range(3):
            writer.write(bad_frame())
            await writer.drain()
            await asyncio.sleep(0.1)
        assert server.get_client("pos") is not None

        # 5th bad frame: connection dropped
        writer.write(bad_frame())
        await writer.drain()
        await asyncio.sleep(0.4)
        assert server.get_client("pos") is None
        writer.close()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_bad_frame_counting_resets_across_sessions(unused_tcp_port):
    """A reconnect starts with a clean bad-frame budget."""
    sink = Sink()
    server = await _server(unused_tcp_port, sink)
    try:
        _, w1, _ = await connect_client(unused_tcp_port, "pos", "secret-p",
                                        device_id="dev-1")
        body = b"{not json"
        for _ in range(4):
            w1.write(struct.pack(">I", len(body)) + body)
            await w1.drain()
            await asyncio.sleep(0.05)
        assert server.get_client("pos") is not None
        w1.close()

        _, w2, ack2 = await connect_client(unused_tcp_port, "pos", "secret-p",
                                           device_id="dev-1")
        assert ack2["type"] == MSG_HANDSHAKE_ACK
        assert server.get_client("pos") is not None
        w2.close()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_heartbeat_echo(unused_tcp_port):
    sink = Sink()
    server = await _server(unused_tcp_port, sink)
    try:
        reader, writer, _ = await connect_client(unused_tcp_port, "pos", "secret-p")
        try:
            frame = await asyncio.wait_for(read_frame(reader), timeout=3)
            assert frame["type"] == MSG_HEARTBEAT
        except asyncio.TimeoutError:
            pytest.fail("server never sent heartbeat")
        writer.write(encode_frame(make_frame(MSG_HEARTBEAT)))
        await writer.drain()
        reply = await asyncio.wait_for(read_frame(reader), timeout=3)
        assert reply["type"] == MSG_HEARTBEAT_ACK
        writer.close()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_close_fails_pending_ack_with_connection_closed(unused_tcp_port):
    """Regression: a connection closing while a send_and_wait_ack() is pending
    must raise ConnectionClosed (caught by the run loop's ``except Exception``),
    NOT asyncio.CancelledError — the old fut.cancel() leaked a BaseException
    that silently killed the whole run task (zombie RUNNING + abort -> 409)."""
    sink = Sink()
    server = await _server(unused_tcp_port, sink)
    try:
        reader, writer, _ = await connect_client(unused_tcp_port, "pos", "secret-p")
        conn = server.get_client("pos")
        assert conn is not None

        ack_waiter = asyncio.create_task(
            conn.send_and_wait_ack(make_frame("config_push"), timeout=10))
        await asyncio.sleep(0.1)  # let the frame be sent and the ack be awaited

        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.2)  # server side notices EOF and closes the conn

        with pytest.raises(ConnectionClosed) as excinfo:
            await asyncio.wait_for(ack_waiter, timeout=3)
        assert "closed before ack" in str(excinfo.value)
        assert server.get_client("pos") is None
    finally:
        await server.stop()
