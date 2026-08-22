#!/usr/bin/env python3
"""Deploy publisher agents to Vertex AI Agent Engine, and measure what they cost.

This is the M1 spike for section 19.9 question 1. The reference codelab deploys
one agent; this project's design assumes 40, and nobody has tested whether that
extrapolates. Deploy a few, poll them, and find out.

    python3 scripts/deploy_agents.py --deploy 3
    python3 scripts/deploy_agents.py --list
    python3 scripts/deploy_agents.py --poll
    python3 scripts/deploy_agents.py --delete-all

Deploying is billable and slow (minutes per agent). --delete-all exists because
leaving reasoning engines running while nobody watches is how a spike turns into
a bill.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT = os.environ.get("GCP_PROJECT_ID", "interchange-wzdx-0807")
LOCATION = os.environ.get("GCP_REGION", "us-central1")
BUCKET = f"gs://{PROJECT}-agents"

# Three publishers chosen so the spike exercises different shapes: a frozen feed
# whose R4 signal is the demo headline, a large feed, and a one-feature feed.
SPIKE_PUBLISHERS = [
    ("Utah DOT", "udot"),
    ("Hawaii DOT", "hidot"),
    ("St. Charles County", "stcharlesco_v4"),
]


def _init():
    """Build the Agent Engine client.

    vertexai.Client is deprecated in favour of agentplatform.Client as of SDK
    1.163, part of the Gemini Enterprise Agent Platform rename. Prefer the new
    one and fall back, so this keeps working on either SDK version.
    """
    try:
        from google.cloud import agentplatform

        return agentplatform.Client(project=PROJECT, location=LOCATION)
    except ImportError:
        import vertexai

        return vertexai.Client(project=PROJECT, location=LOCATION)


def _registry_entry(org: str, feedname: str) -> dict:
    from src.services.fixtures import FixtureSet

    for r in FixtureSet().registry():
        if r["issuingorganization"] == org and r.get("feedname") == feedname:
            return r
    raise SystemExit(f"no registry entry for {org}/{feedname} in the snapshot")


def deploy(count: int) -> int:
    import cloudpickle
    from src.features import publisher_agent
    from src.features.publisher_agent.agent import PublisherAgent

    # cloudpickle serialises a class defined in an importable module BY
    # REFERENCE, so the deployed container tries to `import src` and dies with
    # "No module named 'src'" AFTER a four-minute build. Registering the module
    # for by-value pickling embeds the class definition in the payload instead.
    # The alternative is shipping the source via extra_packages, which is worse
    # here: the agent is one stdlib-only file and dragging the whole src/ tree
    # into every one of 40 agents would couple each publisher's runtime to code
    # it never calls.
    cloudpickle.register_pickle_by_value(publisher_agent.agent)
    cloudpickle.register_pickle_by_value(publisher_agent.signals)

    client = _init()
    chosen = SPIKE_PUBLISHERS[:count]
    print(f"deploying {len(chosen)} agents to {PROJECT}/{LOCATION}\n")
    results = []
    for org, feedname in chosen:
        entry = _registry_entry(org, feedname)
        key = f"{org}|{feedname}"
        agent = PublisherAgent(
            publisher_key=key,
            url=entry["url"]["url"],
            declared_version=str(entry.get("version")),
            declared_cadence=entry.get("datafeed_frequency_update") or "",
        )
        label = f"interchange-{feedname}".replace("_", "-").lower()[:63]
        print(f"  {key} -> {label}")
        started = time.time()
        try:
            remote = client.agent_engines.create(
                agent=agent,
                config={
                    "display_name": label,
                    "description": f"Interchange publisher agent for {org}",
                    "staging_bucket": BUCKET,
                    # The agent itself is stdlib only: no LLM, no dependencies.
                    # google-cloud-aiplatform is here for the RUNTIME, not the
                    # agent. This list REPLACES the container's defaults rather
                    # than adding to them, and the serving harness imports
                    # google.cloud.aiplatform in its own telemetry path
                    # (app/api/telemetry_utils.py). Declaring only what the agent
                    # needs makes the harness fail to boot, ten minutes after the
                    # deploy call, with a ModuleNotFoundError naming a package the
                    # agent never imports.
                    "requirements": [
                        "google-cloud-aiplatform[agent_engines]",
                        "cloudpickle",
                        "pydantic",
                    ],
                },
            )
            took = time.time() - started
            name = getattr(remote, "api_resource", None)
            name = getattr(name, "name", None) or getattr(remote, "resource_name", "?")
            print(f"    deployed in {took / 60:.1f} min -> {name}")
            results.append({"publisher_key": key, "resource": name, "deploy_s": took})
        except Exception as exc:
            print(f"    FAILED after {(time.time() - started) / 60:.1f} min")
            print(f"    {type(exc).__name__}: {str(exc)[:400]}")
            results.append({"publisher_key": key, "error": f"{type(exc).__name__}: {exc}"})

    ok = [r for r in results if "resource" in r]
    print(f"\n{len(ok)}/{len(chosen)} deployed")
    if ok:
        mean = sum(r["deploy_s"] for r in ok) / len(ok)
        print(f"  mean deploy time: {mean / 60:.1f} min")
        print(f"  extrapolated to 40 agents: {mean * 40 / 3600:.1f} h sequential")
    Path("deploy_results.json").write_text(json.dumps(results, indent=2))
    return 0 if ok else 1


def list_agents() -> int:
    client = _init()
    found = list(client.agent_engines.list())
    print(f"{len(found)} agent engines in {PROJECT}/{LOCATION}")
    for a in found:
        res = getattr(getattr(a, "api_resource", None), "name", "?")
        disp = getattr(getattr(a, "api_resource", None), "display_name", "?")
        print(f"  {disp:32} {res}")
    return 0


def _invoke(name: str, method: str = "query", payload: dict | None = None) -> dict:
    """Call a deployed agent's class method over the REST `:query` endpoint.

    The SDK's AgentEngine object does NOT bind the deployed class's methods:
    `a.query()` raises AttributeError, and `client.agent_engines` has no
    `invoke`. The resource's own `:query` endpoint is the working path, and it
    is also what a production caller would use.
    """
    import google.auth
    import google.auth.transport.requests
    import requests

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(google.auth.transport.requests.Request())
    response = requests.post(
        f"https://{LOCATION}-aiplatform.googleapis.com/v1/{name}:query",
        headers={"Authorization": f"Bearer {credentials.token}"},
        json={"class_method": method, "input": payload or {}},
        timeout=120,
    )
    response.raise_for_status()
    return response.json().get("output", {})


def poll() -> int:
    """Query every deployed agent once and show the observation it returns."""
    client = _init()
    found = list(client.agent_engines.list())
    if not found:
        print("no agents deployed")
        return 1
    for a in found:
        disp = getattr(getattr(a, "api_resource", None), "display_name", "?")
        started = time.time()
        try:
            obs = _invoke(a.api_resource.name)
            took = (time.time() - started) * 1000
            if hasattr(obs, "get"):
                print(f"\n  {disp} ({took:.0f} ms round trip)")
                for k in (
                    "http_status",
                    "feature_count",
                    "active_count",
                    "active_with_past_end_date",
                    "update_date",
                    "error",
                ):
                    if obs.get(k) is not None:
                        print(f"    {k}: {obs[k]}")
                if obs.get("content_hash"):
                    print(f"    content_hash: {obs['content_hash'][:16]}")
            else:
                print(f"\n  {disp}: {str(obs)[:300]}")
        except Exception as exc:
            print(f"\n  {disp}: QUERY FAILED {type(exc).__name__}: {str(exc)[:300]}")
    return 0


def delete_all() -> int:
    client = _init()
    found = list(client.agent_engines.list())
    print(f"deleting {len(found)} agent engines")
    for a in found:
        disp = getattr(getattr(a, "api_resource", None), "display_name", "?")
        try:
            a.delete(force=True)
            print(f"  deleted {disp}")
        except Exception as exc:
            print(f"  FAILED {disp}: {type(exc).__name__}: {str(exc)[:200]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy", type=int, metavar="N", help="deploy N spike agents")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--poll", action="store_true")
    ap.add_argument("--delete-all", action="store_true")
    args = ap.parse_args()
    if args.deploy:
        return deploy(args.deploy)
    if args.list:
        return list_agents()
    if args.poll:
        return poll()
    if args.delete_all:
        return delete_all()
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
