"""v1.0.1 reporting patch: summarise() on realistic closed-loop run records, and the exit status of both verifiers
(historical experiment audit and release integrity). Run: .venv/Scripts/python -m pytest -q"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "analysis" / "estimator"))
import freeze  # noqa: E402
import release_integrity  # noqa: E402
import report  # noqa: E402

SOLVER_FAIL = "plant solver failed: input set 0: IDAGetDky: IDA_BAD_K"


def record(controller="ml B, calibrated 20 mV", status="reached 90 %", t90=None, below0_s=0.0, min_mv=20.0, p99=5.0):
    """A closed-loop result row shaped like closed_loop.py writes it: completion is a bool, unfinished runs have no time."""
    completed = status == "reached 90 %"
    return {"controller": controller, "kind": "ml", "margin_mv": 20.0, "combo": [0.8, 1.3, 1.0], "temp": 0.0,
            "status": status, "completed": completed, "t90_min": t90 if completed else None, "soc_end": 0.9 if completed else 0.7,
            "true_min_anode_mv": min_mv, "below_15mv_s": 4.0 if min_mv < 15 else 0.0, "below_0mv_s": below0_s,
            "opt_err_p99_near_mv": p99, "opt_err_max_near_mv": p99 + 2, "max_c": 2.1, "wall_s": 30.0}


def one(rows):
    (s,) = report.summarise(rows)
    return s


def test_no_records_gives_an_empty_summary():
    assert report.summarise([]) == []


def test_records_but_none_completed():
    s = one([record(status="time limit"), record(status="stalled"), record(status=SOLVER_FAIL)])
    assert s["t90_median"] is None and s["done"] == 0 and s["n"] == 3
    assert (s["time_limit"], s["stalled"], s["solver_failed"]) == (1, 1, 1)


def test_odd_number_of_completed_runs():
    assert one([record(t90=t) for t in (90.0, 30.0, 60.0)])["t90_median"] == 60.0


def test_even_number_of_completed_runs_averages_the_two_middle_runs():
    # the v1.0 slice t[len(t) // 2] returned the upper middle run (60 min) here
    assert one([record(t90=t) for t in (90.0, 30.0, 60.0, 40.0)])["t90_median"] == pytest.approx(50.0)


def test_only_completed_runs_enter_the_median():
    rows = [record(t90=80.0), record(status="stalled"), record(t90=20.0), record(status=SOLVER_FAIL),
            record(status="time limit"), record(t90=50.0)]
    s = one(rows)
    assert (s["t90_median"], s["done"], s["n"]) == (50.0, 3, 6)


def test_completion_uses_the_boolean_field_not_string_truthiness():
    r = record(t90=40.0)
    r["completed"] = "False"   # a truthy string must not count as completed
    s = one([r, record(t90=70.0)])
    assert (s["done"], s["t90_median"]) == (1, 70.0)


def test_safety_columns_use_all_runs():
    rows = [record(t90=60.0, below0_s=42.0, min_mv=-8.2, p99=22.6), record(status="stalled", below0_s=10.0, min_mv=-1.0, p99=30.0)]
    s = one(rows)
    assert s["runs_below0"] == 2 and s["max_below0_min"] == pytest.approx(0.7) and s["worst_mv"] == -8.2 and s["opt_p99"] == 30.0


def test_table_labels_completion_conditionally():
    rows = [record(t90=60.0), record(status="time limit")] + [record("map, 15 mV", t90=t) for t in (50.0, 70.0)]
    text = "\n".join(report.table(report.summarise(rows)))
    assert "Completed: N/M; median among completed runs" in text
    assert "| 1/2; 60 min | 1 time limit |" in text and "| 2/2; 60 min | — |" in text


# ---------------------------------------------------------------- historical audit (freeze.py verify)

@pytest.fixture
def archive(tmp_path, monkeypatch):
    content = b"x = 1\r\n"
    (tmp_path / freeze.ARCHIVE).mkdir()
    (tmp_path / freeze.ARCHIVE / "frozen.py").write_bytes(content)
    (tmp_path / "freeze_manifest.json").write_text(json.dumps(
        {"frozen_at": "test", "sha256": {"frozen.py": hashlib.sha256(content).hexdigest()}}))
    monkeypatch.setattr(freeze.C, "HERE", tmp_path)
    return tmp_path / freeze.ARCHIVE


def exit_code(fn, *args):
    with pytest.raises(SystemExit) as e:
        fn(*args)
    return e.value.code


def test_historical_audit_passes_on_exact_bytes(archive):
    assert exit_code(freeze.verify_main) == 0


def test_historical_audit_fails_on_a_line_ending_change(archive, capsys):
    (archive / "frozen.py").write_bytes(b"x = 1\n")
    assert exit_code(freeze.verify_main) == 1
    assert "Verification Failure: Hash discrepancies found." in capsys.readouterr().out


def test_historical_audit_fails_on_a_missing_file(archive, capsys):
    (archive / "frozen.py").unlink()
    assert exit_code(freeze.verify_main) == 1
    assert "MISSING" in capsys.readouterr().out


def test_historical_audit_ignores_working_copies(archive):
    (archive.parent / "frozen.py").write_bytes(b"x = 2  # edited working copy\n")
    assert exit_code(freeze.verify_main) == 0


# ---------------------------------------------------------------- release integrity (release_integrity.py verify)

@pytest.fixture
def release(tmp_path):
    (tmp_path / "a.py").write_bytes(b"print(1)\r\n")
    (tmp_path / "fig.png").write_bytes(b"\x89PNG\r\n")
    m = {"release": "test", "sha256": {"a.py": release_integrity.digest(tmp_path / "a.py"),
                                       "fig.png": release_integrity.digest(tmp_path / "fig.png")}}
    (tmp_path / "release_manifest_test.json").write_text(json.dumps(m))
    return tmp_path


def run_release_verify(root, monkeypatch):
    monkeypatch.setattr(release_integrity, "ROOT", root)
    with pytest.raises(SystemExit) as e:
        release_integrity.main(["verify", str(root / "release_manifest_test.json")])
    return e.value.code


def test_release_verify_passes_on_a_linux_style_checkout(release, monkeypatch):
    (release / "a.py").write_bytes(b"print(1)\n")   # same text with LF endings: text hashes ignore CRLF vs LF
    assert run_release_verify(release, monkeypatch) == 0


def test_release_verify_fails_on_changed_text(release, monkeypatch, capsys):
    (release / "a.py").write_bytes(b"print(2)\r\n")
    assert run_release_verify(release, monkeypatch) == 1
    assert "Verification Failure: Hash discrepancies found." in capsys.readouterr().out


def test_release_verify_fails_on_changed_binary(release, monkeypatch):
    (release / "fig.png").write_bytes(b"\x89PNG\n")   # binary files are hashed as raw bytes
    assert run_release_verify(release, monkeypatch) == 1


def test_release_verify_fails_on_missing_file(release, monkeypatch):
    (release / "fig.png").unlink()
    assert run_release_verify(release, monkeypatch) == 1
