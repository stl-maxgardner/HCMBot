# HCMBot

## Durable local PDF knowledge base (without committing PDFs)

This repo includes a lightweight knowledge-base CLI at `tools/hcm_kb.py` that:

- Ingests PDF text into a local SQLite FTS index
- Supports fast keyword querying
- Lets you export/import the index so knowledge persists across cloud sessions
- Keeps raw PDFs and local index files out of git

### 1) Install dependency

```bash
python3 -m pip install -r requirements.txt
```

### 2) Ingest PDFs

Use any local directory that contains your HCM PDFs.

```bash
python3 tools/hcm_kb.py ingest /path/to/pdfs --recursive
```

By default, the index is stored at:

```text
.local_kb/hcm_kb.sqlite
```

That path is git-ignored via `.gitignore`.

### 3) Query the KB

```bash
python3 tools/hcm_kb.py query "two lane highway capacity" --limit 5
python3 tools/hcm_kb.py stats
```

### 4) Persist KB outside ephemeral workspace

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
- Exported DB files are portable SQLite files and can be backed up anywhere.
- Re-running `ingest` only re-indexes changed PDFs (hash-based skip).
