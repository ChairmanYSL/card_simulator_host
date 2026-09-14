"""L4 section 5.1: inject frames with a valid length prefix but invalid JSON.

Handshakes as a normal android client, then for each of the first four bad
frames follows it with a valid `log` frame (proving the connection still works),
and finally sends the 5th bad frame expecting the host to drop the connection.

Run:  python inject_badframe.py <device-port> <token> <device-id>
Prints a JSON summary on the last line.
"""

from __future__ import annotations

import asyncio
import json
import struct
import sys
from pathlib import Path

BACKEND = Path(r"E:\Work\ZT\card_simulator_host\backend")
sys.path.insert(0, str(BACKEND))

from emv_host.protocol.frame import encode_frame, read_frame  # noqa: E402
from emv_host.protocol.messages import (  # noqa: E402
    MSG_HEARTBEAT,
    MSG_HEARTBEAT_ACK,
    PROTOCOL_VERSION_STRING,
    make_frame,
)

BAD = b"{not json"


def bad_frame() -> bytes:
    return struct.pack(">I", len(BAD)) + BAD


async def pump(reader, writer, seconds: float, seen: list) -> bool:
    """Read whatever arrives for a while, answering heartbeats.

    Returns True if the peer closed the connection while we were pumping.
    """
    end = asyncio.get_running_loop().time() + seconds
    while True:
        remaining = end - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        try:
            frame = await asyncio.wait_for(read_frame(reader), timeout=remaining)
        except asyncio.TimeoutError:
            return False
        except (asyncio.IncompleteReadError, ConnectionError):
            seen.append({"eof": True})
            return True
        seen.append({"type": frame.get("type")})
        if frame.get("type") == MSG_HEARTBEAT:
            writer.write(encode_frame(make_frame(
                MSG_HEARTBEAT_ACK, ref_msg_id=frame.get("msg_id"))))
            await writer.drain()


async def main() -> int:
    port = int(sys.argv[1])
    token = sys.argv[2]
    device_id = sys.argv[3]

    summary = {"handshake": None, "sent_bad": 0, "sent_good": 0,
               "connection_dropped": False, "read_events": []}
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    hs = make_frame("handshake", client_type="android", token=token,
                    device_id=device_id, app_version="injector-0.1",
                    device_model="injector",
                    protocol_version=PROTOCOL_VERSION_STRING)
    writer.write(encode_frame(hs))
    await writer.drain()
    ack = await read_frame(reader)
    summary["handshake"] = ack.get("type")
    print(f"[injector] handshake -> {ack.get('type')} "
          f"version={ack.get('protocol_version')}", flush=True)

    seen: list = []

    for i in range(4):
        writer.write(bad_frame())
        await writer.drain()
        summary["sent_bad"] += 1
        print(f"[injector] sent bad frame #{summary['sent_bad']}", flush=True)

        good = make_frame("log", msg_id=f"ok{i}", message=f"alive{i}")
        writer.write(encode_frame(good))
        await writer.drain()
        summary["sent_good"] += 1
        print(f"[injector] sent good frame alive{i}", flush=True)

        if await pump(reader, writer, 0.6, seen):
            summary["connection_dropped"] = True
            print("[injector] connection dropped before the 5th bad frame",
                  flush=True)
            break

    # 5th bad frame: expect the host to drop us
    writer.write(bad_frame())
    await writer.drain()
    summary["sent_bad"] += 1
    print(f"[injector] sent bad frame #{summary['sent_bad']} (expect drop)", flush=True)

    try:
        frame = await asyncio.wait_for(read_frame(reader), timeout=6.0)
        summary["read_events"].append({"after_5th": frame.get("type")})
        print(f"[injector] UNEXPECTED frame after 5th bad frame: "
              f"{frame.get('type')}", flush=True)
    except asyncio.TimeoutError:
        summary["read_events"].append({"after_5th": "timeout-no-drop"})
        print("[injector] no EOF within 6s (host did NOT drop?)", flush=True)
    except (asyncio.IncompleteReadError, ConnectionError) as exc:
        summary["connection_dropped"] = True
        summary["read_events"].append({"after_5th": f"eof:{type(exc).__name__}"})
        print(f"[injector] connection dropped by host ({type(exc).__name__})", flush=True)

    summary["read_events"].extend(seen)
    try:
        writer.close()
    except OSError:
        pass
    print("SUMMARY " + json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
