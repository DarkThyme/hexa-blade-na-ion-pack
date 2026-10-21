"""
Restricted loader for EVBattery snippets (legacy torch.save files containing only numpy data).

Each .pkl is a sequence of pickles: magic number, protocol version, sys_info, then the payload
(a tuple of a (128, 8) float64 array and an OrderedDict of metadata), then an empty storage-key list.
Static inspection (inspect_pickles.py) shows the payload references only these globals, so the
unpickler allows exactly them and raises on anything else. Persistent IDs (torch tensors) are refused.

Columns (from the authors' code, GDN_battery/data/battery/list.txt):
    volt, current, soc, max_single_volt, min_single_volt, max_temp, min_temp, timestamp
"""

import io
import pickle
import tarfile
from collections import OrderedDict

import numpy as np

COLUMNS = ["volt", "current", "soc", "max_single_volt", "min_single_volt", "max_temp", "min_temp", "timestamp"]



def _codecs_encode(obj, encoding="latin1"):
    """Safe stand-in for _codecs.encode used by numpy pickles to carry raw bytes."""
    if not isinstance(obj, str):
        raise pickle.UnpicklingError("_codecs.encode on non-string refused")
    return obj.encode(encoding)


def _numpy_internal(name: str):
    """numpy's own array / scalar reconstructors (build from dtype + raw bytes, no code execution)."""
    from numpy._core import multiarray  # numpy >= 2

    return getattr(multiarray, name)


class Restricted(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) == ("_codecs", "encode"):
            return _codecs_encode
        if module in ("numpy.core.multiarray", "numpy._core.multiarray") and name in ("_reconstruct", "scalar"):
            return _numpy_internal(name)
        if (module, name) in (("collections", "OrderedDict"), ("numpy", "dtype"), ("numpy", "ndarray")):
            return {"OrderedDict": OrderedDict, "dtype": np.dtype, "ndarray": np.ndarray}[name]
        raise pickle.UnpicklingError(f"refused global {module}.{name}")

    def persistent_load(self, pid):
        raise pickle.UnpicklingError("persistent ids (tensors) refused")


def load_snippet(data: bytes) -> tuple[np.ndarray, dict]:
    f = io.BytesIO(data)
    objs = []
    while f.tell() < len(data):
        objs.append(Restricted(f).load())
    # objs: magic, protocol, sys_info, payload, storage_keys
    payload = objs[3]
    arr, meta = payload if isinstance(payload, tuple) else (payload[0], payload[1])
    arr = np.asarray(arr, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != len(COLUMNS):
        raise ValueError(f"unexpected array shape {arr.shape}")
    return arr, dict(meta)


REFUSED: list[str] = []


def iter_archive(path: str, limit: int | None = None):
    """Yield (array, meta) for each snippet, streaming from the .tar.gz (nothing extracted)."""
    with tarfile.open(path, "r:gz") as tf:
        k = 0
        for m in tf:
            if not m.isfile() or not m.name.endswith(".pkl"):
                continue
            try:
                arr, meta = load_snippet(tf.extractfile(m).read())
            except (pickle.UnpicklingError, ValueError) as e:
                REFUSED.append(f"{m.name}: {e}")
                continue
            yield arr, meta
            k += 1
            if limit and k >= limit:
                return


def read_labels(path: str) -> dict[int, int]:
    import csv

    with tarfile.open(path, "r:gz") as tf:
        for m in tf:
            if m.name.endswith("label.csv"):
                rows = csv.DictReader(io.TextIOWrapper(tf.extractfile(m), encoding="utf-8"))
                return {int(r["car"]): int(r["label"]) for r in rows}
    return {}


if __name__ == "__main__":
    import sys

    # self-test: a malicious pickle must be refused
    evil = b"cos\nsystem\n(S'echo pwned'\ntR."
    try:
        Restricted(io.BytesIO(evil)).load()
        raise SystemExit("SECURITY FAIL: restricted unpickler executed os.system")
    except pickle.UnpicklingError as e:
        print("self-test ok, refused:", e)
    for arr, meta in iter_archive(sys.argv[1], limit=3):
        print(arr.shape, {k: (v if not hasattr(v, "shape") else v.shape) for k, v in meta.items()})
        print("  first row:", dict(zip(COLUMNS, np.round(arr[0], 3))))
        print("  last row: ", dict(zip(COLUMNS, np.round(arr[-1], 3))))
