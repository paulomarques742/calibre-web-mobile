# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#  calibre-web-mobile fork: SQLite FTS5 full-text search index.
#
#  The index lives in its own file (``fulltext.db``) next to app.db so it never
#  touches calibre's metadata.db (which calibre desktop owns) and so deleting the
#  file simply forces a rebuild. The calibre-web request session attaches this
#  file as schema ``fts`` (see db.setup_db) and queries ``fts.books_fts``.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

import os
import sqlite3

from . import logger, ub, config

log = logger.create()

SCHEMA_VERSION = "1"

# Column order matters: it drives the bm25 weights in db.search_query.
_COLUMNS = ("title", "authors", "series", "tags", "description", "publisher", "identifiers")


def fts_path():
    """Path to fulltext.db, alongside app.db."""
    if not ub.app_DB_path:
        return None
    return os.path.join(os.path.dirname(ub.app_DB_path), "fulltext.db")


def _metadata_path():
    if not config.config_calibre_dir:
        return None
    path = os.path.join(config.config_calibre_dir, "metadata.db")
    return path if os.path.exists(path) else None


def _connect(attach_metadata=False):
    path = fts_path()
    if not path:
        return None
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    if attach_metadata:
        meta = _metadata_path()
        if not meta:
            conn.close()
            return None
        # Plain attach (portable across platforms). We only ever SELECT from
        # `cal`, so calibre's metadata.db is never written.
        conn.execute("ATTACH DATABASE ? AS cal;", (meta,))
    return conn


def ensure_schema(conn):
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5("
        "title, authors, series, tags, description, publisher, identifiers,"
        "tokenize = 'unicode61 remove_diacritics 2', prefix = '2 3');"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS fts_meta (key TEXT PRIMARY KEY, value TEXT);")
    conn.commit()


def _meta_get(conn, key):
    row = conn.execute("SELECT value FROM fts_meta WHERE key = ?;", (key,)).fetchone()
    return row[0] if row else None


def _calibre_signature(conn):
    """(book_count, max_last_modified) from the attached calibre db."""
    row = conn.execute("SELECT count(*), coalesce(max(last_modified), '') FROM cal.books;").fetchone()
    return str(row[0]), str(row[1])


def is_stale():
    """True when the index is missing, empty, or out of date vs metadata.db."""
    conn = None
    try:
        conn = _connect(attach_metadata=True)
        if conn is None:
            return False  # No library configured yet; nothing to build.
        ensure_schema(conn)
        if _meta_get(conn, "schema_version") != SCHEMA_VERSION:
            return True
        count, last_mod = _calibre_signature(conn)
        return (_meta_get(conn, "book_count") != count
                or _meta_get(conn, "max_last_modified") != last_mod)
    except Exception as ex:
        log.warning("FTS staleness check failed: %s", ex)
        return False
    finally:
        if conn is not None:
            conn.close()


_INSERT_SQL = """
INSERT INTO books_fts(rowid, title, authors, series, tags, description, publisher, identifiers)
SELECT b.id,
  b.title,
  (SELECT group_concat(a.name, ' ') FROM cal.books_authors_link bal
     JOIN cal.authors a ON a.id = bal.author WHERE bal.book = b.id),
  (SELECT group_concat(s.name, ' ') FROM cal.books_series_link bsl
     JOIN cal.series s ON s.id = bsl.series WHERE bsl.book = b.id),
  (SELECT group_concat(t.name, ' ') FROM cal.books_tags_link btl
     JOIN cal.tags t ON t.id = btl.tag WHERE btl.book = b.id),
  (SELECT group_concat(c.text, ' ') FROM cal.comments c WHERE c.book = b.id),
  (SELECT group_concat(p.name, ' ') FROM cal.books_publishers_link bpl
     JOIN cal.publishers p ON p.id = bpl.publisher WHERE bpl.book = b.id),
  (SELECT group_concat(i.type || ':' || i.val, ' ') FROM cal.identifiers i WHERE i.book = b.id)
FROM cal.books b
"""


def rebuild():
    """Fully rebuild the index from metadata.db. Runs in one transaction."""
    conn = None
    try:
        conn = _connect(attach_metadata=True)
        if conn is None:
            log.info("FTS rebuild skipped: no calibre library configured")
            return False
        ensure_schema(conn)
        conn.execute("BEGIN;")
        conn.execute("DELETE FROM books_fts;")
        conn.execute(_INSERT_SQL + ";")
        count, last_mod = _calibre_signature(conn)
        for key, value in (("schema_version", SCHEMA_VERSION),
                           ("book_count", count),
                           ("max_last_modified", last_mod)):
            conn.execute("INSERT INTO fts_meta(key, value) VALUES(?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value;", (key, value))
        conn.commit()
        log.info("FTS index rebuilt: %s books", count)
        return True
    except Exception as ex:
        log.error("FTS rebuild failed: %s", ex)
        if conn is not None:
            conn.rollback()
        return False
    finally:
        if conn is not None:
            conn.close()


def update_book(book_id):
    """Refresh a single book's row (best effort)."""
    conn = None
    try:
        conn = _connect(attach_metadata=True)
        if conn is None:
            return
        ensure_schema(conn)
        conn.execute("BEGIN;")
        conn.execute("DELETE FROM books_fts WHERE rowid = ?;", (book_id,))
        conn.execute(_INSERT_SQL + " WHERE b.id = ?;", (book_id,))
        # Keep the signature current so a targeted update doesn't trigger a full rebuild.
        count, last_mod = _calibre_signature(conn)
        conn.execute("INSERT INTO fts_meta(key, value) VALUES('book_count', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value;", (count,))
        conn.execute("INSERT INTO fts_meta(key, value) VALUES('max_last_modified', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value;", (last_mod,))
        conn.commit()
    except Exception as ex:
        log.warning("FTS update_book(%s) failed: %s", book_id, ex)
        if conn is not None:
            conn.rollback()
    finally:
        if conn is not None:
            conn.close()


def delete_book(book_id):
    conn = None
    try:
        conn = _connect(attach_metadata=False)
        if conn is None:
            return
        ensure_schema(conn)
        conn.execute("DELETE FROM books_fts WHERE rowid = ?;", (book_id,))
        conn.commit()
    except Exception as ex:
        log.warning("FTS delete_book(%s) failed: %s", book_id, ex)
    finally:
        if conn is not None:
            conn.close()


def build_match_query(term):
    """Turn a user query into an FTS5 MATCH string: each token quoted (safe),
    the final token gets a prefix ``*`` for as-you-type matching, joined with
    implicit AND. Returns None when there is nothing searchable."""
    if not term:
        return None
    tokens = [t for t in term.strip().split() if t]
    if not tokens:
        return None
    parts = []
    for i, tok in enumerate(tokens):
        escaped = tok.replace('"', '""')
        if i == len(tokens) - 1 and len(escaped) >= 2:
            parts.append('"{}"*'.format(escaped))
        else:
            parts.append('"{}"'.format(escaped))
    return " ".join(parts)
