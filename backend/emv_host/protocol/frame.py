"""Device protocol layer.

Wire format (locked, see docs/protocol.md):

    +---------------+----------------------+
    | 4-byte length | UTF-8 JSON payload   |
    +---------------+----------------------+

* length is big-endian unsigned 32-bit, counting the UTF-8 **bytes** of the
  payload (not characters);
* maximum payload size is ``MAX_FRAME_BYTES`` (1 MiB);
* a peer sending more than ``MAX_BAD_FRAMES`` undecodable frames is dropped.
"""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

HEADER_LEN = 4
MAX_FRAME_BYTES = 1 << 20  # 1 MiB
MAX_BAD_FRAMES = 5


class ProtocolError(Exception):
    """Base error for wire-protocol violations."""


class FrameTooLargeError(ProtocolError):
    """A frame exceeded MAX_FRAME_BYTES."""


class FrameDecodeError(ProtocolError):
    """A frame was not valid JSON (or not a JSON object)."""


def encode_frame(payload: dict[str, Any]) -> bytes:
    """Serialize one frame to bytes with the length prefix."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise FrameTooLargeError(f"frame of {len(body)} bytes exceeds {MAX_FRAME_BYTES}")
    return struct.pack(">I", len(body)) + body


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    """Read one length-prefixed JSON frame from the stream."""
    header = await reader.readexactly(HEADER_LEN)
    (length,) = struct.unpack(">I", header)
    if length > MAX_FRAME_BYTES:
        raise FrameTooLargeError(f"declared length {length} exceeds {MAX_FRAME_BYTES}")
    body = await reader.readexactly(length)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrameDecodeError(f"invalid JSON payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise FrameDecodeError("frame payload must be a JSON object")
    return payload
