"""Plan section 4.2 reverse check + 7.2 note: the v1.2 protocol hard switch.

The plan suggests temporarily editing mock_common.py's handshake to advertise
"1.1" and confirming the rejection.  Rather than mutating a repository file, this
probes the live host directly with crafted handshakes:

  * "1.1"  -> must be rejected (protocol_mismatch), client NOT registered
  * absent -> must be rejected (v1.2 no longer tolerates a missing version)
  * "1.2"  -> must be accepted and registered
  * "1.3"  -> must be accepted (forward compatible)

Prints one SUMMARY line.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, r"E:\Work\ZT\card_simulator_host\backend")

from emv_host.protocol.frame import encode_frame, read_frame  # noqa: E402
from emv_host.protocol.messages import make_frame  # noqa: E402
from l3_harness import EV, Harness, api  # noqa: E402

TOKENS = json.loads((Path(r"E:\Work\ZT\card_simulator_host") / "tokens.json")
                    .read_text(encoding="utf-8"))


async def probe(port: int, version, device_id: str) -> dict:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    kwargs = dict(client_type="android", token=TOKENS["android"],
                  device_id=device_id, app_version="probe-0.1",
                  device_model="probe")
    if version is not None:
        kwargs["protocol_version"] = version
    writer.write(encode_frame(make_frame("handshake", **kwargs)))
    await writer.drain()
    try:
        frame = await asyncio.wait_for(read_frame(reader), timeout=8)
        result = {"type": frame.get("type"), "code": frame.get("code"),
                  "message": frame.get("message"),
                  "protocol_version": frame.get("protocol_version"),
                  "session_id": frame.get("session_id")}
    except (asyncio.IncompleteReadError, ConnectionError) as exc:
        result = {"type": "eof", "error": type(exc).__name__}
    except asyncio.TimeoutError:
        result = {"type": "timeout"}
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001
        pass
    return result


async def main() -> int:
    h = Harness("handshake_hard_switch", serve_extra=["--log-level", "info"])
    try:
        h.start_host()
        out = {}
        for label, version in (("1.1", "1.1"), ("missing", None),
                               ("1.2", "1.2"), ("1.3", "1.3")):
            out[label] = await probe(h.dev_port, version, f"probe-{label}")
            h.note(f"probe {label} -> {out[label]}")
            await asyncio.sleep(0.4)
        h.settle(1.5, "for host log flush")
        hl = h.host_log()

        # registered state: after the probes only the 1.2/latest client may be on
        st, snap = api("/api/health")
        devs = (snap or {}).get("devices", {})
        android = devs.get("android", {})
        out["health_android"] = android

        h.ck('1.1 -> error protocol_mismatch', "protocol_mismatch",
             (out["1.1"] or {}).get("code"))
        h.ck('1.1 -> no session_id (not registered)', None,
             (out["1.1"] or {}).get("session_id"))
        h.ck('missing version -> rejected', "protocol_mismatch",
             (out["missing"] or {}).get("code"))
        h.ck('1.2 -> handshake_ack', "handshake_ack", (out["1.2"] or {}).get("type"))
        h.ck('1.2 -> ack echoes 1.2', "1.2",
             (out["1.2"] or {}).get("protocol_version"))
        h.ck('1.3 -> accepted (forward compatible)', "handshake_ack",
             (out["1.3"] or {}).get("type"))
        # the host logs the rejection *message*, not the error code
        h.sub("host: handshake rejected logged", hl, "handshake rejected from")
        h.sub("host: v1.1 not supported logged", hl, "protocol 1.1 not supported")
        h.sub("host: missing version logged", hl,
              "handshake must carry protocol_version=1.2")
        h.sub("host: handshake ok protocol=1.2", hl, "protocol=1.2")
        h.sub("host: handshake ok protocol=1.3", hl, "protocol=1.3")
        h.dump({"probes": out,
                "note": "probe replaces the plan's 'edit mock_common.py' step; "
                        "no repository file was modified"})
    finally:
        h.stop_all()
    print("\n================ summary ================")
    print(f"  [{'ok  ' if h.ok() else 'FAIL'}] handshake_hard_switch "
          f"{[c['name'] for c in h.checks if not c['ok']]}")
    (EV / "L3_handshake_summary.json").write_text(
        json.dumps({"ok": h.ok(), "checks": h.checks}, ensure_ascii=False,
                   indent=2), encoding="utf-8")
    return 0 if h.ok() else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
