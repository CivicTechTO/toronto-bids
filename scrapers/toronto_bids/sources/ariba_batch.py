"""Batched capture for Ariba events over the 500 MB single-zip ceiling (#174).

Ariba hard-stops a single bundle at 500 MB and says so in the picker: "You must select
specific items and perform multiple downloads." This module is the "select specific items"
half -- pure logic over a 5-method Picker protocol, so it is testable without a browser.
The Playwright adapter lives in ariba_attachments.py.

Design: docs/superpowers/specs/2026-07-27-oversized-ariba-bundle-capture-design.md
"""
import json
from pathlib import Path

# Ariba's ceiling is 500 MB against its OWN computed total. 450 leaves headroom -- the one
# event we cannot re-run is not where we want to discover the boundary condition.
BATCH_THRESHOLD_MB = 450

MANIFEST_NAME = "manifest.json"


def partial_dir(dest_dir, document_number: str) -> Path:
    """Working directory for an in-progress capture.

    Deliberately OUTSIDE the canonical `Doc<n>.zip` namespace: capture_attachments decides
    what is already archived by testing for that file, so a partial capture must be invisible
    to it. That is what makes "resume next run" need no new caller state.
    """
    return Path(dest_dir) / ".partial" / f"Doc{document_number}"


def make_fingerprint(row_keys, file_count, total_mb) -> dict:
    """Identity of the event as we planned against it.

    The row-key list is the primary signal and is readable without selecting anything; the two
    counts are properties of the SELECTION (`Total Number` reads 0 with nothing ticked) and are
    captured during the select-all that detects the overflow.
    """
    return {"row_keys": list(row_keys), "file_count": file_count, "total_mb": total_mb}


def read_manifest(pdir) -> dict | None:
    path = Path(pdir) / MANIFEST_NAME
    if not path.exists():
        return None
    return json.loads(path.read_text())


def write_manifest(pdir, fingerprint: dict, batches, omitted) -> Path:
    pdir = Path(pdir)
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / MANIFEST_NAME
    path.write_text(json.dumps(
        {"fingerprint": fingerprint, "batches": [list(b) for b in batches],
         "omitted": list(omitted)}, indent=2))
    return path


def manifest_is_current(manifest, fingerprint: dict) -> bool:
    """Whether a manifest was written against this same version of the event.

    A mismatch means the City changed it (an addendum landed) and the partials describe a
    different event: discard and re-plan rather than merge batches from two versions.
    """
    if not manifest:
        return False
    return manifest.get("fingerprint") == fingerprint
