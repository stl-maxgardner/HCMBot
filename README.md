# HCMBot

## Persistent chat-first HCM knowledge base

This repo includes a prebuilt SQLite full-text index at:

```text
kb/hcm_kb.sqlite
```

The goal is simple: you ask HCM questions in chat, and any Cursor agent can
quickly search this database behind the scenes and answer with citations.

## How this works for you

- Ask questions conversationally in chat.
- The rule in `.cursor/rules/hcm-kb-agent.mdc` instructs agents to use
  `kb/hcm_kb.sqlite` internally for retrieval.
- Agents should return answers with source references (file + page).

No user-facing CLI is required for normal use.

## Notes

- Raw PDFs are not committed.
- The committed database gives fast startup for new agents without re-ingest.
