"""Message vocabulary shared by host, Android card simulator and POS app.

Protocol v2 (2026-09-15):
* host <-> POS restructured to the Mastercard CTEL2 TEI signal set
  (``test_info`` / ``config`` / ``act`` + ACKs; POS -> host ``msg`` / ``out``);
* host <-> android (card link) keeps ``config_push{card_config}`` /
  ``start_emulation`` / ``stop_emulation`` / ``status`` / ``log``; the APDU
  trace channel becomes always-on and judgeable;
* v1.2 POS frames (``pos_config`` / ``pos_kernel_config`` / ``case_started`` /
  ``transaction_result`` / ``session_abort`` / ``device_config``) are REMOVED.

Every frame is a JSON object carrying at minimum:
    {"v": 2, "type": "<message type>", "msg_id": "<uuid>", "ts": 1730000000.0}
"""

from __future__ import annotations

import time
import uuid
from typing import Any

PROTOCOL_VERSION = 2  # envelope "v" field (protocol v2)

# negotiated protocol level carried in the handshake.  v2 is a HARD switch:
# a client that omits protocol_version or advertises anything below 2.0 is
# rejected with protocol_mismatch and disconnected (no compatibility mode).
PROTOCOL_VERSION_STRING = "2.0"

CLIENT_ANDROID = "android"
CLIENT_POS = "pos"
CLIENT_TYPES = (CLIENT_ANDROID, CLIENT_POS)

# --- transport / card link -------------------------------------------------
MSG_HANDSHAKE = "handshake"
MSG_HANDSHAKE_ACK = "handshake_ack"
MSG_HEARTBEAT = "heartbeat"
MSG_HEARTBEAT_ACK = "heartbeat_ack"
MSG_ACK = "ack"
MSG_ERROR = "error"

# host -> android
MSG_CONFIG_PUSH = "config_push"          # card_config
MSG_START_EMULATION = "start_emulation"
MSG_STOP_EMULATION = "stop_emulation"

# config_push payload discriminator (card link only in v2)
CONFIG_TYPE_CARD = "card_config"

# --- host <-> POS v2 (Mastercard CTEL2 TEI) --------------------------------
# host -> pos
MSG_TEST_INFO = "test_info"
MSG_CONFIG = "config"
MSG_ACT = "act"
# pos -> host
MSG_TEST_INFO_ACK = "test_info_ack"
MSG_CONFIG_ACK = "config_ack"
MSG_ACT_ACK = "act_ack"
MSG_MSG = "msg"                          # user interface request data (UIRD)
MSG_OUT = "out"                          # outcome (OPS/DataRecord/DiscData)

# config.signalData ids (host-defined; CTEL2 native is CONF_NAME only, we embed
# the full config -- see docs/protocol-v2-design.md section 4)
CONFIG_ID_NAME = "CONF_NAME"
CONFIG_ID_AID = "AID"
CONFIG_ID_CAPK = "CAPK"
CONFIG_ID_DRL = "DRL"
CONFIG_ID_EXCEPTION_FILE = "EXCEPTION_FILE"
CONFIG_ID_TERM = "TERM"
CONFIG_ID_VERSION = "VERSION"

# response codes used by the CTEL2 *_ACK signals
RESP_YES = "YES"
RESP_NO = "NO"
RESP_NOTSUPPORTED = "NOTSUPPORTED"

# android -> host
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
