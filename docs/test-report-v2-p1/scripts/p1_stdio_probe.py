"""P1 diagnostic: reproduce the two failing real-subprocess server tests.

Variants isolate the stdio wiring (PIPE undrained / PIPE drained / file) while
keeping the exact command line, cwd and env of the failing tests.
"""
import os, socket, subprocess, sys, tempfile, threading, time, urllib.request

REPO = r"E:\Work\ZT\card_simulator_host"
TOKENS = os.path.join(REPO, "tokens.json")
CASES = os.path.join(REPO, "cases")
STATIC = os.path.join(REPO, "frontend", "dist")
UVICORN_MARK = "Uvicorn running"


def free_ports(n=2):
    socks = [socket.socket() for _ in range(n)]
    try:
        for s in socks:
            s.bind(("127.0.0.1", 0))
        return [s.getsockname()[1] for s in socks]
    finally:
        for s in socks:
            s.close()


def probe(variant, cwd, extra_args):
    web, dev = free_ports(2)
    tmp = tempfile.mkdtemp(prefix=f"p1_{variant}_")
    env = dict(os.environ, PYTHONPATH=os.path.join(REPO, "backend"))
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    cmd = [sys.executable, "-m", "emv_host", "serve",
           "--host", "127.0.0.1", "--port", str(web),
           "--device-port", str(dev),
           "--runs", os.path.join(tmp, "runs"),
           "--log-dir", os.path.join(tmp, "logs"),
           "--log-level", "info"] + extra_args
    outpath = os.path.join(tmp, "stdout.txt")
    kw = dict(cwd=cwd, env=env, creationflags=flags)
    buf = []
    drained = None
    if variant in ("pipe", "drained"):
        kw.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    else:
        kw.update(stdout=open(outpath, "wb"), stderr=subprocess.STDOUT)
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, **kw)
    if variant == "drained":
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                buf.append(chunk)
        drained = threading.Thread(target=reader, daemon=True)
        drained.start()
    ok, died, last = False, None, None
    for _ in range(120):
        if proc.poll() is not None:
            died = proc.returncode
            break
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{web}/api/health", timeout=1):
                ok = True
                break
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(0.2)
    elapsed = time.monotonic() - t0
    if variant in ("pipe", "drained"):
        if proc.poll() is None:
            proc.kill()
        if drained is not None:
            stop.set()
            drained.join(timeout=2)
        text = b"".join(buf).decode(errors="replace")
    else:
        if proc.poll() is None:
            proc.kill()
        text = open(outpath, "rb").read().decode(errors="replace")
    pre, sep, _ = text.partition(UVICORN_MARK)
    nlog = len(pre.encode("utf-8", errors="replace"))
    lastline = [ln for ln in text.strip().splitlines() if ln.strip()]
    print(f"--- variant={variant} cwd={cwd}")
    print(f"    ok={ok} died={died} elapsed={elapsed:.1f}s "
          f"bytes_before_{UVICORN_MARK!r}={nlog}{'' if sep else ' (never reached)'}")
    print(f"    last_captured_line: {lastline[-1][:160] if lastline else '(none)'}")
    print(f"    last_probe_err={last}")
    print()


if __name__ == "__main__":
    variant = sys.argv[1]
    where = sys.argv[2] if len(sys.argv) > 2 else "root"
    if where == "root":
        probe(variant, REPO, ["--cases", CASES, "--tokens", TOKENS, "--static", STATIC])
    else:
        probe(variant, os.path.join(REPO, "backend"), [])
