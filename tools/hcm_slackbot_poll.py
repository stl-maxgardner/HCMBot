#!/usr/bin/env python3
"""Poll Slack channels and answer HCM questions from SQLite KB."""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from openai import OpenAI
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

DB_PATH = Path(os.getenv("HCM_DB_PATH", "kb/hcm_kb.sqlite"))
TOP_K = int(os.getenv("HCM_TOP_K", "8"))
MAX_CHARS_PER_SNIPPET = int(os.getenv("HCM_MAX_CHARS_PER_SNIPPET", "650"))
POLL_WINDOW_MINUTES = int(os.getenv("HCM_POLL_WINDOW_MINUTES", "30"))
CHANNEL_IDS = [
    value.strip()
    for value in os.getenv("HCM_CHANNEL_IDS", "").split(",")
    if value.strip()
]

QUESTION_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}


def ensure_env() -> None:
    required = ["SLACK_BOT_TOKEN", "OPENAI_API_KEY"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
    if not CHANNEL_IDS:
        raise RuntimeError("Set HCM_CHANNEL_IDS to one or more channel IDs.")
    if not DB_PATH.exists():
        raise RuntimeError(f"HCM DB not found: {DB_PATH}")


def question_to_fts_query(question: str, max_terms: int = 12) -> str:
    tokens = re.findall(r"[a-z0-9]{3,}", question.lower())
    filtered = [t for t in tokens if t not in QUESTION_STOPWORDS]
    unique_terms: list[str] = []
    seen: set[str] = set()
    for token in filtered:
        if token in seen:
            continue
        unique_terms.append(token)
        seen.add(token)
        if len(unique_terms) >= max_terms:
            break
    if unique_terms:
        return " OR ".join(unique_terms)
    return question.strip()


def search_hcm(question: str, top_k: int = TOP_K) -> list[dict[str, Any]]:
    fts_query = question_to_fts_query(question)
    if not fts_query:
        return []

    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT
                path,
                page_number,
                bm25(page_index) AS score,
                snippet(page_index, 2, '[', ']', ' ... ', 36) AS preview,
                substr(content, 1, ?) AS content
            FROM page_index
            WHERE page_index MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (MAX_CHARS_PER_SNIPPET, fts_query, top_k),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "doc_name": Path(path).name,
            "page": page,
            "score": score,
            "preview": preview,
            "content": content,
        }
        for path, page, score, preview, content in rows
    ]


def build_prompt(question: str, evidence: list[dict[str, Any]]) -> str:
    parts = []
    for idx, item in enumerate(evidence, start=1):
        parts.append(
            (
                f"[{idx}] {item['doc_name']} p.{item['page']}\n"
                f"Preview: {item['preview']}\n"
                f"Text: {item['content']}\n"
            )
        )
    evidence_blob = "\n".join(parts)
    return (
        "You are an assistant answering Highway Capacity Manual questions.\n"
        "Use only the provided evidence passages.\n"
        "If evidence is insufficient or conflicting, state uncertainty clearly.\n"
        "Return:\n"
        "1) A concise answer (2-6 bullets max)\n"
        "2) A 'Citations' section listing source filename and page.\n\n"
        f"Question: {question}\n\n"
        f"Evidence:\n{evidence_blob}"
    )


def answer_with_openai(question: str, evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return (
            "I couldn't find strong evidence in the HCM index for that question. "
            "Try rephrasing or narrowing the topic."
        )

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    prompt = build_prompt(question, evidence)
    response = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        input=prompt,
        max_output_tokens=700,
    )
    text = getattr(response, "output_text", "").strip()
    if text:
        return text

    chunks = []
    for item in getattr(response, "output", []):
        for content in getattr(item, "content", []):
            if getattr(content, "type", "") == "output_text":
                chunks.append(getattr(content, "text", ""))
    joined = "\n".join(c for c in chunks if c).strip()
    return joined or "I couldn't generate an answer right now."


def parse_question(text: str, bot_user_id: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    mention_prefix = rf"^<@{re.escape(bot_user_id)}>\s*"
    if re.match(mention_prefix, text):
        return re.sub(mention_prefix, "", text).strip()
    if re.match(r"^hcm:\s+", text, flags=re.IGNORECASE):
        return re.sub(r"^hcm:\s+", "", text, flags=re.IGNORECASE).strip()
    return ""


def bot_already_replied(client: WebClient, channel_id: str, thread_ts: str, bot_user_id: str) -> bool:
    reply_resp = client.conversations_replies(
        channel=channel_id,
        ts=thread_ts,
        limit=50,
        inclusive=True,
    )
    for msg in reply_resp.get("messages", []):
        if msg.get("user") == bot_user_id and msg.get("ts") != thread_ts:
            return True
    return False


def process_channel(client: WebClient, channel_id: str, bot_user_id: str) -> int:
    posted = 0
    oldest = str(time.time() - (POLL_WINDOW_MINUTES * 60))
    history = client.conversations_history(channel=channel_id, limit=60, oldest=oldest)
    messages = history.get("messages", [])

    for message in reversed(messages):
        if message.get("subtype") or message.get("bot_id"):
            continue
        question = parse_question(message.get("text", ""), bot_user_id)
        if not question:
            continue
        thread_ts = message.get("thread_ts", message.get("ts"))
        if not thread_ts:
            continue
        if bot_already_replied(client, channel_id, thread_ts, bot_user_id):
            continue

        evidence = search_hcm(question)
        answer = answer_with_openai(question, evidence)
        client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=answer)
        posted += 1

    return posted


def main() -> None:
    ensure_env()
    client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    auth = client.auth_test()
    bot_user_id = auth.get("user_id")
    if not bot_user_id:
        raise RuntimeError("Could not resolve bot user ID from Slack auth_test.")

    total = 0
    for channel_id in CHANNEL_IDS:
        try:
            total += process_channel(client, channel_id, bot_user_id)
        except SlackApiError as exc:  # noqa: PERF203
            print(f"Slack API error for channel {channel_id}: {exc.response['error']}")

    print(f"Completed polling. Replies posted: {total}")


if __name__ == "__main__":
    main()
