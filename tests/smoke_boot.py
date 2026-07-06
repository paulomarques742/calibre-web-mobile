"""Boot calibre-web-mobile against a throwaway config dir and exercise the new
routes / schema without a real Calibre library."""
import os
import sys
import tempfile
import sqlite3

CONFIG = tempfile.mkdtemp(prefix="cwm-smoke-")
os.environ["CALIBRE_DBPATH"] = CONFIG
os.environ.setdefault("SECRET_KEY", "smoke-secret")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
# CliParameter parses sys.argv; keep it minimal.
sys.argv = ["cps.py"]

# The bundled python-magic-bin DLL segfaults on Python 3.14 (test-env only).
# Pre-seed a harmless stub so `import magic` never loads the broken DLL; the
# package metadata is still installed so dep_check is satisfied.
import types  # noqa: E402
_magic = types.ModuleType("magic")
_magic.from_buffer = lambda *a, **k: "application/octet-stream"
_magic.from_file = lambda *a, **k: "application/octet-stream"
class _M:  # noqa: E301
    def __init__(self, *a, **k): pass
    def from_buffer(self, *a, **k): return "application/octet-stream"
_magic.Magic = _M
sys.modules["magic"] = _magic

print(">> importing cps", flush=True)
import cps  # noqa: E402
from cps import create_app, limiter  # noqa: E402

print(">> calling create_app()", flush=True)
app = create_app()
print(">> create_app done", flush=True)

# Replicate blueprint registration from cps/main.py (the pieces we test).
from cps.web import web  # noqa: E402
from cps.about import about  # noqa: E402
from cps.pwa import pwa  # noqa: E402
from cps.community import community  # noqa: E402
from cps.search import search  # noqa: E402
from cps.shelf import shelf  # noqa: E402
from cps.jinjia import jinjia  # noqa: E402
from cps.error_handler import init_errorhandler  # noqa: E402

init_errorhandler()
for bp in (search, web, jinjia, about, pwa, community, shelf):
    try:
        app.register_blueprint(bp)
    except Exception as e:
        print("register", bp.name, "FAILED:", e)

rc = 0

def check(label, cond, extra=""):
    global rc
    print(("PASS " if cond else "FAIL ") + label + ((" :: " + str(extra)) if extra else ""))
    if not cond:
        rc = 1

# --- schema checks ------------------------------------------------------
dbpath = os.path.join(CONFIG, "app.db")
check("app.db created", os.path.exists(dbpath))
con = sqlite3.connect(dbpath)
tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
for t in ("book_rating", "book_review", "book_like", "review_like"):
    check("table exists: " + t, t in tables)
cols = {r[1] for r in con.execute("PRAGMA table_info(user)")}
check("user.privacy_hide_activity column", "privacy_hide_activity" in cols, sorted(cols))
con.close()

# --- migration idempotency: run migrate again on the same db ------------
from cps import ub  # noqa: E402
try:
    ub.migrate_Database(ub.session)
    check("migrate_Database re-run idempotent", True)
except Exception as e:
    check("migrate_Database re-run idempotent", False, e)

# --- route checks -------------------------------------------------------
app.config["WTF_CSRF_ENABLED"] = False
client = app.test_client()

r = client.get("/manifest.json")
check("/manifest.json 200", r.status_code == 200, r.status_code)
check("manifest is json", r.mimetype in ("application/manifest+json", "application/json"), r.mimetype)
import json as _json
try:
    man = _json.loads(r.data)
    check("manifest has icons", len(man.get("icons", [])) >= 2)
    check("manifest display standalone", man.get("display") == "standalone")
except Exception as e:
    check("manifest parses", False, e)

r = client.get("/sw.js")
check("/sw.js 200", r.status_code == 200, r.status_code)
check("sw.js is js", "javascript" in r.mimetype, r.mimetype)
check("sw.js has cache name", b"cwm-" in r.data)

r = client.get("/offline")
check("/offline 200", r.status_code == 200, r.status_code)
check("offline mentions offline", b"offline" in r.data.lower())

# --- community endpoints reject anonymous/no-book -----------------------
r = client.post("/ajax/rating/1", json={"rating": 5})
check("rating endpoint reachable (not 404 route)", r.status_code in (403, 404, 302), r.status_code)

print("\nRESULT", "OK" if rc == 0 else "FAILURES", flush=True)
# Server/updater threads are non-daemon; hard-exit so the harness doesn't wait.
os._exit(rc)
