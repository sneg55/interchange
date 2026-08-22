"""From pairs to groups. Section 6.6.

Tiers classify pairs; the merged feed needs groups, and the step between them is
where an implementer would otherwise have to invent a policy. Two rules bound
transitivity so components do not sprawl down a corridor, and both exist because
`CanonicalSourceMap` is one source zone to one canonical ID:

- **One source zone per publisher per component.** If two zones from the same
  publisher would land in one component, the publisher is asserting they are
  distinct and Interchange defers to the publisher.
- **A contradiction is not resolved by transitivity.** If A matches B and A
  matches C but B and C were rejected against each other, A is not duplicated. It
  stays with whichever neighbour is closer and the losing pair is recorded as an
  `AMBIGUOUS_GROUPING` conflict naming the edge that was dropped.

Edges are admitted greedily in ascending order of distance, then publisher key,
so the same edge is dropped regardless of the order adjudications completed in.
Components are recomputed from scratch each cycle; only the identity mapping
persists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .matching import CandidatePair

# A refused pairing, as (left index, right index, distance). Indices rather than
# a records.DroppedEdge on purpose: this layer works over positions in the
# flattened feature list and does not know road event IDs. Building the spec
# record here would mean stuffing an index into a field named `road_event_id`,
# and the same edge object is attached to two components, so whoever resolved it
# first would mutate the copy the other one still needs.
DroppedPair = tuple[int, int, "float | None"]


@dataclass(slots=True)
class Group:
    """One merge group: the source indices that became one canonical zone."""

    members: list[int] = field(default_factory=list)
    edges: list[CandidatePair] = field(default_factory=list)
    dropped: list[DroppedPair] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.members)


def _publisher_of(pair: CandidatePair, index: int) -> str:
    return pair.left_publisher if index == pair.left_index else pair.right_publisher


def build_groups(
    pairs: list[CandidatePair],
    accepted: set[tuple[int, int]] | None = None,
    all_indices: list[int] | None = None,
    rejected: set[tuple[int, int]] | None = None,
) -> list[Group]:
    """Connected components over accepted edges, with the caps applied.

    `accepted` names the pairs the adjudicator approved. Tier 1 edges are always
    accepted; a Tier 2 edge joins a component only when it appears here. A Tier 2
    pair absent from the set is a rejection, not an omission, which is why the
    caller passes the set rather than a filtered list of pairs: an empty set has
    to mean "nothing was approved", not "adjudication has not run".

    `rejected` names pairs an adjudicator explicitly refused, and they are
    EXPLICIT NON-EDGES rather than merely absent ones. Section 6.6: if A matches
    B and A matches C but B and C were rejected against each other, the
    contradiction is not silently resolved by transitivity. Filtering rejections
    out and relying on absence erases the difference between "never a candidate"
    and "adjudicated DISTINCT", and A, B and C end up in one component with no
    conflict recorded anywhere.
    """
    approved = accepted if accepted is not None else set()
    refused = rejected or set()
    admissible = [
        pair
        for pair in pairs
        if pair.tier == "TIER_1_DETERMINISTIC" or (pair.left_index, pair.right_index) in approved
    ]
    # Deterministic order is load-bearing, not tidiness. Two runs over identical
    # input must drop the same edge.
    admissible.sort(key=lambda p: p.sort_key)

    # Source indices each component must not be joined to, carried through
    # unions so a refusal recorded against one member constrains the whole
    # component.
    forbidden: dict[int, set[int]] = {}
    parent: dict[int, int] = {}
    publishers: dict[int, set[str]] = {}
    members: dict[int, list[int]] = {}
    kept_edges: dict[int, list[CandidatePair]] = {}
    dropped: dict[int, list[DroppedPair]] = {}

    def find(index: int) -> int:
        root = index
        while parent[root] != root:
            parent[root] = parent[parent[root]]
            root = parent[root]
        return root

    def ensure(index: int, publisher: str) -> None:
        if index in parent:
            return
        parent[index] = index
        publishers[index] = {publisher}
        members[index] = [index]
        kept_edges[index] = []
        dropped[index] = []
        forbidden[index] = {
            other for left, right in refused for other in ((right,) if left == index else ())
        } | {left for left, right in refused if right == index}

    for pair in admissible:
        left_publisher = pair.left_publisher
        right_publisher = pair.right_publisher
        ensure(pair.left_index, left_publisher)
        ensure(pair.right_index, right_publisher)
        a, b = find(pair.left_index), find(pair.right_index)
        if a == b:
            continue
        blocked_by_refusal = bool(
            (forbidden[a] & set(members[b])) or (forbidden[b] & set(members[a]))
        )
        if blocked_by_refusal or publishers[a] & publishers[b]:
            # Merging would put two zones from one publisher in one component.
            # The publisher is asserting they are distinct, so the edge is
            # dropped and the disagreement is preserved as a record rather than
            # as duplicated geometry.
            edge: DroppedPair = (pair.left_index, pair.right_index, pair.distance_m)
            dropped[a].append(edge)
            dropped[b].append(edge)
            continue
        # Union by size, so `members` concatenation stays near-linear overall.
        if len(members[a]) < len(members[b]):
            a, b = b, a
        parent[b] = a
        members[a].extend(members[b])
        publishers[a] |= publishers[b]
        kept_edges[a].extend([*kept_edges[b], pair])
        dropped[a].extend(dropped[b])
        forbidden[a] |= forbidden[b]
        members[b], kept_edges[b], dropped[b] = [], [], []
        forbidden[b] = set()

    groups = [
        Group(members=sorted(members[root]), edges=kept_edges[root], dropped=dropped[root])
        for root in {find(i) for i in parent}
    ]
    # Every source zone becomes a canonical zone, merged or not. A zone that
    # matched nothing is not an absence to be tidied away; it is one publisher's
    # work zone and it belongs in the merged feed.
    grouped = {i for group in groups for i in group.members}
    for index in all_indices or []:
        if index not in grouped:
            groups.append(Group(members=[index]))
    return sorted(groups, key=lambda g: g.members[0] if g.members else -1)


def dropped_pairs(group: Group) -> list[DroppedPair]:
    """The edges this group discarded, deduplicated and ordered.

    The same edge is recorded on both resulting canonical zones, so a console
    showing either one can name the pairing that was refused.
    """
    seen: set[tuple[int, int]] = set()
    unique = []
    for left, right, distance in group.dropped:
        if (left, right) in seen:
            continue
        seen.add((left, right))
        unique.append((left, right, distance))
    return sorted(unique)
