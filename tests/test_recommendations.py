"""Integration test for recommendations against a fixture calibre library."""
import os
import sys
import types
import tempfile
import sqlite3

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
CONFIG = tempfile.mkdtemp(prefix="cwm-recs-")
os.environ["CALIBRE_DBPATH"] = CONFIG
os.environ.setdefault("SECRET_KEY", "recs-secret")
sys.argv = ["cps.py"]

_m = types.ModuleType("magic")
_m.from_buffer = lambda *a, **k: "text/plain"
_m.from_file = lambda *a, **k: "text/plain"
_m.Magic = type("Magic", (), {"__init__": lambda s, *a, **k: None, "from_buffer": lambda s, *a, **k: "text/plain"})
sys.modules["magic"] = _m

calibre_dir = os.path.join(CONFIG, "lib")
os.makedirs(calibre_dir, exist_ok=True)


def make_metadata_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT, author_sort TEXT,
            timestamp TEXT, pubdate TEXT, series_index REAL DEFAULT 1.0, last_modified TEXT,
            path TEXT DEFAULT '', has_cover INTEGER DEFAULT 0, uuid TEXT DEFAULT '', flags INTEGER DEFAULT 1);
        CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT DEFAULT '');
        CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INTEGER, author INTEGER);
        CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
        CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INTEGER, series INTEGER);
        CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INTEGER, tag INTEGER);
        CREATE TABLE comments (id INTEGER PRIMARY KEY, book INTEGER, text TEXT);
        CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
        CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INTEGER, publisher INTEGER);
        CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INTEGER, type TEXT, val TEXT);
        CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT, uncompressed_size INTEGER, name TEXT);
        CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT);
        CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INTEGER, lang_code INTEGER);
        CREATE TABLE ratings (id INTEGER PRIMARY KEY, rating INTEGER);
        CREATE TABLE books_ratings_link (id INTEGER PRIMARY KEY, book INTEGER, rating INTEGER);
        CREATE TABLE custom_columns (id INTEGER PRIMARY KEY, label TEXT, name TEXT, datatype TEXT,
            display TEXT, is_multiple BOOL, normalized BOOL);
    """)
    # 5 fantasy books (tag 1), 3 sci-fi (tag 2). Authors: 1=fantasy author (books 1,2).
    ts = "2020-01-01 00:00:00+00:00"
    books = [(i, "Book %d" % i, "Book %02d" % i, "Auth", ts, ts, 1.0, ts) for i in range(1, 9)]
    con.executemany("INSERT INTO books(id,title,sort,author_sort,timestamp,pubdate,series_index,last_modified) "
                    "VALUES(?,?,?,?,?,?,?,?)", books)
    con.executemany("INSERT INTO authors(id,name,sort) VALUES(?,?,?)",
                    [(1, "Fantasy Author", "Author, Fantasy"), (2, "SciFi Author", "Author, SciFi")])
    # books 1,2 -> author 1; 3,4,5 -> other; 6,7,8 -> author 2
    al = [(1, 1), (2, 1), (3, 1), (4, 1), (5, 1), (6, 2), (7, 2), (8, 2)]
    con.executemany("INSERT INTO books_authors_link(book,author) VALUES(?,?)", al)
    con.executemany("INSERT INTO tags(id,name) VALUES(?,?)", [(1, "Fantasy"), (2, "Science Fiction")])
    tl = [(1, 1), (2, 1), (3, 1), (4, 1), (5, 1), (6, 2), (7, 2), (8, 2)]
    con.executemany("INSERT INTO books_tags_link(book,tag) VALUES(?,?)", tl)
    con.commit()
    con.close()


make_metadata_db(os.path.join(calibre_dir, "metadata.db"))

from cps import create_app, config, ub, db, calibre_db  # noqa: E402
app = create_app()

# Point calibre_db at the fixture library.
object.__setattr__(config, "config_calibre_dir", calibre_dir)
db.CalibreDB.update_config(config, calibre_dir, ub.app_DB_path)
calibre_db.reconnect_db(config, ub.app_DB_path)

rc = 0
def check(label, cond, extra=""):
    global rc
    print(("PASS " if cond else "FAIL ") + label + ((" :: " + str(extra)) if extra else ""))
    if not cond:
        rc = 1

import cps.recommendations as recs  # noqa: E402
from cps.cw_login import login_user  # noqa: E402

USER = 1  # admin user id
with app.test_request_context():
    admin = ub.session.query(ub.User).filter(ub.User.id == USER).first()
    login_user(admin)

    check("calibre_db sees 8 books", calibre_db.session.query(db.Books).count() == 8,
          calibre_db.session.query(db.Books).count())

    # Cold start: no signals -> popular fallback (empty, no downloads/likes yet).
    recs.invalidate(USER)
    cold = recs.get_recommended_ids(USER, limit=10)
    check("cold start returns list (no crash)", isinstance(cold, list), cold)

    # Strong signal: rate fantasy book 1 five stars, like fantasy book 2.
    ub.session.add(ub.BookRating(book_id=1, user_id=USER, rating=5))
    ub.session.add(ub.BookLike(book_id=2, user_id=USER))
    ub.session.commit()
    recs.invalidate(USER)

    ids = recs.get_recommended_ids(USER, limit=10)
    check("recommendations returned", len(ids) > 0, ids)
    check("seed book 1 excluded", 1 not in ids, ids)
    check("liked book 2 excluded", 2 not in ids, ids)
    fantasy = [b for b in ids if b in (3, 4, 5)]
    scifi = [b for b in ids if b in (6, 7, 8)]
    check("fantasy books recommended", len(fantasy) >= 1, ids)
    if fantasy and scifi:
        check("a fantasy book outranks first sci-fi book", ids.index(fantasy[0]) < ids.index(scifi[0]), ids)
    else:
        check("fantasy dominates (no scifi surfaced)", len(scifi) == 0, ids)

    # Hydration path (exercises fill_indexpage + case ordering).
    books = recs.get_recommended_books(USER, limit=5)
    check("get_recommended_books hydrates Books", all(hasattr(b, "title") for b in books), [b.id for b in books])

print("\nRESULT", "OK" if rc == 0 else "FAILURES", flush=True)
os._exit(rc)
