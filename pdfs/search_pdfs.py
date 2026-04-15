#!/usr/bin/env python3
"""
Search tool for the Highway Capacity Manual PDF knowledge base.
Supports keyword search across all documents with page number references.
"""

import json
import re
import sys
from typing import List, Dict, Tuple

KB_FILE = '/workspace/pdfs/knowledge_base.json'

def load_kb():
    with open(KB_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def search(kb: dict, query: str, max_results: int = 10, context_chars: int = 400) -> List[Dict]:
    """Search the knowledge base for a query string."""
    query_lower = query.lower()
    terms = query_lower.split()
    results = []

    for fname, doc in kb.items():
        if fname.endswith('[dup]') or 'dup' in doc['short_title']:
            continue  # skip duplicates
        for page_num, text in doc['pages'].items():
            text_lower = text.lower()
            # Score: count how many terms appear
            score = sum(text_lower.count(term) for term in terms)
            if score == 0:
                continue
            # Find best context window
            best_pos = -1
            best_score = 0
            for term in terms:
                pos = text_lower.find(term)
                while pos != -1:
                    window = text_lower[max(0, pos-50):pos+200]
                    window_score = sum(window.count(t) for t in terms)
                    if window_score > best_score:
                        best_score = window_score
                        best_pos = pos
                    pos = text_lower.find(term, pos+1)

            if best_pos >= 0:
                start = max(0, best_pos - 150)
                end = min(len(text), best_pos + context_chars)
                snippet = text[start:end].strip()
                # Clean up the snippet
                snippet = re.sub(r'\s+', ' ', snippet)
                results.append({
                    'file': fname,
                    'short_title': doc['short_title'],
                    'page': int(page_num),
                    'score': score,
                    'snippet': snippet,
                })

    results.sort(key=lambda x: -x['score'])
    return results[:max_results]

def get_page_text(kb: dict, fname: str, page_num: int) -> str:
    """Get full text of a specific page."""
    doc = kb.get(fname)
    if not doc:
        return f"Document '{fname}' not found."
    text = doc['pages'].get(str(page_num))
    if not text:
        return f"Page {page_num} not found in '{fname}'."
    return text

def list_docs(kb: dict):
    """List all documents."""
    for fname, doc in kb.items():
        if 'dup' in doc['short_title']:
            continue
        print(f"  {fname}: {doc['short_title']} ({doc['total_pages']} pages)")
        print(f"    {doc['full_title']}")

if __name__ == '__main__':
    kb = load_kb()
    if len(sys.argv) < 2:
        print("Usage: python3 search_pdfs.py <query>")
        print("       python3 search_pdfs.py --list")
        sys.exit(1)
    
    if sys.argv[1] == '--list':
        list_docs(kb)
        sys.exit(0)

    query = ' '.join(sys.argv[1:])
    results = search(kb, query, max_results=5)
    
    if not results:
        print(f"No results found for: '{query}'")
        sys.exit(0)

    print(f"Top results for: '{query}'\n")
    for i, r in enumerate(results, 1):
        print(f"[{i}] {r['short_title']} — Page {r['page']} (score: {r['score']})")
        print(f"     {r['snippet'][:300]}...")
        print()
