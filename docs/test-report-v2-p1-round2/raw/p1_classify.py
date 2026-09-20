"""Classify each pytest failure block in the raw full-run log by root cause."""
import io, re, sys

path = sys.argv[1]
text = io.open(path, encoding="utf-8", errors="replace").read()

# pytest failure sections start with a line of underscores around the test name
blocks = re.split(r"\n_{5,} (?P<name>[^_].*?) _{5,}\n", text)
# blocks[0] is preamble, then name, body, name, body, ...
rows = []
for i in range(1, len(blocks) - 1, 2):
    name = blocks[i].strip()
    body = blocks[i + 1]
    body = body.split("=============================== warnings summary")[0]
    errs = [ln for ln in body.splitlines() if ln.startswith("E ")]
    root = errs[-1][2:].strip() if errs else "(no E-line)"
    # look for the frame that raised
    loc = ""
    for ln in reversed(body.splitlines()):
        m = re.match(r"(tests\\[^:]+\.py:\d+): ", ln)
        if m:
            loc = m.group(1)
            break
    rows.append((name, loc, root))

print(f"total failure blocks: {len(rows)}\n")
for name, loc, root in rows:
    print(f"* {name}")
    print(f"    at : {loc}")
    print(f"    why: {root[:300]}")
    print()
