"""Security tests for the restricted EVBattery loader. Run: .venv/Scripts/python -m pytest -q"""

import io
import pickle
import sys
from collections import OrderedDict
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent / "analysis" / "evbattery"))
import safe_load  # noqa: E402


@pytest.mark.parametrize("payload", [
    b"cos\nsystem\n(S'echo pwned'\ntR.",                 # os.system
    b"cbuiltins\neval\n(S'1+1'\ntR.",                     # eval
    b"csubprocess\nPopen\n(S'calc'\ntR.",                 # subprocess
])
def test_refuses_code_execution(payload):
    with pytest.raises(pickle.UnpicklingError):
        safe_load.Restricted(io.BytesIO(payload)).load()


def test_refuses_persistent_ids():
    class P(pickle.Pickler):
        def persistent_id(self, obj):
            return "storage" if isinstance(obj, bytes) else None

    buf = io.BytesIO()
    P(buf, protocol=2).dump([b"x"])
    with pytest.raises(pickle.UnpicklingError):
        safe_load.Restricted(io.BytesIO(buf.getvalue())).load()


def test_decodes_snippet_shaped_like_evbattery():
    arr = np.arange(128 * 8, dtype=np.float64).reshape(128, 8)
    meta = OrderedDict(label="10", car=7, charge_segment="3", mileage=np.float64(1234.5), capacity=0)
    parts = [pickle.dumps(119547037146038801333356, protocol=2), pickle.dumps(1001, protocol=2),
             pickle.dumps({"protocol_version": 1001}, protocol=2), pickle.dumps((arr, meta), protocol=2),
             pickle.dumps([], protocol=2)]
    a, m = safe_load.load_snippet(b"".join(parts))
    assert a.shape == (128, 8) and np.array_equal(a, arr)
    assert m["car"] == 7 and float(m["mileage"]) == 1234.5
