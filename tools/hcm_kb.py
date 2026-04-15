#!/usr/bin/env python3
"""Build and query a local, durable PDF knowledge base."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

DEFAULT_DB_PATH = Path("kb/hcm_kb.sqlite")
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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_db_path(db_path: str | Path) -> Path:
    path = Path(db_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            path TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            page_count INTEGER NOT NULL,
            indexed_page_count INTEGER NOT NULL,
            indexed_at TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS page_index USING fts5(
            path UNINDEXED,
            page_number UNINDEXED,
            content
        );
        """
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def find_pdfs(root: Path, recursive: bool) -> list[Path]:
    if recursive:
        return sorted(path for path in root.rglob("*.pdf") if path.is_file())
    return sorted(path for path in root.glob("*.pdf") if path.is_file())


def extract_pages(pdf_path: Path) -> tuple[int, list[tuple[int, str]]]:
    reader = PdfReader(str(pdf_path))
    indexed_pages: list[tuple[int, str]] = []
    page_count = len(reader.pages)

    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").replace("\x00", " ").strip()
        if text:
            indexed_pages.append((page_number, text))

    return page_count, indexed_pages


def ingest_pdf(conn: sqlite3.Connection, pdf_path: Path, force: bool) -> str:
    absolute_path = str(pdf_path.resolve())
    current_hash = file_sha256(pdf_path)

    row = conn.execute(
        "SELECT sha256 FROM documents WHERE path = ?",
        (absolute_path,),
    ).fetchone()

    if row and row[0] == current_hash and not force:
        return "skipped"

    page_count, indexed_pages = extract_pages(pdf_path)

    conn.execute("DELETE FROM page_index WHERE path = ?", (absolute_path,))
    conn.execute(
        """
        INSERT OR REPLACE INTO documents (path, sha256, page_count, indexed_page_count, indexed_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (absolute_path, current_hash, page_count, len(indexed_pages), utc_now_iso()),
    )

    conn.executemany(
        "INSERT INTO page_index (path, page_number, content) VALUES (?, ?, ?)",
        [(absolute_path, page_number, content) for page_number, content in indexed_pages],
    )

    return "indexed" if row else "added"


def cmd_ingest(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.db_path)
    pdf_root = Path(args.pdf_dir).expanduser().resolve()
    if not pdf_root.exists() or not pdf_root.is_dir():
        print(f"PDF directory does not exist: {pdf_root}", file=sys.stderr)
        return 1

    pdf_files = find_pdfs(pdf_root, recursive=args.recursive)
    if not pdf_files:
        print(f"No PDFs found in {pdf_root}")
        return 0

    conn = connect_db(db_path)
    ensure_schema(conn)

    added = 0
    indexed = 0
    skipped = 0
    failed = 0

    for pdf in pdf_files:
        try:
            status = ingest_pdf(conn, pdf, force=args.force)
            conn.commit()
            if status == "added":
                added += 1
            elif status == "indexed":
                indexed += 1
            else:
                skipped += 1
            print(f"{status:7} {pdf}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            conn.rollback()
            print(f"failed  {pdf} ({exc})", file=sys.stderr)

    print(
        f"\nIngest complete: {len(pdf_files)} files scanned | "
        f"{added} new | {indexed} updated | {skipped} skipped | {failed} failed"
    )

    if failed:
        return 2
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.db_path)
    if not db_path.exists():
        print(f"Database does not exist: {db_path}", file=sys.stderr)
        return 1

    conn = connect_db(db_path)
    ensure_schema(conn)

    rows = search_index(conn, args.query, args.limit)

    if not rows:
        print("No results.")
        return 0

    for idx, (path, page_number, score, preview) in enumerate(rows, start=1):
        print(f"{idx}. {Path(path).name} (page {page_number}, score {score:.3f})")
        print(f"   {preview}")
    return 0


def search_index(
    conn: sqlite3.Connection, fts_query: str, limit: int
) -> list[tuple[str, int, float, str]]:
    return conn.execute(
        """
        SELECT
            path,
            page_number,
            bm25(page_index) AS score,
            snippet(page_index, 2, '[', ']', ' ... ', 24) AS preview
        FROM page_index
        WHERE page_index MATCH ?
        ORDER BY score
        LIMIT ?
        """,
        (fts_query, limit),
    ).fetchall()


def question_to_fts_query(question: str, max_terms: int = 12) -> str:
    tokens = re.findall(r"[a-z0-9]{3,}", question.lower())
    filtered = [token for token in tokens if token not in QUESTION_STOPWORDS]

    unique_terms: list[str] = []
    seen: set[str] = set()
    for token in filtered:
        if token not in seen:
            unique_terms.append(token)
            seen.add(token)
        if len(unique_terms) >= max_terms:
            break

    if unique_terms:
        return " OR ".join(unique_terms)
    return question.strip()


def cmd_ask(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.db_path)
    if not db_path.exists():
        print(f"Database does not exist: {db_path}", file=sys.stderr)
        return 1

    conn = connect_db(db_path)
    ensure_schema(conn)

    fts_query = question_to_fts_query(args.question)
    if not fts_query:
        print("Question is empty.", file=sys.stderr)
        return 1

    rows = search_index(conn, fts_query, args.limit)
    if not rows:
        print("No evidence found for that question.")
        return 0

    print(f"Question: {args.question}")
    print(f"Search query: {fts_query}")
    print("\nTop evidence passages:")
    for idx, (path, page_number, score, preview) in enumerate(rows, start=1):
        print(f"{idx}. {Path(path).name} (page {page_number}, score {score:.3f})")
        print(f"   {preview}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.db_path)
    if not db_path.exists():
        print(f"Database does not exist: {db_path}", file=sys.stderr)
        return 1

    conn = connect_db(db_path)
    ensure_schema(conn)

    doc_count, page_count, indexed_page_count = conn.execute(
        """
        SELECT
            COUNT(*),
            COALESCE(SUM(page_count), 0),
            COALESCE(SUM(indexed_page_count), 0)
        FROM documents
        """
    ).fetchone()
    last_indexed = conn.execute("SELECT MAX(indexed_at) FROM documents").fetchone()[0]

    print(f"Database: {db_path}")
    print(f"Documents indexed: {doc_count}")
    print(f"Total PDF pages: {page_count}")
    print(f"Indexed pages with text: {indexed_page_count}")
    print(f"Last indexed: {last_indexed or 'n/a'}")
    return 0


def cleanup_sqlite_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()


def copy_sqlite_database(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        destination.unlink()
    cleanup_sqlite_sidecars(destination)

    source_conn = sqlite3.connect(source)
    try:
        source_conn.execute("PRAGMA wal_checkpoint(FULL);")
        source_conn.commit()
        destination_conn = sqlite3.connect(destination)
        try:
            source_conn.backup(destination_conn)
            destination_conn.commit()
        finally:
            destination_conn.close()
    finally:
        source_conn.close()

    cleanup_sqlite_sidecars(source)
    cleanup_sqlite_sidecars(destination)


def cmd_export(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.db_path)
    export_path = Path(args.output).expanduser().resolve()
    if not db_path.exists():
        print(f"Database does not exist: {db_path}", file=sys.stderr)
        return 1

    copy_sqlite_database(db_path, export_path)
    print(f"Exported KB database to {export_path}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    source_path = Path(args.input).expanduser().resolve()
    db_path = resolve_db_path(args.db_path)
    if not source_path.exists():
        print(f"Input database does not exist: {source_path}", file=sys.stderr)
        return 1

    copy_sqlite_database(source_path, db_path)
    print(f"Imported KB database to {db_path}")
    return 0


def cmd_backup_copy(args: argparse.Namespace) -> int:
    """Copy exported DB to a target location (external sync helper)."""
    source_path = Path(args.source).expanduser().resolve()
    destination_path = Path(args.destination).expanduser().resolve()
    if not source_path.exists():
        print(f"Source file does not exist: {source_path}", file=sys.stderr)
        return 1
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)
    print(f"Copied {source_path} -> {destination_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest PDFs into a local SQLite FTS knowledge base and query it."
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help=f"Path for the SQLite KB database (default: {DEFAULT_DB_PATH})",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Ingest PDFs from a directory")
    ingest.add_argument("pdf_dir", help="Directory containing PDF files")
    ingest.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively scan for PDF files",
    )
    ingest.add_argument(
        "--force",
        action="store_true",
        help="Re-index files even if hashes are unchanged",
    )
    ingest.set_defaults(func=cmd_ingest)

    query = subparsers.add_parser("query", help="Search the indexed KB")
    query.add_argument("query", help="FTS query string")
    query.add_argument("--limit", type=int, default=8, help="Max results to return")
    query.set_defaults(func=cmd_query)

    ask = subparsers.add_parser(
        "ask",
        help="Question-oriented retrieval with cited evidence passages",
    )
    ask.add_argument("question", help="Natural language question")
    ask.add_argument("--limit", type=int, default=8, help="Max evidence passages")
    ask.set_defaults(func=cmd_ask)

    stats = subparsers.add_parser("stats", help="Show KB index statistics")
    stats.set_defaults(func=cmd_stats)

    export = subparsers.add_parser(
        "export-db",
        help="Export the SQLite database to a portable file path",
    )
    export.add_argument("output", help="Output SQLite file path")
    export.set_defaults(func=cmd_export)

    import_db = subparsers.add_parser(
        "import-db",
        help="Import a previously exported SQLite database",
    )
    import_db.add_argument("input", help="Input SQLite file path")
    import_db.set_defaults(func=cmd_import)

    backup_copy = subparsers.add_parser(
        "copy-backup",
        help="Copy an exported database file to another location",
    )
    backup_copy.add_argument("source", help="Source SQLite file path")
    backup_copy.add_argument("destination", help="Destination file path")
    backup_copy.set_defaults(func=cmd_backup_copy)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
