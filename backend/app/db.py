import json, os, sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv('ALFA_DB', ROOT/'data/local/alfa.sqlite3'))
def uploads_dir(): return Path(os.getenv('ALFA_UPLOAD_DIR') or DB_PATH.parent/'uploads')
def now(): return datetime.now(timezone.utc).isoformat()
def dumps(value): return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
def unpack(row): return json.loads(row['payload']) if row else None
@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn=sqlite3.connect(DB_PATH,timeout=20)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback(); raise
    finally: conn.close()
def migrate():
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        for p in sorted((ROOT/'backend/migrations').glob('*.sql')): c.executescript(p.read_text())
def setting():
    with db() as c:
        return unpack(c.execute("SELECT payload FROM settings WHERE id='global'").fetchone()) or {'daily_budget':500,'run_budget':25,'mode':'fixture','auto_refresh':False}
