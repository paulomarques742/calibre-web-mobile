# calibre-web-mobile fork tests

Integration smoke tests for the mobile/social/search/recommendation features
added in this fork. They boot the real app against a throwaway config dir and a
tiny fixture calibre library — no external services, no network.

## Running

```bash
python tests/smoke_boot.py            # boot + app.db schema + PWA routes
python tests/test_fts.py              # FTS5 index build/query end to end
python tests/test_recommendations.py  # content-based recommendations
```

Each script prints `PASS`/`FAIL` lines and exits non-zero on failure
(`RESULT OK` at the end means all passed).

## What they cover

- **smoke_boot.py** — the app starts with all fork changes; `book_rating`,
  `book_review`, `book_like`, `review_like` tables are created; the
  `user.privacy_hide_activity` column is migrated in; `migrate_Database` is
  idempotent on re-run; `/manifest.json`, `/sw.js`, `/offline` respond.
- **test_fts.py** — `fts.build_match_query` (incl. quote-injection escaping and
  as-you-type prefix), `is_stale`/`rebuild`/`update_book`/`delete_book`, and
  bm25-ranked MATCH results over title/author/tag/series/description.
- **test_recommendations.py** — seed weighting, taste profile, candidate
  scoring, exclusion of already-seen books, and the `fill_indexpage` hydration
  path (a fantasy rating surfaces other fantasy books and no sci-fi).

## Note on `python-magic-bin` (Windows / Python 3.14 only)

Each script pre-seeds a stub `magic` module in `sys.modules`. The pinned
`python-magic-bin==0.4.14` ships a libmagic DLL that **segfaults** on
Python 3.14 at `import magic`. The stub avoids loading that DLL; it does not
affect any behaviour under test. On Linux/Docker with a working `libmagic`
this stub is harmless. If you run the app itself on Python 3.14 + Windows,
prefer a supported Python (3.11–3.12) or a `libmagic` that loads cleanly.
