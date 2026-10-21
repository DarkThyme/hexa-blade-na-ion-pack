"""
Release integrity: hash the artifacts of a release into its own manifest, and verify a checkout against it.

  python release_integrity.py build v1.0.1                 write release_manifest_v1.0.1.json (run on the commit to be tagged)
  python release_integrity.py verify release_manifest_v1.0.1.json   exit 1 on any mismatch or missing file

This is separate from the historical experiment audit (analysis/estimator/freeze.py verify), which checks the archived
v1.0 frozen artifacts against the original freeze manifest and never uses this file.

Text files are hashed with CRLF normalised to LF, so a Windows and a Linux checkout of the same commit agree;
binary files (models, figures, PDF, Word) are hashed as raw bytes.
"""

import fnmatch
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEXT = {".py", ".json", ".md", ".txt", ".yml", ".yaml", ".js", ".csv", ".toml", ".rs", ".html", ".ps1"}
# what a release vouches for: the estimator study (code, models, calibration, report, figures, archive), every
# analysis report, the project report and the provenance log
PATTERNS = ["analysis/estimator/*", "analysis/*/REPORT.md", "analysis/validation/MODEL_CARD.md",
            "docs/Hexa-Blade_Project_Report.pdf", "docs/Hexa-Blade_Project_Report.docx", "PROVENANCE_LOG.md"]


def digest(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in TEXT and "frozen_v1.0" not in path.parts:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def tracked(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout.split("\n")
    return sorted(p for p in out if p and any(fnmatch.fnmatch(p, pat) for pat in PATTERNS))


def build(version: str, root: Path | None = None) -> Path:
    root = root or ROOT
    files = tracked(root)
    manifest = {"release": version, "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "hashing": "sha256; text files with CRLF normalised to LF (except the byte-exact frozen_v1.0 archive)",
                "sha256": {p: digest(root / p) for p in files}}
    out = root / f"release_manifest_{version}.json"
    out.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out.name}: {len(files)} files")
    return out


def verify(manifest_path: Path, root: Path | None = None) -> bool:
    root = root or ROOT
    m = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    missing = [p for p in m["sha256"] if not (root / p).is_file()]
    bad = [p for p, h in m["sha256"].items() if p not in missing and digest(root / p) != h]
    if missing:
        print("MISSING:", missing)
    if bad:
        print("CHANGED:", bad)
    if not (missing or bad):
        print(f"release {m['release']} integrity OK: all {len(m['sha256'])} artifacts match")
    return not (missing or bad)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "build":
        build(argv[1])
    elif len(argv) == 2 and argv[0] == "verify":
        if not verify(Path(argv[1])):
            print("Verification Failure: Hash discrepancies found.")
            sys.exit(1)
        sys.exit(0)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
