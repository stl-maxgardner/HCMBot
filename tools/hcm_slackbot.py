#!/usr/bin/env python3
"""Slack Events API bot for querying the HCM SQLite knowledge base."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
from google import genai
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from slack_sdk.signature import SignatureVerifier

DB_PATH = Path(os.getenv("HCM_DB_PATH", "kb/hcm_kb.sqlite"))
TOP_K = int(os.getenv("HCM_TOP_K", "8"))
MAX_CHARS_PER_SNIPPET = int(os.getenv("HCM_MAX_CHARS_PER_SNIPPET", "650"))
VERTEX_MODEL = os.getenv("VERTEX_MODEL", "gemini-2.0-flash-001")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

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
    required = [
        "SLACK_BOT_TOKEN",
        "SLACK_SIGNING_SECRET",
        "GOOGLE_CLOUD_PROJECT",
        "SLACK_ALLOWED_TEAM_IDS",
        "SLACK_ALLOWED_APP_IDS",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
    if not parse_csv_env("SLACK_ALLOWED_TEAM_IDS"):
        raise RuntimeError("SLACK_ALLOWED_TEAM_IDS must contain at least one team ID.")
    if not parse_csv_env("SLACK_ALLOWED_APP_IDS"):
        raise RuntimeError("SLACK_ALLOWED_APP_IDS must contain at least one app ID.")
    if not DB_PATH.exists():
        raise RuntimeError(f"HCM DB not found: {DB_PATH}")


def parse_csv_env(name: str) -> set[str]:
    value = os.getenv(name, "")
    return {item.strip() for item in value.split(",") if item.strip()}


def question_to_fts_query(question: str, max_terms: int = 12) -> str:
    tokens = re.findall(r"[a-z0-9]{3,}", question.lower())
    filtered = [token for token in tokens if token not in QUESTION_STOPWORDS]
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
    snippets = []
    for idx, item in enumerate(evidence, start=1):
        snippets.append(
            (
                f"[{idx}] {item['doc_name']} p.{item['page']}\n"
                f"Preview: {item['preview']}\n"
                f"Text: {item['content']}\n"
            )
        )

    return (
        "You answer Highway Capacity Manual questions.\n"
        "Use only the evidence snippets provided.\n"
        "If evidence is weak or conflicting, state uncertainty.\n"
        "Respond with:\n"
        "- concise answer (2-6 bullets)\n"
        "- 'Citations' section with filename + page\n\n"
        f"Question: {question}\n\n"
        f"Evidence:\n{''.join(snippets)}"
    )


def answer_with_vertex(question: str, evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return (
            "I couldn't find strong evidence in the HCM index for that question. "
            "Try rephrasing or narrowing the topic."
        )

    client = genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=GOOGLE_CLOUD_LOCATION,
    )
    prompt = build_prompt(question, evidence)
    response = client.models.generate_content(model=VERTEX_MODEL, contents=prompt)
    text = (response.text or "").strip()
    return text or "I couldn't generate an answer right now."


def handle_question(question: str, say) -> None:  # type: ignore[no-untyped-def]
    if not question:
        say("Ask me an HCM question.")
        return
    say("Searching the HCM knowledge base...")
    evidence = search_hcm(question)
    answer = answer_with_vertex(question, evidence)
    say(answer)


def create_bolt_app() -> App:
    app = App(
        token=os.environ["SLACK_BOT_TOKEN"],
        signing_secret=os.environ["SLACK_SIGNING_SECRET"],
    )

    @app.event("app_mention")
    def handle_app_mention(event: dict[str, Any], say) -> None:  # type: ignore[no-untyped-def]
        raw = event.get("text", "")
        question = re.sub(r"<@[^>]+>", "", raw).strip()
        if not question:
            say("Ask me an HCM question after mentioning me.")
            return
        handle_question(question, say)

    @app.event("message")
    def handle_direct_message(event: dict[str, Any], say) -> None:  # type: ignore[no-untyped-def]
        if event.get("channel_type") != "im":
            return
        if event.get("bot_id") or event.get("subtype"):
            return
        text = (event.get("text") or "").strip()
        if not text:
            return
        handle_question(text, say)

    return app


def create_flask_app() -> Flask:
    ensure_env()
    bolt_app = create_bolt_app()
    handler = SlackRequestHandler(bolt_app)
    signature_verifier = SignatureVerifier(os.environ["SLACK_SIGNING_SECRET"])
    allowed_team_ids = parse_csv_env("SLACK_ALLOWED_TEAM_IDS")
    allowed_app_ids = parse_csv_env("SLACK_ALLOWED_APP_IDS")
    app = Flask(__name__)

    @app.get("/")
    def health() -> Any:
        return jsonify({"ok": True})

    @app.post("/slack/events")
    def slack_events() -> Any:
        body = request.get_data()
        if not signature_verifier.is_valid_request(body=body, headers=request.headers):
            return jsonify({"ok": False, "error": "invalid_signature"}), 401

        payload = request.get_json(silent=True) or {}
        if payload.get("type") == "url_verification" and "challenge" in payload:
            return jsonify({"challenge": payload["challenge"]})

        team_id = payload.get("team_id")
        app_id = payload.get("api_app_id")
        if team_id not in allowed_team_ids or app_id not in allowed_app_ids:
            return jsonify({"ok": False, "error": "forbidden_workspace_or_app"}), 403
        return handler.handle(request)

    return app


def main() -> None:
    app = create_flask_app()
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
