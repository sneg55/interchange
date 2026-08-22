"""Writing the merged feed out, once it has passed its own gate. Section 6.8.

Extracted from `fleet_cycle` on the file size limit, and the seam is a real one:
everything here happens after the republisher has already decided, and none of it
may change that decision.
"""

from __future__ import annotations

from typing import Any

from src.constants.error_ids import ErrorIds


def publish_feed(sink: Any, output: Any, cycle_id: str) -> None:
    """Write the feed and stamp the artifact with where it went and how big.

    Gated on `output.published`, which is the republisher's verdict after
    validating against the official WZDx schema. A sink that wrote regardless
    would route around the one invariant this project cannot ship without: a
    merged feed that would quarantine its own publisher is not published.

    A failed upload is recorded, not raised. The cycle's observations and trust
    decisions are already correct and discarding them over a storage error would
    lose reliability history to fix nothing. `feed_uri` stays null, which reads
    as "not written" exactly as it does when no sink is configured, and the error
    id says which of the two it was.
    """
    if sink is None or not output.published or output.feed is None:
        return
    try:
        uri, size = sink.put(output.feed, cycle_id)
    except Exception as exc:  # noqa: BLE001 - a failed upload is not a failed cycle
        output.artifact.validation_result["publish_error"] = (
            f"{ErrorIds.PUB_SINK_FAILED}: {type(exc).__name__}: {exc}"
        )
        return
    output.artifact.feed_uri = uri
    output.artifact.byte_size = size
