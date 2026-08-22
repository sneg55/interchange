"""Registry and feed sources backed by the committed snapshot.

Reads `tests/fixtures/`, written by `scripts/capture_fixtures.py`. This is what
lets the fleet be built and tested with no cloud access and no network, and it is
the same mechanism behind the demo fallback in section 10.

Checksums are verified on load. A fixture that has been corrupted or hand-edited
raises rather than quietly feeding wrong bytes into a test that then "passes",
which would be the same class of error the whole product exists to catch.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from .fetch_result import FetchResult

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


class FixtureError(RuntimeError):
    """A fixture is missing, unreadable, or does not match its recorded hash."""


class FixtureSet:
    """Loads and verifies the snapshot once, then serves it."""

    def __init__(self, fixture_dir: Path | None = None) -> None:
        self.dir = fixture_dir or FIXTURE_DIR
        manifest_path = self.dir / "manifest.json"
        if not manifest_path.exists():
            raise FixtureError(
                f"no snapshot at {manifest_path}. Run: python3 scripts/capture_fixtures.py"
            )
        with manifest_path.open() as fh:
            self.manifest: dict[str, Any] = json.load(fh)
        self.captured_at: str = self.manifest["captured_at"]
        # url -> manifest entry, so a FeedSource can answer by URL exactly as the
        # live one does and callers need no special-casing.
        self._by_url: dict[str, dict[str, Any]] = {
            entry["url"]: entry for entry in self.manifest["feeds"].values()
        }

    def _load(self, entry: dict[str, Any]) -> bytes:
        path = self.dir / entry["path"]
        try:
            # GzipFile rather than gzip.open: the latter's overloads widen the
            # return to str | bytes, which then fails the hash call downstream.
            with gzip.GzipFile(path, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            raise FixtureError(f"cannot read {path}: {exc}") from exc
        actual = hashlib.sha256(raw).hexdigest()
        if actual != entry["sha256"]:
            raise FixtureError(
                f"{path} does not match its recorded hash "
                f"({entry['sha256'][:12]} != {actual[:12]}). "
                "Re-capture, or investigate why it changed."
            )
        return raw

    def registry(self) -> list[dict[str, Any]]:
        return json.loads(self._load(self.manifest["registry"]))

    def feed_urls(self) -> list[str]:
        return sorted(self._by_url)

    def entry_for_url(self, url: str) -> dict[str, Any] | None:
        return self._by_url.get(url)

    def body_for_url(self, url: str) -> dict[str, Any]:
        entry = self._by_url.get(url)
        if entry is None:
            raise FixtureError(f"no fixture captured for {url}")
        return json.loads(self._load(entry))

    def schema(self, version: str, member: str) -> dict[str, Any]:
        entry = self.manifest["schemas"].get(f"{version}/{member}")
        if entry is None:
            raise FixtureError(f"no schema fixture for {version}/{member}")
        return json.loads(self._load(entry))


class FixtureRegistrySource:
    """RegistrySource backed by the snapshot."""

    def __init__(self, fixtures: FixtureSet | None = None) -> None:
        self._fixtures = fixtures or FixtureSet()

    def active_entries(self) -> list[dict[str, Any]]:
        return [r for r in self._fixtures.registry() if r.get("active")]


class FixtureFeedSource:
    """FeedSource backed by the snapshot.

    Supports two behaviours a live source has and a naive stub would not:

    - **Conditional requests.** Passing the etag this source previously issued
      yields a 304, so the carry-forward path in section 6.2 and the recovery
      rule in section 6.4 are exercisable offline. Without this, the deadlock
      that round three found (a 304-forever publisher never recovering) could
      not have a regression test.
    - **Injected failures.** `fail_urls` makes a publisher unreachable on demand,
      which is what R1, backoff and the Minnesota/New Mexico paths need.
    """

    def __init__(
        self, fixtures: FixtureSet | None = None, fail_urls: set[str] | None = None
    ) -> None:
        self._fixtures = fixtures or FixtureSet()
        self.fail_urls: set[str] = set(fail_urls or ())

    def _etag_for(self, url: str) -> str:
        entry = self._fixtures.entry_for_url(url)
        # The content hash IS the etag: an unchanged fixture must produce an
        # unchanged validator, or conditional-GET tests would be meaningless.
        return f'W/"{entry["sha256"][:16]}"' if entry else 'W/"unknown"'

    def _last_modified_for(self, url: str) -> str:
        entry = self._fixtures.entry_for_url(url)
        return (entry or {}).get("captured_at", "")

    def fetch(
        self,
        url: str,
        etag: str | None = None,
        last_modified: str | None = None,
        timeout: float = 30.0,
    ) -> FetchResult:
        del timeout  # fixtures are instant; kept for interface parity
        if url in self.fail_urls:
            return FetchResult.failure(
                "Interchange did not reach the feed: this offline run marks it unreachable",
                origin="PUBLISHER",
            )
        entry = self._fixtures.entry_for_url(url)
        if entry is None:
            return FetchResult.failure(
                # Named as Interchange's own gap, not as the publisher's. This
                # read `NoFixture: nothing captured for <url>` on the operator
                # console thirteen times over, as though the publisher had done
                # something, and a notice went to the registry owner asserting
                # the feed was unreachable on the strength of it.
                f"Interchange has no captured response for {url} in this offline run, "
                f"so this poll was never attempted against the publisher",
                origin="INTERCHANGE",
            )
        current = self._etag_for(url)
        stamp = self._last_modified_for(url)
        # ETag takes precedence over Last-Modified, as RFC 9110 requires. Honouring
        # either would let a stale ETag plus a matching Last-Modified hide changed
        # content, which is the one thing a conditional request must never do.
        if etag is not None:
            if etag == current:
                return FetchResult.unchanged(current, last_modified=stamp)
        elif last_modified is not None and last_modified == stamp:
            return FetchResult.unchanged(current, last_modified=stamp)
        try:
            body = self._fixtures.body_for_url(url)
        except FixtureError as exc:
            return FetchResult.failure(
                f"Interchange could not read its captured response: {exc}", origin="INTERCHANGE"
            )
        return FetchResult(
            status=200,
            etag=current,
            last_modified=stamp,
            body=body,
            wire_bytes=entry.get("bytes", 0),
        )
