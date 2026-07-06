"""Exercise the FTS5 index end to end against a fixture calibre metadata.db,
plus unit-test the query builder. Runs cps.fts's real rebuild()/is_stale()."""
import os
import sys
import types
import tempfile
import sqlite3

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
CONFIG = tempfile.mkdtemp(prefix="cwm-fts-")
os.environ["CALIBRE_DBPATH"] = CONFIG
os.environ.setdefault("SECRET_KEY", "fts-secret")
sys.argv = ["cps.py"]

# Avoid the segfaulting python-magic-bin DLL on py3.14 (test-env only).
_m = types.ModuleType("magic")
_m.from_buffer = lambda *a, **k: "text/plain"
_m.from_file = lambda *a, **k: "text/plain"
_m.Magic = type("Magic", (), {"__init__": lambda self, *a, **k: None,
                              "from_buffer": lambda self, *a, **k: "text/plain"})
sys.modules["magic"] = _m


def make_metadata_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT,
                            timestamp TEXT, last_modified TEXT);
        CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
        CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INTEGER, author INTEGER);
        CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
        CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INTEGER, series INTEGER);
        CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INTEGER, tag INTEGER);
        CREATE TABLE comments (id INTEGER PRIMARY KEY, book INTEGER, text TEXT);
        CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
        CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INTEGER, publisher INTEGER);
        CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INTEGER, type TEXT, val TEXT);
    """)
    con.executemany("INSERT INTO books(id,title,sort,timestamp,last_modified) VALUES(?,?,?,?,?)", [
        (1, "Harry Potter and the Prisoner of Azkaban", "Harry Potter 3", "2020", "2020-01-01"),
        (2, "The Hobbit", "Hobbit", "2019", "2019-01-01"),
        (3, "Dune", "Dune", "2018", "2018-01-01"),
    ])
    con.executemany("INSERT INTO authors(id,name,sort) VALUES(?,?,?)", [
        (1, "J. K. Rowling", "Rowling"), (2, "J. R. R. Tolkien", "Tolkien"), (3, "Frank Herbert", "Herbert")])
    con.executemany("INSERT INTO books_authors_link(book,author) VALUES(?,?)", [(1, 1), (2, 2), (3, 3)])
    con.executemany("INSERT INTO series(id,name,sort) VALUES(?,?,?)", [(1, "Harry Potter", "Harry Potter")])
    con.executemany("INSERT INTO books_series_link(book,series) VALUES(?,?)", [(1, 1)])
    con.executemany("INSERT INTO tags(id,name) VALUES(?,?)", [(1, "Fantasy"), (2, "Science Fiction")])
    con.executemany("INSERT INTO books_tags_link(book,tag) VALUES(?,?)", [(1, 1), (2, 1), (3, 2)])
    con.executemany("INSERT INTO comments(book,text) VALUES(?,?)", [
        (1, "<p>A young wizard learns about Sirius Black.</p>"),
        (3, "Paul Atreides on the desert planet Arrakis.")])
    con.executemany("INSERT INTO publishers(id,name,sort) VALUES(?,?,?)", [(1, "Bloomsbury", "Bloomsbury")])
    con.executemany("INSERT INTO books_publishers_link(book,publisher) VALUES(?,?)", [(1, 1)])
    con.commit()
    con.close()


rc = 0
def check(label, cond, extra=""):
    global rc
    print(("PASS " if cond else "FAIL ") + label + ((" :: " + str(extra)) if extra else ""))
    if not cond:
        rc = 1

# Fixture library
calibre_dir = os.path.join(CONFIG, "lib")
os.makedirs(calibre_dir, exist_ok=True)
make_metadata_db(os.path.join(calibre_dir, "metadata.db"))

from cps import fts, ub, config  # noqa: E402
# Point the module at our fixture without booting the whole app.
ub.app_DB_path = os.path.join(CONFIG, "app.db")
object.__setattr__(config, "config_calibre_dir", calibre_dir)

# --- build_match_query (pure) ------------------------------------------
check("empty -> None", fts.build_match_query("") is None)
check("single token prefix", fts.build_match_query("dune") == '"dune"*', fts.build_match_query("dune"))
check("multi token AND + prefix",
      fts.build_match_query("harry azkab") == '"harry" "azkab"*', fts.build_match_query("harry azkab"))
check("quote injection escaped", '""' in fts.build_match_query('a"b cd'), fts.build_match_query('a"b cd'))

# --- staleness before build --------------------------------------------
check("stale before first build", fts.is_stale() is True)

# --- rebuild -----------------------------------------------------------
check("rebuild succeeds", fts.rebuild() is True)
check("fulltext.db created", os.path.exists(fts.fts_path()))
check("not stale after build", fts.is_stale() is False)

# --- query the index ---------------------------------------------------
conn = sqlite3.connect(fts.fts_path())
def match(q):
    mq = fts.build_match_query(q)
    return [r[0] for r in conn.execute(
        "SELECT rowid FROM books_fts WHERE books_fts MATCH ? "
        "ORDER BY bm25(books_fts,10.0,8.0,7.0,6.0,1.0,3.0,3.0)", (mq,)).fetchall()]

check("row count == 3", conn.execute("SELECT count(*) FROM books_fts").fetchone()[0] == 3)
check("title prefix multi-token 'harri'->'harry' NOT (typo)", match("harri potter") == [], match("harri potter"))
check("title match 'harry azkab' -> book 1", match("harry azkab") == [1], match("harry azkab"))
check("author match 'tolkien' -> book 2", match("tolkien") == [2], match("tolkien"))
check("tag match 'fantasy' -> books 1,2", sorted(match("fantasy")) == [1, 2], match("fantasy"))
check("description match 'arrakis' -> book 3", match("arrakis") == [3], match("arrakis"))
check("series match 'prisoner' -> book 1", match("prisoner") == [1], match("prisoner"))

# --- update_book / delete_book -----------------------------------------
meta = sqlite3.connect(os.path.join(calibre_dir, "metadata.db"))
meta.execute("UPDATE books SET title='Dune Messiah' WHERE id=3")
meta.commit(); meta.close()
fts.update_book(3)
check("update_book reindexes new title", match("messiah") == [3], match("messiah"))
fts.delete_book(2)
check("delete_book removes row", match("tolkien") == [], match("tolkien"))

conn.close()
print("\nRESULT", "OK" if rc == 0 else "FAILURES", flush=True)
os._exit(rc)
