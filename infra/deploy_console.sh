#!/usr/bin/env bash
# Build the console and put it on Cloud Run. Spec 6.9, 19.
#
#   infra/deploy_console.sh interchange-wzdx-0807 you@example.com
#
# The second argument is the approver allowlist, comma separated. It is
# deliberately a required argument rather than an optional one with a default:
# an empty allowlist is legal and means nobody can approve anything, which is the
# safe direction but a silent one, and a deploy that quietly stalled the notice
# queue would look exactly like a working deploy until someone tried to approve.
#
# Reads the Firebase web config from the project rather than taking it as input,
# so the deployed console cannot end up pointed at a different project than the
# one it writes to. Those values are public by design (see console/src/lib/env.ts).
#
# They are RUNTIME environment, not build arguments, which is the opposite of the
# usual advice for NEXT_PUBLIC_ and is correct here: `env.ts` reads
# `process.env[name]` dynamically, so Next's build-time inlining cannot see it,
# and the client is handed the config as a prop rather than reading an
# environment of its own. Passing them at build time deployed a console that
# reported no Firebase config with every value set.

set -euo pipefail

PROJECT="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
APPROVERS="${2:-}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-interchange-console}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONSOLE="$(dirname "$HERE")/console"

if [[ -z "$PROJECT" || -z "$APPROVERS" ]]; then
  echo "usage: $0 <project-id> <approver-emails-comma-separated>" >&2
  exit 2
fi

TOKEN="$(gcloud auth print-access-token)"
api() {
  curl -sS -H "Authorization: Bearer $TOKEN" -H "x-goog-user-project: $PROJECT" "$@"
}

echo "== firebase web config"
APP=$(api "https://firebase.googleapis.com/v1beta1/projects/$PROJECT/webApps" \
  | python3 -c 'import json,sys; apps=json.load(sys.stdin).get("apps") or []; print(apps[0]["name"] if apps else "")')
if [[ -z "$APP" ]]; then
  echo "no Firebase web app in $PROJECT. Create one first:" >&2
  echo "  curl -X POST .../v1beta1/projects/$PROJECT/webApps -d '{\"displayName\":\"Interchange console\"}'" >&2
  exit 1
fi
read -r API_KEY AUTH_DOMAIN < <(api "https://firebase.googleapis.com/v1beta1/$APP/config" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["apiKey"], d["authDomain"])')
echo "   $AUTH_DOMAIN"

echo "== deploy"
gcloud run deploy "$SERVICE" \
  --project="$PROJECT" \
  --region="$REGION" \
  --source="$CONSOLE" \
  --allow-unauthenticated \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=$PROJECT,INTERCHANGE_APPROVERS=$APPROVERS,NEXT_PUBLIC_FIREBASE_API_KEY=$API_KEY,NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=$AUTH_DOMAIN" \
  --cpu=1 --memory=1Gi --min-instances=0 --max-instances=4

URL=$(gcloud run services describe "$SERVICE" --project="$PROJECT" --region="$REGION" \
  --format='value(status.url)')

cat <<EOF

$URL

--allow-unauthenticated is Cloud Run's ingress, NOT the console's access control.
Every page is behind Firebase sign-in and every read is behind the security
rules; the flag means the sign-in page itself is reachable without a Google
identity on the request, which it has to be.

Sign-in will fail until this host is an authorized domain:
  gcloud identity is not the tool for it, and the API call is:
  PATCH https://identitytoolkit.googleapis.com/v2/projects/$PROJECT/config?updateMask=authorizedDomains
EOF
