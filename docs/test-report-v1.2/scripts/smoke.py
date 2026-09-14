"""Smoke test for l3_harness: start host, health, graceful stop, lock handling."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from l3_harness import EV, REPO, Harness, api  # noqa: E402

out = {}

lock = REPO / "runs" / ".host.lock"
out["lock_before"] = lock.read_text(encoding="utf-8").strip() if lock.is_file() else None

h = Harness("smoke")
h.start_host()

st, body = api("/api/health")
out["health_status"] = st
out["health"] = body

out["lock_during"] = lock.read_text(encoding="utf-8").strip() if lock.is_file() else None

ptimeout = 3
h.host.stop()
out["host_returncode"] = h.host.popen.returncode

out["lock_after"] = lock.read_text(encoding="utf-8").strip() if lock.is_file() else None

log = h.host_log()
out["hostlog_has_starting"] = "starting: web" in log
out["hostlog_has_stale_lock"] = "removing stale lock" in log
out["hostlog_has_bye"] = "bye" in log
out["hostlog_tail"] = log.splitlines()[-12:]

(EV / "L3_smoke.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
print(json.dumps({k: v for k, v in out.items() if k != "hostlog_tail"},
                 ensure_ascii=False, indent=2))
print("--- host log tail ---")
for line in out["hostlog_tail"]:
    print("  " + line)
