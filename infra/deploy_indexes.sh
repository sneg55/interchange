#!/usr/bin/env bash
# Create every composite index in firestore.indexes.json, using gcloud.
#
#   infra/deploy_indexes.sh interchange-wzdx-0807
#
# The documented path is `firebase deploy --only firestore:indexes`, and it is
# still the right one when the Firebase CLI is authenticated. This exists because
# it often is not: `firebase login` is a browser flow, `gcloud auth login` is
# already done for everything else, and an index that has not been created fails
# the query at RUNTIME rather than at deploy time. A fleet that cannot reload its
# own observation history is not a slow fleet, it is a stopped one.
#
# Reads the same JSON the Firebase CLI reads, so the two cannot describe
# different indexes. Existing indexes are reported and skipped, not treated as
# failures, so this is safe to re-run.

set -euo pipefail

PROJECT="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
DATABASE="${2:-(default)}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INDEXES="$HERE/firestore.indexes.json"

if [[ -z "$PROJECT" ]]; then
  echo "usage: $0 <project-id> [database]" >&2
  exit 2
fi
if [[ ! -r "$INDEXES" ]]; then
  echo "cannot read $INDEXES" >&2
  exit 2
fi

# One line per index: collection-group, then the --field-config flags.
python3 - "$INDEXES" <<'PY' > /tmp/interchange-indexes.txt
import json, sys

with open(sys.argv[1]) as handle:
    definition = json.load(handle)

for index in definition.get("indexes", []):
    flags = []
    for field in index.get("fields", []):
        path = field["fieldPath"]
        if "arrayConfig" in field:
            flags.append(f"--field-config=field-path={path},array-config=contains")
        else:
            flags.append(f"--field-config=field-path={path},order={field['order'].lower()}")
    print(index["collectionGroup"], " ".join(flags))
PY

created=0
existing=0
while read -r group flags; do
  [[ -z "$group" ]] && continue
  # shellcheck disable=SC2086
  # --async, and it is the difference between minutes and an hour. Without it
  # gcloud waits for each index to finish building before submitting the next,
  # measured at roughly six minutes apiece against an EMPTY database, serially,
  # for twelve indexes. Firestore builds them concurrently when asked to.
  if output=$(gcloud firestore indexes composite create \
        --project="$PROJECT" \
        --database="$DATABASE" \
        --collection-group="$group" \
        --query-scope=COLLECTION \
        --async \
        $flags 2>&1); then
    created=$((created + 1))
    echo "created: $group"
  elif grep -qi "already exists" <<<"$output"; then
    existing=$((existing + 1))
    echo "exists:  $group"
  else
    echo "FAILED:  $group" >&2
    echo "$output" >&2
    exit 1
  fi
done < /tmp/interchange-indexes.txt

echo
echo "$created created, $existing already present, in $PROJECT ($DATABASE)"
echo "Indexes build in the background. Check with:"
echo "  gcloud firestore indexes composite list --project=$PROJECT --database='$DATABASE'"
