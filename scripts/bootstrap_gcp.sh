#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/bootstrap_gcp.sh --project PROJECT_ID --region REGION [options]

Required:
  --project PROJECT_ID          GCP project ID

Optional:
  --region REGION               GCP region (default: us-central1)
  --service-name NAME           Cloud Run service name (default: hcm-slackbot)
  --repo-name NAME              Artifact Registry repo name (default: hcm-slackbot)
  --slack-bot-token TOKEN       Create/update Secret Manager slack-bot-token
  --slack-signing-secret VALUE  Create/update Secret Manager slack-signing-secret
  --skip-secrets                Do not create/update secrets
  -h, --help                    Show this help

Examples:
  scripts/bootstrap_gcp.sh --project my-proj --region us-central1
  scripts/bootstrap_gcp.sh --project my-proj --slack-bot-token xoxb-... --slack-signing-secret ...
EOF
}

PROJECT_ID=""
REGION="us-central1"
SERVICE_NAME="hcm-slackbot"
REPO_NAME="hcm-slackbot"
SKIP_SECRETS="false"
SLACK_BOT_TOKEN=""
SLACK_SIGNING_SECRET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      PROJECT_ID="${2:-}"
      shift 2
      ;;
    --region)
      REGION="${2:-}"
      shift 2
      ;;
    --service-name)
      SERVICE_NAME="${2:-}"
      shift 2
      ;;
    --repo-name)
      REPO_NAME="${2:-}"
      shift 2
      ;;
    --slack-bot-token)
      SLACK_BOT_TOKEN="${2:-}"
      shift 2
      ;;
    --slack-signing-secret)
      SLACK_SIGNING_SECRET="${2:-}"
      shift 2
      ;;
    --skip-secrets)
      SKIP_SECRETS="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$PROJECT_ID" ]]; then
  echo "--project is required." >&2
  usage
  exit 1
fi

echo "==> Configuring gcloud project: $PROJECT_ID"
gcloud config set project "$PROJECT_ID" >/dev/null

echo "==> Enabling required APIs"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com >/dev/null

echo "==> Ensuring Artifact Registry repository exists: $REPO_NAME ($REGION)"
if ! gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPO_NAME" \
    --repository-format=docker \
    --location="$REGION"
else
  echo "Artifact Registry repo already exists."
fi

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
CLOUDBUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

echo "==> Granting Cloud Build IAM roles"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/run.admin" >/dev/null

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/iam.serviceAccountUser" >/dev/null

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/secretmanager.secretAccessor" >/dev/null

if [[ "$SKIP_SECRETS" == "false" ]]; then
  echo "==> Ensuring required secrets exist"
  if ! gcloud secrets describe slack-bot-token >/dev/null 2>&1; then
    gcloud secrets create slack-bot-token --replication-policy=automatic >/dev/null
  fi
  if ! gcloud secrets describe slack-signing-secret >/dev/null 2>&1; then
    gcloud secrets create slack-signing-secret --replication-policy=automatic >/dev/null
  fi

  if [[ -n "$SLACK_BOT_TOKEN" ]]; then
    printf "%s" "$SLACK_BOT_TOKEN" | gcloud secrets versions add slack-bot-token --data-file=- >/dev/null
    echo "Updated secret: slack-bot-token"
  fi
  if [[ -n "$SLACK_SIGNING_SECRET" ]]; then
    printf "%s" "$SLACK_SIGNING_SECRET" | gcloud secrets versions add slack-signing-secret --data-file=- >/dev/null
    echo "Updated secret: slack-signing-secret"
  fi
fi

cat <<EOF

Bootstrap complete.

Next deploy command:
  gcloud builds submit \\
    --config cloudbuild.yaml \\
    --substitutions=_SERVICE_NAME=${SERVICE_NAME},_REGION=${REGION},_VERTEX_MODEL=gemini-2.0-flash-001

After first deploy, set Slack Event Request URL to:
  https://<CLOUD_RUN_URL>/slack/events

EOF
