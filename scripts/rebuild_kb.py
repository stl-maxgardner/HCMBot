#!/usr/bin/env python3
"""Rebuild or refresh kb/hcm_kb.sqlite from a local PDF directory."""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

DEFAULT_DB = Path("kb/hcm_kb.sqlite")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


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


def extract_pages(pdf_path: Path) -> tuple[int, list[tuple[int, str]]]:
    reader = PdfReader(str(pdf_path))
    page_count = len(reader.pages)
    extracted: list[tuple[int, str]] = []
    for page_no, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").replace("\x00", " ").strip()
        if text:
            extracted.append((page_no, text))
    return page_count, extracted


def infer_label_from_filename(path: Path) -> str:
    name = path.stem.lower()

    if "1985" in name:
        return "hcm_1985.pdf"
    if "2000" in name:
        return "hcm_2000_4th_edition.pdf"
    if "2010" in name:
        return "hcm_2010_5th_edition.pdf"

    if "2016" in name:
        if any(
            token in name
            for token in ("vol1", "vol_1", "vol-1", "volume1", "volume_1", "volume-1", "v1")
        ):
            return "hcm_2016_vol1.pdf"
        if any(
            token in name
            for token in ("vol2", "vol_2", "vol-2", "volume2", "volume_2", "volume-2", "v2")
        ):
            return "hcm_2016_vol2.pdf"
        if any(
            token in name
            for token in ("vol3", "vol_3", "vol-3", "volume3", "volume_3", "volume-3", "v3")
        ):
            return "hcm_2016_vol3.pdf"

    # Fall back to sanitized basename if no known pattern matched.
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in path.stem)
    return f"{cleaned}.pdf"


def upsert_pdf(conn: sqlite3.Connection, pdf_path: Path, label: str, force: bool) -> str:
    display_path = f"/virtual_hcm_docs/{label}"
    current_hash = file_sha256(pdf_path)
    existing = conn.execute(
        "SELECT sha256 FROM documents WHERE path = ?",
        (display_path,),
    ).fetchone()

    if existing and existing[0] == current_hash and not force:
        return "skipped"

    page_count, pages = extract_pages(pdf_path)

    conn.execute("DELETE FROM page_index WHERE path = ?", (display_path,))
    conn.execute(
        """
        INSERT OR REPLACE INTO documents (path, sha256, page_count, indexed_page_count, indexed_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (display_path, current_hash, page_count, len(pages), utc_now_iso()),
    )
    conn.executemany(
        "INSERT INTO page_index (path, page_number, content) VALUES (?, ?, ?)",
        [(display_path, page_no, content) for page_no, content in pages],
    )
    return "updated" if existing else "added"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build or refresh kb/hcm_kb.sqlite from PDFs outside the repo."
    )
    parser.add_argument(
        "--pdf-dir",
        required=True,
        help="Directory containing source PDFs (can be outside repo).",
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB),
        help=f"Output sqlite DB path (default: {DEFAULT_DB}).",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively scan for PDFs.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing indexed docs before rebuilding.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-index PDFs even when file hash is unchanged.",
    )
    args = parser.parse_args()

    pdf_root = Path(args.pdf_dir).expanduser().resolve()
    if not pdf_root.exists() or not pdf_root.is_dir():
        print(f"PDF directory not found: {pdf_root}", file=sys.stderr)
        return 1

    db_path = Path(args.db_path).expanduser()
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    pattern_iter = pdf_root.rglob("*.pdf") if args.recursive else pdf_root.glob("*.pdf")
    pdfs = sorted(p for p in pattern_iter if p.is_file())
    if not pdfs:
        print(f"No PDFs found in {pdf_root}")
        return 0

    conn = sqlite3.connect(db_path)
    ensure_schema(conn)

    if args.reset:
        conn.execute("DELETE FROM page_index")
        conn.execute("DELETE FROM documents")
        conn.commit()

    seen_labels: set[str] = set()
    added = 0
    updated = 0
    skipped = 0
    failed = 0

    for pdf in pdfs:
        try:
            label = infer_label_from_filename(pdf)
            if label in seen_labels:
                raise ValueError(f"Duplicate inferred label '{label}'")
            seen_labels.add(label)

            status = upsert_pdf(conn, pdf, label, force=args.force)
            conn.commit()
            if status == "added":
                added += 1
            elif status == "updated":
                updated += 1
            else:
                skipped += 1
            print(f"{status:7} {pdf} -> {label}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            conn.rollback()
            print(f"failed  {pdf} ({exc})", file=sys.stderr)

    docs, total_pages, indexed_pages = conn.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(page_count), 0), COALESCE(SUM(indexed_page_count), 0)
        FROM documents
        """
    ).fetchone()
    conn.close()

    print(
        "\nRebuild complete: "
        f"{len(pdfs)} scanned | {added} added | {updated} updated | {skipped} skipped | {failed} failed"
    )
    print(f"DB: {db_path}")
    print(f"Docs: {docs} | Total pages: {total_pages} | Indexed pages: {indexed_pages}")

    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
