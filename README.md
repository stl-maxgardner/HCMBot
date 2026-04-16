# HCMBot

## Persistent HCM knowledge base

This repo includes a prebuilt SQLite full-text index at:

```text
kb/hcm_kb.sqlite
```

It can be used by:

- Cursor agents (via `.cursor/rules/hcm-kb-agent.mdc`)
- A Slack bot (`tools/hcm_slackbot.py`) for chat-based HCM Q&A

## Slack bot setup

### 1) Install dependencies

```bash
python3 -m pip install -r requirements.txt
```

### 2) Create a Slack app

In Slack API dashboard:

1. Create app from scratch.
2. Enable **Socket Mode** and create an app-level token with
   `connections:write` scope.
3. Enable **Event Subscriptions** and subscribe to bot event:
   - `app_mention`
4. Under **OAuth & Permissions**, add bot token scopes:
   - `app_mentions:read`
   - `channels:history`
   - `chat:write`
   - `groups:history` (optional for private channels)
5. Install app to workspace.

### 3) Configure environment

```bash
export SLACK_BOT_TOKEN="xoxb-..."
export SLACK_APP_TOKEN="xapp-..."
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL="gpt-4.1-mini"  # optional
export HCM_DB_PATH="kb/hcm_kb.sqlite"  # optional
```

### 4) Run bot

```bash
python3 tools/hcm_slackbot.py
```

### 5) Ask questions in Slack

- Mention the bot in a channel:
  - `@hcmbot What are major LOS differences between HCM 2000 and 2010?`
- Or use prefixed message:
  - `hcm: how is capacity estimated for basic freeway segments?`

The bot retrieves evidence from `kb/hcm_kb.sqlite`, then synthesizes an answer
with citations (filename + page).

## No-deploy mode for coworkers (GitHub Actions)

If you want coworkers to use the bot without running anything locally, use the
included workflow:

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

No deployment or local runtime is needed for coworkers.

## Cursor agent behavior

For Cursor Cloud Agents, `.cursor/rules/hcm-kb-agent.mdc` is set to
`alwaysApply: true`, so agents should query the same SQLite knowledge base
behind the scenes and cite sources in responses.

## Notes

- Raw PDFs are not committed.
- The committed database gives fast startup without re-ingest.
