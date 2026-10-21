# Archived frozen artifacts (historical experiment audit)

Exact bytes of the 10 files hashed in `../freeze_manifest.json` (frozen 2026-10-21 00:59:27, before the final-family
run). Check them with `python freeze.py verify` from `analysis/estimator/`; it exits 1 on any mismatch or missing file.

- Never edit these files or the manifest. The working copies one level up may change in later releases; those are
  covered by the release-integrity manifest in the repository root (`release_integrity.py`).
- `closed_loop.py` here was **reconstructed** for v1.0.1: v1.0 shipped a working copy with a three-line post-freeze edit
  and with line endings normalised by git. Reverting the documented edit and restoring CRLF line endings reproduces the
  frozen SHA-256 exactly. All other files are the original bytes. Details: `../../../PROVENANCE_LOG.md`.
- `.gitattributes` stores this folder byte for byte (no line-ending conversion).
