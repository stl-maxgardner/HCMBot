# HCMBot

## Persistent HCM knowledge base

This repo includes a prebuilt SQLite full-text index at:

```text
kb/hcm_kb.sqlite
```

## Rebuilding the KB (without committing PDFs)

You can regenerate `kb/hcm_kb.sqlite` from a local PDF directory that is outside
this repo (or in a git-ignored path).

Example:

```bash
python3 scripts/rebuild_kb.py \
  --pdf-dir /absolute/path/to/local/hcm_pdfs \
  --recursive \
  --reset
```

Notes:

- Source PDFs are **not** required to be in the repo.
- The script indexes PDF text and writes only to `kb/hcm_kb.sqlite`.
- Use `--force` to re-index unchanged files.
- Use `--db-path` if you want to build to an alternate sqlite output path.

It can be used by:

- Cursor agents (via `.cursor/rules/hcm-kb-agent.mdc`)
- A Slack bot (`tools/hcm_slackbot.py`) for chat-based HCM Q&A

## Slack bot (minimal GCP setup: Cloud Run + Secret Manager)

### 1) Install dependencies

```bash
python3 -m pip install -r requirements.txt
```

### 2) Create a Slack app

In Slack API dashboard:

1. Create app from scratch.
2. Under **OAuth & Permissions**, add bot token scopes:
   - `app_mentions:read`
   - `chat:write`
   - `channels:history` (recommended)
   - `im:history` (required for DM support)
   - `im:read` (recommended for DM behavior)
   - `groups:history` (optional for private channels)
3. Enable **Event Subscriptions**:
   - Turn on Event Subscriptions
   - Request URL: `https://YOUR_CLOUD_RUN_URL/slack/events`
   - Subscribe to bot event:
   - `app_mention`
   - `message.im` (required for direct messages)
4. Install app to workspace.
5. Invite bot to channel(s): `/invite @your-bot-name`

### 3) Create GCP secrets (one-time)

```bash
gcloud secrets create slack-bot-token --replication-policy=automatic
printf "%s" "xoxb-..." | gcloud secrets versions add slack-bot-token --data-file=-

gcloud secrets create slack-signing-secret --replication-policy=automatic
printf "%s" "your-signing-secret" | gcloud secrets versions add slack-signing-secret --data-file=-
```

### 4) Deploy to Cloud Run

Set project vars:

```bash
export PROJECT_ID="your-gcp-project-id"
export REGION="us-central1"
gcloud config set project "$PROJECT_ID"
```

Run deploy commands from the repository root (the directory containing
`cloudbuild.yaml`, `Dockerfile`, and `kb/`) so `--source .` uploads the correct
project:

```bash
cd /absolute/path/to/HCMBot
pwd
```

If you are not in repo root, use an absolute source path instead of `.`.

Deploy from source:

```bash
gcloud run deploy hcm-slackbot \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --service-account hcm-slackbot-sa@stl-datascience.iam.gserviceaccount.com \
  --set-env-vars GOOGLE_CLOUD_PROJECT="$PROJECT_ID",GOOGLE_CLOUD_LOCATION="$REGION",VERTEX_MODEL=gemini-2.0-flash-001,HCM_DB_PATH=kb/hcm_kb.sqlite \
  --set-secrets SLACK_BOT_TOKEN=slack-bot-token:latest,SLACK_SIGNING_SECRET=slack-signing-secret:latest
```

If deploy prints `Setting IAM policy failed`, your user likely lacks permission to
grant public invoker during deploy. In that case, run (or ask an admin to run):

```bash
gcloud beta run services add-iam-policy-binding hcm-slackbot \
  --region "$REGION" \
  --member=allUsers \
  --role=roles/run.invoker
```

Verify policy includes `allUsers`:

```bash
gcloud run services get-iam-policy hcm-slackbot \
  --region "$REGION" \
  --format=yaml
```

### 4b) Optional: one-command deploys with Cloud Build

This repo includes:

```text
cloudbuild.yaml
```

It builds the container, pushes it, and deploys Cloud Run with Secret Manager
bindings.

Create Artifact Registry repo once:

```bash
gcloud artifacts repositories create hcm-slackbot \
  --repository-format=docker \
  --location="$REGION"
```

Grant Cloud Build runtime permissions (once):

```bash
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
CLOUDBUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/run.admin"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/iam.serviceAccountUser"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding slack-bot-token \
  --member="serviceAccount:hcm-slackbot-sa@stl-datascience.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding slack-signing-secret \
  --member="serviceAccount:hcm-slackbot-sa@stl-datascience.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

Run deploy via Cloud Build:

```bash
gcloud builds submit \
  --config cloudbuild.yaml \
  --substitutions=_SERVICE_NAME=hcm-slackbot,_REGION="$REGION",_VERTEX_MODEL=gemini-2.0-flash-001,_RUNTIME_SERVICE_ACCOUNT=hcm-slackbot-sa@stl-datascience.iam.gserviceaccount.com
```

### 4c) Optional: bootstrap script for one-time setup

Use the helper script to enable APIs, create Artifact Registry, and set IAM roles:

```bash
chmod +x scripts/bootstrap_gcp.sh
scripts/bootstrap_gcp.sh --project "$PROJECT_ID" --region "$REGION"
```

If you want the script to also set secret values:

```bash
scripts/bootstrap_gcp.sh \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --slack-bot-token "xoxb-..." \
  --slack-signing-secret "your-signing-secret"
```

Optional: create a GitHub trigger in Cloud Build UI pointing to `main` so deploys
happen automatically on each push.

Your service will expose:

```text
POST /slack/events
```

### 5) Configure Slack Request URL

```bash
https://YOUR_CLOUD_RUN_URL/slack/events
```

After setting that in Slack Event Subscriptions, mention the bot in Slack:

`@hcmbot What are major LOS differences between HCM 2000 and 2010?`

You can also DM the bot directly with plain questions (no mention prefix needed),
once `message.im` is subscribed and `im:*` scopes are granted.

The bot retrieves evidence from `kb/hcm_kb.sqlite`, then synthesizes an answer
with citations (filename + page).

### Secret Manager notes

Secret Manager stores sensitive values (like Slack tokens) securely so they are
not committed to git or hardcoded in the app. Cloud Run injects them as
environment variables at runtime using `--set-secrets`.

## Optional fallback: no-deploy mode via GitHub Actions

Coworkers can already use the bot directly in Slack once the Cloud Run deployment
is live and the bot is invited to channels. The workflow below is an alternative
for cases where you do not want to run a persistent Cloud Run service.

```text
.github/workflows/hcm-slackbot-poller.yml
```

It runs every 5 minutes and posts threaded answers in configured Slack channels.

### One-time maintainer setup

1. In GitHub repo **Settings → Secrets and variables → Actions**:
   - Add secret `SLACK_BOT_TOKEN` (xoxb token)
   - Add secret `OPENAI_API_KEY`
2. Add repository variable `HCM_CHANNEL_IDS` with comma-separated Slack channel IDs
   (e.g., `C0123456789,C0987654321`).
3. Optional: add repo variable `OPENAI_MODEL` (default `gpt-4.1-mini`).
4. Ensure the Slack bot is invited to those channels.
5. Merge this PR; workflow starts automatically on schedule.

### How coworkers use it

In configured channels, coworkers can ask by:

- Mentioning the bot: `@hcmbot what changed from HCM 2000 to 2010 for freeway LOS?`
- Or prefixing a message: `hcm: explain multilane highway capacity assumptions`

No deployment or local runtime is needed for coworkers, but replies are near-real-time
rather than instant because this fallback mode runs on a schedule.

## Cursor agent behavior

For Cursor Cloud Agents, `.cursor/rules/hcm-kb-agent.mdc` is set to
`alwaysApply: true`, so agents should query the same SQLite knowledge base
behind the scenes and cite sources in responses.

## Notes

- Raw PDFs are not committed.
- The committed database gives fast startup without re-ingest.
