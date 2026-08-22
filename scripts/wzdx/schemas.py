"""Official USDOT schema resolution, covering every version the live fleet declares.

Version matters. Validating a v4.1 feed against the v4.0 schema produces a pile
of meaningless errors, so the schema set is always chosen from the feed's own
declared version.

Five versions appear in the live registry: 4.1, 4.2, bare "4", "CWZ 1.0" and
"3.1". Only the WZDx ones are published in usdot-jpo-ode/wzdx. CWZ is a separate
specification and is not resolvable here, which is a reported outcome rather
than a failure: a publisher must never be penalized for publishing a spec this
tool has not implemented.
"""

SCHEMA_ROOT = "https://raw.githubusercontent.com/usdot-jpo-ode/wzdx/main/schemas"

# version -> (root schema name, member schema names). The root document was
# renamed from WZDxFeed to WorkZoneFeed after 4.0, and Direction was added.
# 3.0 and 3.1 ship a single self-contained document with no members.
SCHEMA_SETS = {
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

# Every WZDx version $refs these by absolute URL, so a validator holding only
# the WZDx documents cannot resolve a road event's geometry: it raises
# Unresolvable instead of counting errors. Pinning them is what makes an offline
# conformance check possible, and what stops an online one from depending on a
# third party's uptime for a result attributed to the publisher.
GEOJSON_SCHEMAS = {
    "LineString": "https://geojson.org/schema/LineString.json",
    "MultiPoint": "https://geojson.org/schema/MultiPoint.json",
    "Point": "https://geojson.org/schema/Point.json",
}

# The registry writes v4.0 as bare "4" and v3.0 as bare "3".
ALIASES = {"4": "4.0", "3": "3.0"}

SCHEMA_UNKNOWN = "SCHEMA_UNKNOWN"

_CACHE = {}


def normalize(version):
    v = str(version).strip()
    return ALIASES.get(v, v)


def is_known(version):
    return normalize(version) in SCHEMA_SETS


def validate(doc, version, fetch_json):
    """Validate against the schema for `version`.

    Returns a list of errors, or the string SCHEMA_UNKNOWN when no schema set is
    published for the declared version. The caller must treat SCHEMA_UNKNOWN as
    "not checked", never as "passed" and never as a defect in the publisher.
    """
    from jsonschema import Draft7Validator, RefResolver

    version = normalize(version)
    members = SCHEMA_SETS.get(version)
    if members is None:
        return SCHEMA_UNKNOWN
    root, names = members
    if version not in _CACHE:
        _CACHE[version] = {
            f"{n}.json": fetch_json(f"{SCHEMA_ROOT}/{version}/{n}.json") for n in names
        }
    store = _CACHE[version]
    main = store[f"{root}.json"]
    resolver = RefResolver(base_uri="", referrer=main, store=store)
    return list(Draft7Validator(main, resolver=resolver).iter_errors(doc))
