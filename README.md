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

## Cursor agent behavior

For Cursor Cloud Agents, `.cursor/rules/hcm-kb-agent.mdc` is set to
`alwaysApply: true`, so agents should query the same SQLite knowledge base
behind the scenes and cite sources in responses.

## Notes

- Raw PDFs are not committed.
- The committed database gives fast startup without re-ingest.
