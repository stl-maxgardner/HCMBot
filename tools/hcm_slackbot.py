#!/usr/bin/env python3
"""Slack bot for querying HCM SQLite knowledge base."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from openai import OpenAI
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

DB_PATH = Path(os.getenv("HCM_DB_PATH", "kb/hcm_kb.sqlite"))
TOP_K = int(os.getenv("HCM_TOP_K", "8"))
MAX_CHARS_PER_SNIPPET = int(os.getenv("HCM_MAX_CHARS_PER_SNIPPET", "650"))

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
    required = ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "OPENAI_API_KEY"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
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

    results = []
    for path, page, score, preview, content in rows:
        results.append(
            {
                "doc_name": Path(path).name,
                "page": page,
                "score": score,
                "preview": preview,
                "content": content,
            }
        )
    return results


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

    # Fallback for SDK variants.
    chunks = []
    for item in getattr(response, "output", []):
        for content in getattr(item, "content", []):
            if getattr(content, "type", "") == "output_text":
                chunks.append(getattr(content, "text", ""))
    joined = "\n".join(c for c in chunks if c).strip()
    return joined or "I couldn't generate an answer right now."


def create_app() -> App:
    app = App(token=os.getenv("SLACK_BOT_TOKEN"))

    @app.event("app_mention")
    def handle_app_mention(event: dict[str, Any], say) -> None:  # type: ignore[no-untyped-def]
        raw = event.get("text", "")
        question = re.sub(r"<@[^>]+>", "", raw).strip()
        if not question:
            say("Ask me an HCM question after mentioning me.")
            return

        say("Looking that up in the HCM knowledge base...")
        evidence = search_hcm(question)
        answer = answer_with_openai(question, evidence)
        say(answer)

    @app.message(re.compile(r"^hcm:\s+", re.IGNORECASE))
    def handle_prefixed_message(message: dict[str, Any], say) -> None:  # type: ignore[no-untyped-def]
        text = message.get("text", "")
        question = re.sub(r"^hcm:\s+", "", text, flags=re.IGNORECASE).strip()
        if not question:
            say("Use `hcm: <your question>`.")
            return
        say("Searching HCM...")
        evidence = search_hcm(question)
        answer = answer_with_openai(question, evidence)
        say(answer)

    return app


def main() -> None:
    ensure_env()
    app = create_app()
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()


if __name__ == "__main__":
    main()
