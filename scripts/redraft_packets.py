#!/usr/bin/env python3
"""Re-render the text of evidence packets that have not been decided yet.

Run with: python3 scripts/redraft_packets.py --project ID [--apply]

Why this exists. A packet's two renderings are written once, when the packet is
opened, and nothing in the cycle ever rewrites them. So a defect in the renderer
is fixed for packets opened afterwards and never for the ones already in the
store, and the packets already in the store are the ones sitting in the queue
waiting for a human to approve and file them.

The defect this was written for: `_moment` printed a feed's own last-updated
time in the publisher's local wall time and labelled it UTC. Utah DOT's notice
said `2023-03-19 07:04:04 UTC` for an instant that is 13:04:04 UTC, while the
publisher page beside it said 13:04. Six hours, in the evidence a quarantine
rests on, in a document addressed to a named public agency.

What it will not touch:

- Anything not in DRAFT. An APPROVED or WITHHELD packet's text is the exact text
  a named human decided on, and `approved_rendering_sha256` is a hash of it.
  Rewriting that would silently break the one auditable act in this product, and
  a decision attesting to text nobody read is worse than a wrong timestamp.
- The registry rendering's PROSE. Re-drafting through a model would produce
  different wording for reasons unrelated to the fix, so a packet whose registry
  text came from a drafter is reported and left alone; a human should look.

Dry run by default. `--apply` writes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.entrypoints.cycle_docs import doc_id
from src.features.evidence.packet import EvidencePacket
from src.features.evidence.renderings import consumer_rendering, registry_rendering
from src.services.firestore_store import FirestoreStore

COLLECTION = "evidence_packets"

# What `registry_rendering` appends below a model's prose. Its presence is the
# only reliable evidence that a drafter wrote the text above it: a deterministic
# notice never carries it, whichever revision of the renderer produced it.
FACTS_MARKER = "Facts of record:"


def redraft(doc: dict) -> dict | None:
    """The fields to write, or None if this packet must not be rewritten.

    Pure, so the decision of what is safe to touch is testable without a store.
    """
    if doc.get("approval_state") != "DRAFT":
        return None
    packet = EvidencePacket.from_doc(doc)
    consumer = consumer_rendering(packet)
    registry = registry_rendering(packet)
    stored = doc.get("registry_rendering") or ""
    # The marker `registry_rendering` itself appends when a drafter succeeds,
    # rather than "the stored text differs from what we would render now".
    #
    # That second test is what the first version used, and the dry run against
    # production classified all fifteen drafts as model-written. They were not:
    # they are deterministic notices produced by the OLD renderer, and they
    # differ from today's output for exactly the reason this backfill exists.
    # The heuristic that was meant to protect a model's prose was instead
    # refusing to fix any packet at all, which is the failure mode where a
    # safety check quietly does nothing and reports success.
    drafted_by_model = FACTS_MARKER in stored
    changed = {}
    if doc.get("consumer_rendering") != consumer:
        changed["consumer_rendering"] = consumer
    if not drafted_by_model and doc.get("registry_rendering") != registry:
        changed["registry_rendering"] = registry
    if drafted_by_model:
        changed["_model_drafted"] = True
    return changed or None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write; otherwise report only")
    # Required, not defaulted. This writes, and a write script that guesses
    # which project it is pointed at is one shell away from rewriting the wrong
    # store's evidence.
    parser.add_argument("--project", required=True, help="GCP project holding the packets")
    parser.add_argument("--database", default="(default)")
    args = parser.parse_args()

    store = FirestoreStore(args.project, args.database)
    docs = store.all(COLLECTION)
    drafts = [d for d in docs if d.get("approval_state") == "DRAFT"]
    print(f"{len(docs)} packets, {len(drafts)} in DRAFT")

    rewritten = 0
    skipped_model = 0
    for doc in drafts:
        changed = redraft(doc)
        if changed is None:
            continue
        if changed.pop("_model_drafted", False):
            skipped_model += 1
            print(f"  {doc['packet_id']}: registry prose came from a drafter, left alone")
        if not changed:
            continue
        rewritten += 1
        print(f"  {doc['packet_id']}: {', '.join(sorted(changed))}")
        if args.apply:
            # Through `doc_id`, the same escaper the cycle writes with. The
            # `packet_id` FIELD is the logical id and may contain characters
            # Firestore refuses in a document id: NHDOT/VTAOT/MEDOT publishes
            # under a key with slashes in it. Writing the field verbatim would
            # either be refused, which is what happened, or land on a second
            # document beside the one it was meant to correct.
            store.put(COLLECTION, doc_id(doc["packet_id"]), {**doc, **changed})

    verb = "rewrote" if args.apply else "would rewrite"
    print(f"{verb} {rewritten} packet(s); {skipped_model} left for a human to look at")
    if not args.apply:
        print("Dry run. Pass --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
