"""Retaining the last body each publisher served, so a 304 still has one.

Sections 6.2 and 8. A `304 Not Modified` is the publisher saying the copy you
already hold is current, which is only worth anything to a system that still
holds it. The poller used to answer a 304 with `(observation, None)`: the
observation carried its counts forward correctly and the body was simply gone,
so the publisher's zones dropped out of the merge. One cycle looked fine. From
the second onward a fleet of publishers doing exactly the right thing merged
nothing at all.

Section 8 already describes the production storage: body snapshots written on
content-hash change, gzipped, to GCS, retained 30 days. This module is the local
implementation of that contract and the read shape the poller needs. It holds
bodies in memory rather than gzipping them to disk, because the caller that owns
persistence across runs is the fleet runner, the same way it owns the
CanonicalIdentity map.

Written on content-hash change only, as section 8 says. That is not a size
optimisation here: rewriting an identical body on every poll would make the
retention window measure poll frequency rather than content age.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any


class InMemoryBodySnapshots:
    """BodySnapshots held for the life of the process.

    One body per publisher, the most recent. The poller only ever asks for the
    latest, because a 304 refers to whatever the publisher last served and
    nothing older is answerable.
    """

    def __init__(self) -> None:
        self._bodies: dict[str, tuple[dict[str, Any], str]] = {}

    def latest(self, publisher_key: str) -> tuple[dict[str, Any], str] | None:
        return self._bodies.get(publisher_key)

    def record(self, publisher_key: str, body: dict[str, Any], content_hash: str) -> None:
        held = self._bodies.get(publisher_key)
        if held is not None and held[1] == content_hash:
            # Unchanged content is not re-recorded. Section 8 writes a snapshot
            # on content-hash change, and a store that rewrote on every poll
            # would date its entries by when it last looked rather than by when
            # the publisher last changed anything.
            return
        self._bodies[publisher_key] = (body, content_hash)

    def __len__(self) -> int:
        return len(self._bodies)


class FileBodySnapshots:
    """BodySnapshots that outlive the process. Gzipped to disk, per section 8.

    The in-memory one is correct for a run that starts and ends. A fleet polling
    for weeks restarts, and on the poll after a restart every well-behaved
    publisher answers the conditional request with a 304 that this system can no
    longer honour: the loaded history says a body was measured, the retained body
    is gone, and the merge loses that publisher's zones. That is the exact defect
    section 6.2 was written to close, reappearing once per restart rather than
    once per fleet.

    The content hash lives in its own tiny file beside the body. `record` is
    called on every successful poll and must decide whether anything changed;
    decompressing a seven-thousand-feature feed to answer that would cost more
    than the write it is avoiding.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _paths(self, publisher_key: str) -> tuple[Path, Path]:
        # Hashed rather than sanitised. Publisher keys carry spaces, pipes and
        # punctuation from the registry, and any scheme that strips characters
        # can map two publishers onto one file, which would serve one
        # organization's zones under another's name.
        stem = hashlib.sha256(publisher_key.encode()).hexdigest()[:32]
        return self.root / f"{stem}.json.gz", self.root / f"{stem}.hash"

    def latest(self, publisher_key: str) -> tuple[dict[str, Any], str] | None:
        body_path, hash_path = self._paths(publisher_key)
        try:
            content_hash = hash_path.read_text().strip()
            with gzip.open(body_path, "rt") as handle:
                body = json.load(handle)
        except (OSError, json.JSONDecodeError):
            # Nothing retained, or what was retained is unreadable. Both mean the
            # poller has no body to answer a 304 with, which it already handles
            # by dropping to no body rather than by guessing.
            return None
        return body, content_hash

    def record(self, publisher_key: str, body: dict[str, Any], content_hash: str) -> None:
        body_path, hash_path = self._paths(publisher_key)
        if hash_path.exists() and hash_path.read_text().strip() == content_hash:
            return
        # Body first, hash second, both through a temporary file. The hash is
        # what `latest` trusts, so writing it before the body it describes would
        # leave a window where a crash yields a hash pointing at the previous
        # body, and the poller would serve content no observation measured.
        tmp = body_path.with_suffix(".gz.tmp")
        with gzip.open(tmp, "wt") as handle:
            json.dump(body, handle)
        tmp.replace(body_path)
        hash_tmp = hash_path.with_suffix(".hash.tmp")
        hash_tmp.write_text(content_hash)
        hash_tmp.replace(hash_path)
