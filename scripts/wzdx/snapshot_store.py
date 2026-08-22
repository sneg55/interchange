"""Where a snapshot artifact lives on disk, and how it is checksummed.

Split out of `scripts/capture_fixtures.py` when that file reached the 300 line
limit. This is the storage half: paths, gzip, SHA-256. The capture half decides
*what* to fetch and *why*; this half only decides how bytes become a fixture and
how a fixture becomes bytes again.

Kept deliberately small and dependency-free so `--verify` can re-hash a snapshot
with no network and no registry access at all.
"""

from __future__ import annotations

import datetime
import gzip
import hashlib
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
MANIFEST = FIXTURE_DIR / "manifest.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def write_artifact(name: str, data: bytes) -> dict[str, Any]:
    """Store one artifact gzipped, return its manifest entry fields."""
    path = FIXTURE_DIR / f"{name}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 so an unchanged capture produces an identical file. Without it the
    # gzip header timestamp changes every run and every snapshot looks modified.
    with gzip.GzipFile(path, "wb", mtime=0) as fh:
        fh.write(data)
    return {
        "path": path.relative_to(FIXTURE_DIR).as_posix(),
        "sha256": sha256(data),
        "bytes": len(data),
    }


def read_artifact(rel_path: str) -> bytes:
    with gzip.open(FIXTURE_DIR / rel_path, "rb") as fh:
        return fh.read()
