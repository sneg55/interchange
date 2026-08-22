"""Screen a cycle's distinct free text concurrently, before the serial pass.

Purely an optimisation, and deliberately shaped so it cannot become anything
else: it decides no verdicts of its own, every caller still goes through
`ScreeningGate.screen`, and deleting this module changes wall-clock and nothing
else. `screen` simply finds the answer already cached.

It exists because a real screener is a network round trip. Measured at 0.191s per
call against Model Armor, and one live cycle carries roughly ten thousand
DISTINCT strings once repeats are collapsed: New York DOT alone serves 6,594
features holding 549 distinct road names and 1,571 distinct descriptions.
Serially that is half an hour of a fifteen minute cycle spent almost entirely
waiting, and switching the fleet from fail-closed to Model Armor turned a nine
minute cycle into one that had not finished in twenty-nine.

Failures are dropped rather than recorded. An outage has to reach the fail-closed
path in `screen`, with its own error id and its own incident, and swallowing one
into a warmed cache is exactly the "not checked, stored as checked" this whole
system exists to prevent.
"""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .gate import ScreeningGate

# Model Armor is a network round trip, so a cycle's screening cost is wall-clock
# waiting rather than compute. Sixteen keeps a fifteen minute cadence comfortable
# without being an unannounced load test against someone else's quota.
WARM_WORKERS = 16


def prewarm(gate: ScreeningGate, texts: Iterable[tuple[str | None, str]], at: str) -> int:
    """Screen the distinct texts among `(text, publisher_key)`. Returns calls made."""
    pending: dict[str, tuple[str, str]] = {}
    for text, publisher_key in texts:
        if not text or text in pending or not gate.unscreened(text):
            continue
        pending[text] = (text, publisher_key)
    if not pending:
        return 0

    def screen_one(item: tuple[str, str]) -> tuple[str, str, Any, str] | None:
        text, publisher_key = item
        try:
            verdict, category = gate.screener.screen(text)
        except Exception:  # noqa: BLE001 - deliberately left for `screen` to fail closed
            return None
        return text, verdict, category, publisher_key

    done = 0
    with ThreadPoolExecutor(max_workers=WARM_WORKERS) as pool:
        for outcome in pool.map(screen_one, list(pending.values())):
            if outcome is None:
                continue
            text, verdict, category, publisher_key = outcome
            # Filed on the main thread once the pool has produced the verdict, so
            # the cache and the new-result list are only ever written from one.
            gate.record_verdict(text, publisher_key, verdict, category, at)
            done += 1
    return done


def free_text(feeds: dict[str, list[dict[str, Any]]]) -> list[tuple[str | None, str]]:
    """Every screenable string in a cycle's feeds, paired with its publisher.

    The same two fields `screen_sources` screens, read the same way. If one of
    them gains a third field, this misses it, and the only consequence is that
    the field is screened serially: correctness does not depend on this list
    being complete.
    """
    from src.entrypoints.cycle_sources import core

    found: list[tuple[str | None, str]] = []
    for publisher_key, features in feeds.items():
        for feature in features:
            if not isinstance(feature, dict):
                continue
            details = core(feature)
            found.append((details.get("description"), publisher_key))
            for name in details.get("road_names") or []:
                found.append((name if isinstance(name, str) else None, publisher_key))
    return found
