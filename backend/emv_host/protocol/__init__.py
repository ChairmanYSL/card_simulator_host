"""Device protocol package."""

from .connection import ClientConnection, ConnectionClosed, HandshakeError
from .frame import (
    MAX_BAD_FRAMES,
    MAX_FRAME_BYTES,
    FrameDecodeError,
    FrameTooLargeError,
    ProtocolError,
    encode_frame,
    read_frame,
)
from .server import ProtocolServer

__all__ = [
    "ClientConnection",
    "ConnectionClosed",
    "HandshakeError",
    "MAX_BAD_FRAMES",
    "MAX_FRAME_BYTES",
    "FrameDecodeError",
    "FrameTooLargeError",
    "ProtocolError",
    "ProtocolServer",
    "encode_frame",
    "read_frame",
]
