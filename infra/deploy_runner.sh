#!/usr/bin/env bash
# Put the live fleet runner on a VM and keep it there. Build plan M3.
#
#   infra/deploy_runner.sh interchange-wzdx-0807
#
# A VM rather than Cloud Run, and the reason is state rather than taste. The
# runner keeps two things on local disk that a fresh process cannot rebuild:
#
#   - `.fleet/bodies`, the last body each publisher served. Without it the poll
#     after every restart sends a conditional request the stored observations
#     justify, receives 304, and has nothing to answer it with, so that
#     publisher's zones leave the merged feed until its content next changes.
#   - the zone-write hashes, which are in memory by design. Losing them costs
#     one full ~50,000-document rewrite, which is survivable but not free.
#
# Cloud Run's filesystem is per-instance and its instances are replaced. A
# persistent disk makes both problems disappear without new code.
#
# Idempotent. Re-run it to push a code change: it stops the service, replaces
# the tree, and starts it again.

set -euo pipefail

PROJECT="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
ZONE="${ZONE:-us-central1-a}"
VM="${VM:-interchange-fleet}"
SA="fleet-runner"
INTERVAL="${INTERVAL:-900}"
REGION="${REGION:-us-central1}"
# Model Armor is regional and addressed by template, not URL. Overridable, but
# defaulted rather than left empty: an empty template with SCREENER=model-armor
# is a startup failure by design, and the point of naming it here is that the
# deployed fleet screens rather than redacting everything.
SCREENER="${SCREENER:-model-armor}"
MODEL_ARMOR_TEMPLATE="${MODEL_ARMOR_TEMPLATE:-interchange-ingest}"
# Where the merged feed is written once it has passed its own gate. Empty is a
# legitimate state and means the feed is validated and discarded, which the
# artifact reports as a null feed_uri rather than as a zero-byte publish.
OUTPUT_BUCKET="${OUTPUT_BUCKET:-${PROJECT}-feed}"
# The two model seats, both outside the trust gate. Authenticated through Vertex
# with the VM's own service account, so no API key exists to leak: the unit below
# sets GOOGLE_GENAI_USE_VERTEXAI and the SDK uses application default credentials.
ADJUDICATOR="${ADJUDICATOR:-gemini}"
DRAFTER="${DRAFTER:-gemini}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"

if [[ -z "$PROJECT" ]]; then
  echo "usage: $0 <project-id>" >&2
  exit 2
fi

say() { printf '\n== %s\n' "$1"; }

say "APIs"
gcloud services enable compute.googleapis.com --project="$PROJECT"

say "service account"
SA_EMAIL="$SA@$PROJECT.iam.gserviceaccount.com"
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA" \
    --project="$PROJECT" \
    --display-name="Interchange live fleet runner"
fi
# datastore.user, not datastore.owner. The runner writes documents; it has no
# business creating or deleting databases or indexes, and the deploy scripts
# that do run as a human.
#
# Retried, because a service account created a second ago is not yet visible to
# the IAM policy API: the binding fails with "does not exist" naming the account
# the previous command just printed the email of. Bounded, so a genuine
# permission failure still stops the deploy instead of spinning.
for attempt in 1 2 3 4 5 6; do
  if gcloud projects add-iam-policy-binding "$PROJECT" \
      --member="serviceAccount:$SA_EMAIL" \
      --role="roles/datastore.user" \
      --condition=None >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" == 6 ]]; then
    echo "could not grant roles/datastore.user to $SA_EMAIL" >&2
    exit 1
  fi
  echo "  waiting for $SA_EMAIL to propagate (attempt $attempt)"
  sleep 10
done

say "vm"
# e2-standard-2, measured rather than guessed. An e2-small was tried first and
# reached 1.4 GB resident of its 2 GB while still reconciling, with the whole
# write phase still ahead of it, so it was going to be killed rather than slow.
# The reconciler is also single-threaded and CPU-bound, and a shared-core type
# throttles to a fraction of a vCPU once its burst credits are gone, which turns
# a five-minute cycle into one that does not finish inside its own interval.
if ! gcloud compute instances describe "$VM" --zone="$ZONE" --project="$PROJECT" >/dev/null 2>&1; then
  gcloud compute instances create "$VM" \
    --project="$PROJECT" \
    --zone="$ZONE" \
    --machine-type="${MACHINE_TYPE:-e2-standard-2}" \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --boot-disk-size=20GB \
    --service-account="$SA_EMAIL" \
    --scopes=https://www.googleapis.com/auth/cloud-platform \
    --labels=component=fleet-runner
fi

say "code"
# The working tree, not a git clone. What runs has to be what is on this disk:
# cloning would deploy the last commit, and the point of the exercise is that
# history starts accruing from what has actually been tested here.
TAR=/tmp/interchange-runner.tar.gz
tar --exclude='.git' --exclude='.venv' --exclude='node_modules' --exclude='.fleet' \
    --exclude='console' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='product-critic-output' \
    -czf "$TAR" -C "$REPO" .
gcloud compute scp "$TAR" "$VM:/tmp/interchange.tar.gz" --zone="$ZONE" --project="$PROJECT"

say "install and start"
gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" --command="
set -euo pipefail
sudo systemctl stop interchange-fleet 2>/dev/null || true
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv >/dev/null
sudo mkdir -p /opt/interchange
sudo chown \$USER /opt/interchange
# The tree is replaced; .fleet is NOT, because it holds the retained bodies and
# a redeploy that discarded them would reintroduce the restart-time 304 gap this
# whole VM exists to avoid.
find /opt/interchange -mindepth 1 -maxdepth 1 ! -name .fleet -exec rm -rf {} +
tar -xzf /tmp/interchange.tar.gz -C /opt/interchange
if [ ! -d /opt/interchange/.venv ]; then
  python3 -m venv /opt/interchange/.venv
fi
# Only what the live path needs. Model Armor IS in this cycle: screening every
# free text field is the difference between a merged feed carrying real road
# names and one carrying the redaction placeholder on 98.8 percent of them.
# Gemini is in it too, on Vertex with the VM's own service account.
/opt/interchange/.venv/bin/pip install -q --upgrade pip
# pydantic and pydantic-settings are not optional: src/utils/env.py is the single
# env boundary and the runner imports it to resolve the screener's template.
# Missing them, the unit crash-loops on import before it polls anything.
#
# No backticks in this heredoc. It is unquoted so the local shell expands $PROJECT
# and friends, which means a backticked path in a comment is run as a command.
/opt/interchange/.venv/bin/pip install -q jsonschema google-cloud-firestore google-cloud-modelarmor \
  google-genai google-cloud-storage pydantic pydantic-settings

sudo tee /etc/systemd/system/interchange-fleet.service >/dev/null <<UNIT
[Unit]
Description=Interchange live fleet runner
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=\$USER
WorkingDirectory=/opt/interchange
# The screener reads its template from the env boundary. Named here rather than
# left to a default, because a missing template is a hard exit and not a quiet
# fall back to fail-closed: both redact, so a silent fallback would be
# indistinguishable from screening that found every road name hostile.
Environment=GCP_PROJECT_ID=$PROJECT
Environment=GCP_REGION=$REGION
Environment=MODEL_ARMOR_TEMPLATE_ID=$MODEL_ARMOR_TEMPLATE
Environment=GCS_BUCKET_OUTPUT=$OUTPUT_BUCKET
# Read by the google-genai SDK itself, not by this repository. Vertex means the
# VM's service account is the credential, so there is no key in this unit file
# for anyone with a shell on the box to read.
Environment=GOOGLE_GENAI_USE_VERTEXAI=true
Environment=GOOGLE_CLOUD_PROJECT=$PROJECT
Environment=GOOGLE_CLOUD_LOCATION=$REGION
ExecStart=/opt/interchange/.venv/bin/python scripts/run_live_cycle.py --store firestore --project $PROJECT --root /opt/interchange/.fleet --interval $INTERVAL --screener $SCREENER --adjudicator $ADJUDICATOR --drafter $DRAFTER --output-bucket $OUTPUT_BUCKET
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now interchange-fleet
sleep 5
systemctl is-active interchange-fleet
"

cat <<EOF

Running. Follow it with:
  gcloud compute ssh $VM --zone=$ZONE --project=$PROJECT --command='journalctl -u interchange-fleet -f'
Stop it with:
  gcloud compute ssh $VM --zone=$ZONE --project=$PROJECT --command='sudo systemctl stop interchange-fleet'
EOF
