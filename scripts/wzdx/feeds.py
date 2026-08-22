"""Registry access and feed fetching for the WZDx probes.

The federal registry is the fleet's source of truth. Two of its properties
shape everything downstream and are easy to get wrong:

  - `issuingorganization` is NOT unique. The live registry lists Colorado DOT
    twice, so any fleet keyed on organization name silently collapses two
    publishers into one.
  - The declared cadence field is `datafeed_frequency_update` (a string such
    as "1m"), not `updatefrequency`, which is absent from every entry.
"""

import gzip
import json
import ssl
import urllib.request

REGISTRY = "https://datahub.transportation.gov/resource/69qe-yiui.json?$limit=500"
UA = {"User-Agent": "wzdx-dedup-probe/1.0", "Accept-Encoding": "gzip"}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def fetch_json(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
        body = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return json.loads(body)


def active_registry():
    return [r for r in fetch_json(REGISTRY) if r.get("active")]


def feed_key(entry):
    """Stable identity for a registry entry: (issuingorganization, feedname).

    Organization name alone is not unique, so the key pairs it with the feed
    name. The URL is deliberately excluded: it is a mutable attribute of the
    publisher, and folding it into the identity would make an endpoint change
    look like a decommission followed by a new provision, destroying the
    reliability history that is the whole point of a per-publisher agent.
    """
    return (entry["issuingorganization"], entry.get("feedname"))


def find_entries(registry, org):
    return [r for r in registry if r["issuingorganization"].lower() == org.lower()]


def load_feed(registry, org, timeout=60):
    """Resolve one organization to (entry, features). Raises on key-gated feeds."""
    matches = find_entries(registry, org)
    if not matches:
        near = [r["issuingorganization"] for r in registry
                if org.lower() in r["issuingorganization"].lower()]
        raise SystemExit(f"no active registry entry for {org!r}."
                         + (f" close matches: {near}" if near else ""))
    entry = matches[0]
    if entry.get("needapikey"):
        raise SystemExit(f"{org} requires an API key; not probed")
    doc = fetch_json(entry["url"]["url"], timeout=timeout)
    return entry, matches, doc.get("features", [])
