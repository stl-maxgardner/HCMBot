# HCMBot

## Persistent HCM knowledge base for agents

This repo includes a lightweight knowledge-base CLI at `tools/hcm_kb.py` and a
prebuilt SQLite index at `kb/hcm_kb.sqlite`.

The goal is persistent agent context: new cloud agents can immediately query HCM
content without re-downloading or re-ingesting all PDFs.

Capabilities:

- Ingest PDF text into a SQLite FTS index
- Run keyword search (`query`) and question-oriented evidence retrieval (`ask`)
- Export/import indexes for external backups
- Keep raw PDFs out of git while optionally versioning the prebuilt DB

### 1) Install dependency

```bash
python3 -m pip install -r requirements.txt
```

### 2) Query immediately (no ingest required)

Because `kb/hcm_kb.sqlite` is committed, you can query right away:

```bash
python3 tools/hcm_kb.py stats
python3 tools/hcm_kb.py ask "What are key delay components at signalized intersections?" --limit 6
```

### 3) Rebuild or refresh the index from PDFs

Use any local directory that contains your HCM PDFs.

```bash
python3 tools/hcm_kb.py ingest /path/to/pdfs --recursive
```

By default, the index path is:

```text
kb/hcm_kb.sqlite
```

For temporary/local-only work, you can still use:

```bash
python3 tools/hcm_kb.py --db-path .local_kb/hcm_kb.sqlite ingest /path/to/pdfs --recursive
```

### 4) Query the KB

```bash
python3 tools/hcm_kb.py query "two lane highway capacity" --limit 5
python3 tools/hcm_kb.py ask "How is LOS determined for unsignalized intersections?" --limit 8
python3 tools/hcm_kb.py stats
```

### 5) Persist KB outside git as backup

Export the DB to a location you sync externally (Drive/Dropbox/S3 mount/etc):

```bash
python3 tools/hcm_kb.py export-db /path/to/synced/hcm_kb.sqlite
```

Later (new cloud session), restore it with:

```bash
python3 tools/hcm_kb.py import-db /path/to/synced/hcm_kb.sqlite
```

Optional helper to copy backups:

```bash
python3 tools/hcm_kb.py copy-backup /path/from/hcm_kb.sqlite /path/to/hcm_kb.sqlite
```

### Notes

- Raw PDFs are not committed unless you explicitly add them to git.
- The committed DB (`kb/hcm_kb.sqlite`) gives fast startup for new agents.
- Re-running `ingest` only re-indexes changed PDFs (hash-based skip).
