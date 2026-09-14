"""Message vocabulary shared by host, Android card simulator and POS app.

Every frame is a JSON object and carries at minimum:

    {"v": 1, "type": "<message type>", "msg_id": "<uuid>", "ts": 1730000000.0}

The token is used **only** inside the handshake; afterwards the connection
itself is the session (``session_id`` granted by the server).

Attempt/session attribution: the server stamps ``run_id``, ``case_id`` and
``attempt_id`` on ``case_started``; the POS app MUST echo ``case_id`` +
``attempt_id`` in ``transaction_result``.  Results with an unknown or stale
attempt id go to quarantine, never to judgment.

Protocol level history:

* v1.1 — card_config carries the full profile; keys travel beside it; host
  pushes ``device_config`` once per connection (heartbeat/log/trace/ack
  settings); POS receives ``pos_config`` (tag list).
* v1.2 — HARD switch.  ``device_config`` is deleted (both sides use hardcoded
  defaults: heartbeat 10s / warn 30s / ack timeout 15s); the card side reads
  ``trace_enabled`` from each ``card_config`` instead; ``pos_kernel_config``
  (AID/CAPK/TERM) is added for the POS; ``session_abort`` notifies both sides
  of an abnormal stop and requires an ack.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

PROTOCOL_VERSION = 1  # envelope "v" field (wire version, stays 1)

# negotiated protocol level carried in the handshake.  v1.2 is a HARD switch:
# a client that omits protocol_version or advertises anything below 1.2 is
# rejected with protocol_mismatch and disconnected (no compatibility mode).
PROTOCOL_VERSION_STRING = "1.2"

CLIENT_ANDROID = "android"
CLIENT_POS = "pos"
CLIENT_TYPES = (CLIENT_ANDROID, CLIENT_POS)

# --- message types ---------------------------------------------------------
MSG_HANDSHAKE = "handshake"
MSG_HANDSHAKE_ACK = "handshake_ack"
MSG_HEARTBEAT = "heartbeat"
MSG_HEARTBEAT_ACK = "heartbeat_ack"
MSG_ACK = "ack"
MSG_ERROR = "error"

# host -> android / pos
MSG_CONFIG_PUSH = "config_push"          # card_config / pos_config / pos_kernel_config
MSG_START_EMULATION = "start_emulation"
MSG_STOP_EMULATION = "stop_emulation"
# host -> both: abnormal-stop notice (v1.2); each side must ack it
MSG_SESSION_ABORT = "session_abort"

# config_push payload discriminators (v1.2)
#   card_config       -> android: profile + keys + timeout_seconds + trace_enabled
#   pos_config        -> pos:     pos_params (tag list, unchanged since v1.1)
#   pos_kernel_config -> pos:     kernel_params {aid[], capk[], term}
# ``device_config`` is GONE in v1.2: both sides use hardcoded defaults
# (heartbeat 10s / warn 30s / ack timeout 15s).
CONFIG_TYPE_CARD = "card_config"
CONFIG_TYPE_POS = "pos_config"
CONFIG_TYPE_POS_KERNEL = "pos_kernel_config"

# host -> pos (informational, NOT a command; POS is operated manually)
MSG_CASE_STARTED = "case_started"

# pos -> host
MSG_TRANSACTION_RESULT = "transaction_result"

# any client -> host
MSG_STATUS = "status"
MSG_LOG = "log"

# --- ack / error codes -----------------------------------------------------
ERR_AUTH_FAILED = "auth_failed"
ERR_ALREADY_CONNECTED = "already_connected"
ERR_BAD_FRAME = "bad_frame"
ERR_HANDSHAKE_TIMEOUT = "handshake_timeout"
ERR_UNKNOWN_MESSAGE = "unknown_message"
ERR_SERVER_BUSY = "server_busy"
ERR_PROTOCOL_MISMATCH = "protocol_mismatch"


def version_tuple(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return (0,)  # unparseable -> treat as too old / invalid


def new_msg_id() -> str:
    return uuid.uuid4().hex


def make_frame(msg_type: str, **fields: Any) -> dict[str, Any]:
    """Build a frame with protocol envelope fields."""
    frame: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "type": msg_type,
        "msg_id": new_msg_id(),
        "ts": time.time(),
    }
    frame.update(fields)
    return frame


def make_ack(ack_msg_id: str, ok: bool = True, detail: str = "") -> dict[str, Any]:
    return make_frame(MSG_ACK, ack_msg_id=ack_msg_id, ok=ok, detail=detail)


def make_error(code: str, message: str, ref_msg_id: str | None = None) -> dict[str, Any]:
    frame = make_frame(MSG_ERROR, code=code, message=message)
    if ref_msg_id is not None:
        frame["ref_msg_id"] = ref_msg_id
    return frame


def make_heartbeat() -> dict[str, Any]:
    return make_frame(MSG_HEARTBEAT)
