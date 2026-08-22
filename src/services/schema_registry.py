"""Resolving and applying the official USDOT schema for a feed's own version.

Version matching is the whole point. Validating a v4.1 feed against the v4.0
schema produced 81 spurious errors during research, all of which disappeared once
the schema matched the feed, so a validator that hardcodes one version does not
measure conformance, it measures version skew.

Five versions appear in the live registry: 4.1, 4.2, bare "4", "CWZ 1.0" and
"3.1". Only the WZDx ones are published in usdot-jpo-ode/wzdx. CWZ is a separate
specification. An unresolvable version yields SCHEMA_UNKNOWN, which suppresses R3
rather than failing the publisher: a publisher must never be penalised for
publishing a specification Interchange has not implemented.
"""

from __future__ import annotations

from typing import Any, Protocol

# version -> (root schema name, member schema names). The root document was
# renamed from WZDxFeed to WorkZoneFeed after 4.0, and Direction was added.
# 3.0 and 3.1 ship a single self-contained document with no members.
SCHEMA_SETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "3.0": ("WZDxFeed", ("WZDxFeed",)),
    "3.1": ("WZDxFeed", ("WZDxFeed",)),
    "4.0": ("WZDxFeed", ("BoundingBox", "FeedInfo", "RoadEventFeature", "WZDxFeed")),
    "4.1": (
        "WorkZoneFeed",
        ("BoundingBox", "Direction", "FeedInfo", "RoadEventFeature", "WorkZoneFeed"),
    ),
    "4.2": (
        "WorkZoneFeed",
        ("BoundingBox", "Direction", "FeedInfo", "RoadEventFeature", "WorkZoneFeed"),
    ),
}

SCHEMA_ROOT = "https://raw.githubusercontent.com/usdot-jpo-ode/wzdx/main/schemas"

# Every WZDx version $refs these by absolute URL. Without them the validator
# raises Unresolvable on the first road event rather than returning an error
# count, so a missing GeoJSON document is not a smaller check, it is no check.
GEOJSON_REFS = {
    "https://geojson.org/schema/LineString.json": "LineString",
    "https://geojson.org/schema/MultiPoint.json": "MultiPoint",
    "https://geojson.org/schema/Point.json": "Point",
}
GEOJSON_VERSION = "geojson"

# The registry writes v4.0 as bare "4" and v3.0 as bare "3".
ALIASES = {"4": "4.0", "3": "3.0"}

SCHEMA_UNKNOWN = "SCHEMA_UNKNOWN"


class SchemaLoader(Protocol):
    """Supplies one member schema document. Fixtures locally, GCS or GitHub live."""

    def schema(self, version: str, member: str) -> dict[str, Any]: ...


def normalize(version: str | None) -> str:
    return ALIASES.get(str(version or "").strip(), str(version or "").strip())


def is_known(version: str | None) -> bool:
    return normalize(version) in SCHEMA_SETS


class SchemaRegistry:
    """Caches compiled validators per version.

    Compiling is the expensive part and the fleet validates the same five
    versions thousands of times a day, so the cache is what makes per-poll
    conformance checking affordable at the five minute floor.
    """

    def __init__(self, loader: SchemaLoader) -> None:
        self._loader = loader
        self._validators: dict[str, Any] = {}

    def resolve(self, declared_version: str | None) -> str:
        """The version actually used, or SCHEMA_UNKNOWN.

        Recorded on the observation as `schema_version_used` so a reader can tell
        which schema produced an error count, and can tell "not checked" from
        "checked and clean".
        """
        version = normalize(declared_version)
        return version if version in SCHEMA_SETS else SCHEMA_UNKNOWN

    def _validator(self, version: str) -> Any:
        if version in self._validators:
            return self._validators[version]
        from jsonschema import Draft7Validator
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT7

        root, members = SCHEMA_SETS[version]
        registry = Registry()
        for name in members:
            doc = self._loader.schema(version, name)
            resource = Resource.from_contents(doc, default_specification=DRAFT7)
            # Registered under both the bare filename and the absolute URL the
            # WZDx documents actually $ref. Only the absolute form is used at
            # resolution time; the bare one is kept because 3.x documents
            # self-reference by filename.
            registry = registry.with_resource(f"{name}.json", resource)
            registry = registry.with_resource(f"{SCHEMA_ROOT}/{version}/{name}.json", resource)
        for url, name in GEOJSON_REFS.items():
            geo = Resource.from_contents(
                self._loader.schema(GEOJSON_VERSION, name), default_specification=DRAFT7
            )
            registry = registry.with_resource(url, geo)
        main = self._loader.schema(version, root)
        validator = Draft7Validator(main, registry=registry)
        self._validators[version] = validator
        return validator

    def error_count(
        self, doc: dict[str, Any], declared_version: str | None
    ) -> tuple[str, int | None]:
        """Return (schema_version_used, error_count).

        The count is None when the version is unknown. None is not zero: zero
        means the feed was validated and passed, None means it was never checked,
        and section 6.4 requires R3 to be NOT_APPLICABLE in the second case
        rather than ADMIT.
        """
        version = self.resolve(declared_version)
        if version == SCHEMA_UNKNOWN:
            return SCHEMA_UNKNOWN, None
        return version, sum(1 for _ in self._validator(version).iter_errors(doc))

    def errors(self, doc: dict[str, Any], declared_version: str | None) -> list[Any] | str:
        """The full error list, for the evidence packet rather than the scorer."""
        version = self.resolve(declared_version)
        if version == SCHEMA_UNKNOWN:
            return SCHEMA_UNKNOWN
        return list(self._validator(version).iter_errors(doc))


class FixtureSchemaLoader:
    """SchemaLoader backed by the checksummed snapshot in tests/fixtures/."""

    def __init__(self, fixtures: Any = None) -> None:
        if fixtures is None:
            from .fixtures import FixtureSet

            fixtures = FixtureSet()
        self._fixtures = fixtures

    def schema(self, version: str, member: str) -> dict[str, Any]:
        return self._fixtures.schema(version, member)
