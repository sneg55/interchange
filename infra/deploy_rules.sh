#!/usr/bin/env bash
# Publish firestore.rules, using gcloud credentials rather than the Firebase CLI.
#
#   infra/deploy_rules.sh interchange-wzdx-0807
#
# Same reason as deploy_indexes.sh: `firebase login` is a second browser flow on
# top of the gcloud one, and this needs no new credential. It drives the
# firebaserules API directly, which is what the Firebase CLI does.
#
# Two calls. A ruleset is immutable content; the release is the pointer that
# makes one of them live. Creating a ruleset changes nothing until the release
# moves, which is also why a failed release leaves the previous rules serving.

set -euo pipefail

PROJECT="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULES="$HERE/firestore.rules"
API="https://firebaserules.googleapis.com/v1/projects/$PROJECT"

if [[ -z "$PROJECT" ]]; then
  echo "usage: $0 <project-id>" >&2
  exit 2
fi
if [[ ! -r "$RULES" ]]; then
  echo "cannot read $RULES" >&2
  exit 2
fi

# `x-goog-user-project` on every call below. Without it the firebaserules API
# refuses user credentials outright ("requires a quota project, which is not set
# by default"), because a user token carries no project to bill the call to.
TOKEN="$(gcloud auth print-access-token)"

payload=$(python3 - "$RULES" <<'PY'
import json, sys

with open(sys.argv[1]) as handle:
    source = handle.read()
print(json.dumps({"source": {"files": [{"name": "firestore.rules", "content": source}]}}))
PY
)

ruleset=$(curl -sS -X POST "$API/rulesets" \
  -H "Authorization: Bearer $TOKEN" \
  -H "x-goog-user-project: $PROJECT" \
  -H "Content-Type: application/json" \
  -d "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')

echo "ruleset: $ruleset"

# PATCH the release if it exists, POST if it does not. A new database has no
# cloud.firestore release, and a project that has deployed before has one that
# cannot be created twice.
if curl -sSf -o /dev/null -H "Authorization: Bearer $TOKEN" \
     -H "x-goog-user-project: $PROJECT" \
     "$API/releases/cloud.firestore" 2>/dev/null; then
  method=PATCH
  url="$API/releases/cloud.firestore"
  body="{\"release\":{\"name\":\"projects/$PROJECT/releases/cloud.firestore\",\"rulesetName\":\"$ruleset\"}}"
else
  method=POST
  url="$API/releases"
  body="{\"name\":\"projects/$PROJECT/releases/cloud.firestore\",\"rulesetName\":\"$ruleset\"}"
fi

curl -sS -X "$method" "$url" \
  -H "Authorization: Bearer $TOKEN" \
  -H "x-goog-user-project: $PROJECT" \
  -H "Content-Type: application/json" \
  -d "$body" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("release:", d.get("name"), d.get("rulesetName", ""))'
