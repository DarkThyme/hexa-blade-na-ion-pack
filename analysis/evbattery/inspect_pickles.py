"""Static inspection of EVBattery pickles: lists the GLOBAL/STACK_GLOBAL references without executing them.
Run:  .venv/Scripts/python -I analysis/evbattery/inspect_pickles.py <archive.tar.gz> [n_samples]"""

import collections
import pickletools
import sys
import tarfile

archive = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
globals_seen = collections.Counter()
with tarfile.open(archive, "r:gz") as tf:
    k = 0
    for m in tf:
        if not m.name.endswith(".pkl"):
            if m.name.endswith(".csv"):
                print("CSV", m.name, "\n", tf.extractfile(m).read().decode()[:600])
            continue
        data = tf.extractfile(m).read()
        strings = []
        for op, arg, _ in pickletools.genops(data):
            if op.name in ("SHORT_BINUNICODE", "BINUNICODE", "UNICODE"):
                strings.append(arg)
            if op.name == "GLOBAL":
                globals_seen[arg.replace(" ", ".")] += 1
            if op.name == "STACK_GLOBAL":
                globals_seen[".".join(strings[-2:])] += 1
        if k == 0:
            print("first file:", m.name, len(data), "bytes")
            pickletools.dis(data[:1500], annotate=0) if len(data) < 1500 else None
            print("strings:", strings[:40])
        k += 1
        if k >= n:
            break
print("\nglobals referenced across", k, "files:")
for g, c in globals_seen.most_common():
    print(f"  {c:5d}  {g}")
