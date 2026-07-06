"""Run calibre-web-mobile locally on Windows + Python 3.14 for click-testing.

Points the app at a THROWAWAY config dir (never your production app.db) and
neutralises the python-magic-bin DLL that segfaults on Python 3.14. Good for
testing browse / social / search / recommendations. NOTE: file *uploads* rely
on real libmagic and won't behave correctly under the stub — test uploads on
the homeserver (Linux/Docker) instead.

Usage:
    .venv-local\\Scripts\\python.exe tools\\run_local_win.py [CONFIG_DIR]

If CONFIG_DIR is omitted, uses .\\local-test-data (created if missing). Put a
COPY of your production metadata.db under a calibre library folder and point the
app at it from the web UI (Admin > Edit Calibre Database Configuration), or start
fresh. Default admin login on a fresh config: admin / admin123. Open
http://localhost:8083
"""
import os
import sys
import types

# 1) Stub the segfaulting `magic` module BEFORE importing cps.
_m = types.ModuleType("magic")
_m.from_buffer = lambda *a, **k: "application/octet-stream"
_m.from_file = lambda *a, **k: "application/octet-stream"
_m.Magic = type("Magic", (), {"__init__": lambda s, *a, **k: None,
                              "from_buffer": lambda s, *a, **k: "application/octet-stream"})
sys.modules["magic"] = _m

# 2) Isolate all app state in a throwaway config dir.
config_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.getcwd(), "local-test-data")
os.makedirs(config_dir, exist_ok=True)
os.environ["CALIBRE_DBPATH"] = config_dir
os.environ.setdefault("SECRET_KEY", "local-test-only")

# 3) Boot the real app (same entry point as `python cps.py`).
sys.argv = ["cps.py"]
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
print("calibre-web-mobile local test server")
print("  config dir : {}".format(config_dir))
print("  open       : http://localhost:8083")
print("  fresh login: admin / admin123")
from cps.main import main  # noqa: E402
main()
