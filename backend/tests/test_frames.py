"""Frame codec tests: round-trip, size cap, bad JSON."""

import asyncio

import pytest

from emv_host.protocol.frame import (
    MAX_FRAME_BYTES,
    FrameDecodeError,
    FrameTooLargeError,
    encode_frame,
    read_frame,
)


def _feed(reader, payload_bytes):
    reader.feed_data(payload_bytes)
    reader.feed_eof()


async def test_round_trip():
    payload = {"type": "handshake", "msg_id": "abc", "中文": "值"}
    encoded = encode_frame(payload)
    assert len(encoded) >= 4
    reader = asyncio.StreamReader()
    _feed(reader, encoded)
    frame = await read_frame(reader)
    assert frame == payload


async def test_length_prefix_is_big_endian():
    payload = {"type": "x", "pad": "a" * 300}
    encoded = encode_frame(payload)
    body_len = len(encoded) - 4
    assert int.from_bytes(encoded[:4], "big") == body_len
    assert encoded[:4] == body_len.to_bytes(4, "big")


async def test_multi_frame_stream():
    reader = asyncio.StreamReader()
    _feed(reader, encode_frame({"type": "a", "msg_id": "1"})
          + encode_frame({"type": "b", "msg_id": "2"}))
    first = await read_frame(reader)
    second = await read_frame(reader)
    assert first["type"] == "a" and second["type"] == "b"


async def test_oversize_declared_length_rejected():
    reader = asyncio.StreamReader()
    _feed(reader, (MAX_FRAME_BYTES + 1).to_bytes(4, "big"))
    with pytest.raises(FrameTooLargeError):
        await read_frame(reader)


async def test_bad_json_rejected():
    reader = asyncio.StreamReader()
    body = b"{not json"
    _feed(reader, len(body).to_bytes(4, "big") + body)
    with pytest.raises(FrameDecodeError):
        await read_frame(reader)


async def test_non_object_payload_rejected():
    reader = asyncio.StreamReader()
    body = b"[1,2,3]"
    _feed(reader, len(body).to_bytes(4, "big") + body)
    with pytest.raises(FrameDecodeError):
        await read_frame(reader)


def test_make_frame_envelope_is_v2():
    """Protocol v2: every outbound frame carries envelope v=2 + a msg_id."""
    from emv_host.protocol.messages import PROTOCOL_VERSION, make_frame

    frame = make_frame("x")
    assert PROTOCOL_VERSION == 2
    assert frame["v"] == 2
    assert frame["type"] == "x"
    assert frame["msg_id"]
