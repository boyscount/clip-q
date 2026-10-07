"""SQLite storage for ClipQueue.

Written to port to Postgres later: no SQLite-only types, every id is TEXT,
timestamps are ISO-8601 UTC strings, and JSON columns hold text. The only
thing to swap is the connection factory and the `?` placeholders.

WAL is on so the render worker can write progress while the API reads.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("CLIPQUEUE_DB", ROOT / "data" / "clipqueue.db"))

STATUSES = ("queued", "rendering", "ready", "posted", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id          TEXT PRIMARY KEY,
  email       TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  token_hash  TEXT NOT NULL UNIQUE,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  handle      TEXT NOT NULL,
  sub_id      TEXT NOT NULL,
  daily_cap   INTEGER NOT NULL DEFAULT 0,
  hue         INTEGER NOT NULL DEFAULT 20,
  created_at  TEXT NOT NULL,
  UNIQUE (user_id, handle)
);

CREATE TABLE IF NOT EXISTS products (
  id          TEXT NOT NULL,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  price       INTEGER NOT NULL,
  was         INTEGER NOT NULL DEFAULT 0,
  com         INTEGER NOT NULL DEFAULT 0,
  sold        INTEGER NOT NULL DEFAULT 0,
  hue         INTEGER NOT NULL DEFAULT 200,
  free        INTEGER NOT NULL DEFAULT 0,
  shots       INTEGER NOT NULL DEFAULT 4,
  bullets     TEXT NOT NULL DEFAULT '[]',
  link        TEXT NOT NULL DEFAULT '',
  shop        TEXT NOT NULL DEFAULT '',
  has_photos  INTEGER NOT NULL DEFAULT 0,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (user_id, id)
);

CREATE TABLE IF NOT EXISTS jobs (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  batch_id      TEXT NOT NULL,
  product_id    TEXT NOT NULL,
  account_id    TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  sub_id        TEXT NOT NULL DEFAULT '',
  format        TEXT NOT NULL,
  aspect        TEXT NOT NULL,
  persona       TEXT NOT NULL,
  voice         TEXT NOT NULL,
  cart          INTEGER NOT NULL DEFAULT 1,
  status        TEXT NOT NULL DEFAULT 'queued',
  progress      INTEGER NOT NULL DEFAULT 0,
  attempts      INTEGER NOT NULL DEFAULT 0,
  scheduled_at  TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  started_at    TEXT,
  finished_at   TEXT,
  script        TEXT NOT NULL DEFAULT '[]',
  caption       TEXT NOT NULL DEFAULT '',
  link          TEXT NOT NULL DEFAULT '',
  duration      REAL NOT NULL DEFAULT 0,
  size_kb       INTEGER NOT NULL DEFAULT 0,
  video_path    TEXT NOT NULL DEFAULT '',
  error         TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS jobs_user_created ON jobs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS jobs_claim        ON jobs (status, scheduled_at);
CREATE INDEX IF NOT EXISTS jobs_batch        ON jobs (batch_id);

CREATE INDEX IF NOT EXISTS jobs_sub ON jobs (sub_id);

-- One row per order item the affiliate report attributes to one of our clips.
-- Money is stored in satang (integers): the report hands back decimal strings
-- and float baht would drift once the totals get large.
CREATE TABLE IF NOT EXISTS conversions (
  id             TEXT PRIMARY KEY,          -- Shopee's conversion/order-item id
  user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  job_id         TEXT,                      -- NULL when the sub-id is not ours
  sub_id         TEXT NOT NULL DEFAULT '',
  item_id        TEXT NOT NULL DEFAULT '',
  item_name      TEXT NOT NULL DEFAULT '',
  qty            INTEGER NOT NULL DEFAULT 1,
  amount_satang  INTEGER NOT NULL DEFAULT 0,
  commission_satang INTEGER NOT NULL DEFAULT 0,
  status         TEXT NOT NULL DEFAULT '',  -- pending / completed / cancelled
  purchase_time  TEXT NOT NULL DEFAULT '',
  synced_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS conv_user_time ON conversions (user_id, purchase_time DESC);
CREATE INDEX IF NOT EXISTS conv_job       ON conversions (job_id);

CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id   TEXT NOT NULL,
  job_id    TEXT,
  level     TEXT NOT NULL DEFAULT 'info',
  message   TEXT NOT NULL,
  at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS events_user_at ON events (user_id, id DESC);

-- one row per (batch idempotency key) so a retried POST cannot double-queue
CREATE TABLE IF NOT EXISTS batches (
  key        TEXT NOT NULL,
  user_id    TEXT NOT NULL,
  batch_id   TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (user_id, key)
);
"""

_local = threading.local()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    """One connection per thread — sqlite3 objects are not thread-safe."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        _local.conn = conn
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


@contextmanager
def tx():
    """BEGIN IMMEDIATE so two workers cannot claim the same job."""
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# Columns added after the first release. CREATE TABLE IF NOT EXISTS silently
# does nothing to a table that already exists, so a database made by an older
# version keeps its old shape and every query touching a new column fails.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("jobs", "sub_id", "TEXT NOT NULL DEFAULT ''"),
]


def add_missing_columns() -> list[str]:
    """Add columns an older database is missing.

    Must run BEFORE the schema script: that script indexes the new columns, and
    CREATE INDEX on a column the table does not have yet fails outright.
    """
    conn = connect()
    applied = []
    for table, column, ddl in MIGRATIONS:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue  # a fresh database — the schema script creates it complete
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            applied.append(f"{table}.{column}")
    return applied


def backfill_sub_ids() -> int:
    """Recover sub_id for jobs created before the column existed.

    It is read out of the affiliate link rather than rebuilt from the account,
    because the link is what actually went out — a reconstructed sub-id that
    disagrees with the posted link would attribute orders to the wrong clip.
    """
    conn = connect()
    rows = conn.execute(
        "SELECT id, link FROM jobs WHERE (sub_id IS NULL OR sub_id = '') AND link LIKE '%sub_id=%'"
    ).fetchall()
    updates = []
    for row in rows:
        _, _, tail = row["link"].partition("sub_id=")
        sub_id = tail.split("&")[0].strip()
        if sub_id:
            updates.append((sub_id, row["id"]))
    if updates:
        conn.executemany("UPDATE jobs SET sub_id = ? WHERE id = ?", updates)
    return len(updates)


def init() -> None:
    conn = connect()
    changed = add_missing_columns()   # before the script: it indexes those columns
    conn.executescript(SCHEMA)
    filled = backfill_sub_ids()       # after: the table is guaranteed to exist
    if changed:
        print("อัปเกรดฐานข้อมูล: เพิ่มคอลัมน์ " + ", ".join(changed))
    if filled:
        print(f"อัปเกรดฐานข้อมูล: เติม sub_id ย้อนหลัง {filled} งาน")


def reset_for_tests(path: Path) -> None:
    """Point the module at a scratch database. Tests only."""
    global DB_PATH
    close()
    DB_PATH = path
    init()


# ---------------------------------------------------------------- users

def hash_token(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_user(email: str, name: str, token: str | None = None) -> tuple[str, str]:
    """Returns (user_id, token). The token is shown once and only stored hashed."""
    token = token or secrets.token_urlsafe(24)
    user_id = "u_" + secrets.token_hex(8)
    connect().execute(
        "INSERT INTO users (id, email, name, token_hash, created_at) VALUES (?,?,?,?,?)",
        (user_id, email.strip().lower(), name.strip(), hash_token(token), now()),
    )
    return user_id, token


def user_by_token(token: str) -> sqlite3.Row | None:
    if not token:
        return None
    return connect().execute(
        "SELECT * FROM users WHERE token_hash = ?", (hash_token(token),)
    ).fetchone()


def user_by_email(email: str) -> sqlite3.Row | None:
    return connect().execute(
        "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
    ).fetchone()


# ------------------------------------------------------------- accounts

def upsert_account(user_id: str, handle: str, sub_id: str, daily_cap: int, hue: int = 20) -> str:
    existing = connect().execute(
        "SELECT id FROM accounts WHERE user_id = ? AND handle = ?", (user_id, handle)
    ).fetchone()
    if existing:
        connect().execute(
            "UPDATE accounts SET sub_id = ?, daily_cap = ?, hue = ? WHERE id = ?",
            (sub_id, daily_cap, hue, existing["id"]),
        )
        return existing["id"]

    account_id = "a_" + secrets.token_hex(6)
    connect().execute(
        "INSERT INTO accounts (id, user_id, handle, sub_id, daily_cap, hue, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (account_id, user_id, handle, sub_id, daily_cap, hue, now()),
    )
    return account_id


def accounts(user_id: str) -> list[sqlite3.Row]:
    return connect().execute(
        "SELECT * FROM accounts WHERE user_id = ? ORDER BY created_at", (user_id,)
    ).fetchall()


def set_cap(user_id: str, account_id: str, cap: int) -> bool:
    cur = connect().execute(
        "UPDATE accounts SET daily_cap = ? WHERE id = ? AND user_id = ?",
        (cap, account_id, user_id),
    )
    return cur.rowcount > 0


def posted_on(user_id: str, account_id: str, day: str) -> int:
    """How many clips that account has already marked posted on a given date."""
    row = connect().execute(
        "SELECT COUNT(*) AS n FROM jobs"
        " WHERE user_id = ? AND account_id = ? AND status = 'posted'"
        " AND substr(scheduled_at, 1, 10) = ?",
        (user_id, account_id, day),
    ).fetchone()
    return row["n"]


# ------------------------------------------------------------- products

def replace_products(user_id: str, products: list[dict]) -> int:
    with tx() as conn:
        conn.execute("DELETE FROM products WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT INTO products (id,user_id,name,price,was,com,sold,hue,free,shots,"
            "bullets,link,shop,has_photos,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(
                str(p["id"]), user_id, p["name"], int(p["price"]), int(p.get("was", 0)),
                int(p.get("com", 0)), int(p.get("sold", 0)), int(p.get("hue", 200)),
                1 if p.get("free") else 0, int(p.get("shots", 4)),
                json.dumps(p.get("bullets", []), ensure_ascii=False),
                p.get("link", ""), p.get("shop", ""),
                1 if p.get("hasPhotos") else 0, now(),
            ) for p in products],
        )
    return len(products)


def products(user_id: str) -> list[dict]:
    rows = connect().execute(
        "SELECT * FROM products WHERE user_id = ? ORDER BY com DESC, sold DESC", (user_id,)
    ).fetchall()
    return [product_dict(r) for r in rows]


def product_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "price": row["price"], "was": row["was"],
        "com": row["com"], "sold": row["sold"], "hue": row["hue"],
        "free": bool(row["free"]), "shots": row["shots"],
        "bullets": json.loads(row["bullets"]), "link": row["link"],
        "shop": row["shop"], "hasPhotos": bool(row["has_photos"]),
    }


def product(user_id: str, product_id: str) -> dict | None:
    row = connect().execute(
        "SELECT * FROM products WHERE user_id = ? AND id = ?", (user_id, product_id)
    ).fetchone()
    return product_dict(row) if row else None


# ----------------------------------------------------------------- jobs

def insert_jobs(rows: list[dict]) -> None:
    connect().executemany(
        "INSERT INTO jobs (id,user_id,batch_id,product_id,account_id,sub_id,format,aspect,"
        "persona,voice,cart,status,progress,scheduled_at,created_at,link) "
        "VALUES (:id,:user_id,:batch_id,:product_id,:account_id,:sub_id,:format,:aspect,"
        ":persona,:voice,:cart,'queued',0,:scheduled_at,:created_at,:link)",
        rows,
    )


def jobs(user_id: str, status: str | None = None, limit: int = 500) -> list[sqlite3.Row]:
    sql = "SELECT * FROM jobs WHERE user_id = ?"
    args: list = [user_id]
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(limit)
    return connect().execute(sql, args).fetchall()


def job(user_id: str, job_id: str) -> sqlite3.Row | None:
    return connect().execute(
        "SELECT * FROM jobs WHERE user_id = ? AND id = ?", (user_id, job_id)
    ).fetchone()


def claim_next() -> sqlite3.Row | None:
    """Atomically take the oldest queued job. Returns None when idle."""
    with tx() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued'"
            " ORDER BY scheduled_at, created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        cur = conn.execute(
            "UPDATE jobs SET status='rendering', progress=0, attempts=attempts+1,"
            " started_at=?, error='' WHERE id=? AND status='queued'",
            (now(), row["id"]),
        )
        if cur.rowcount == 0:
            return None  # another worker won the race
        return conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()


def set_progress(job_id: str, progress: int) -> None:
    connect().execute(
        "UPDATE jobs SET progress=? WHERE id=? AND status='rendering'",
        (max(0, min(100, int(progress))), job_id),
    )


def finish_job(job_id: str, *, script: list[str], caption: str, duration: float,
               size_kb: int, video_path: str) -> None:
    connect().execute(
        "UPDATE jobs SET status='ready', progress=100, finished_at=?, script=?,"
        " caption=?, duration=?, size_kb=?, video_path=?, error='' WHERE id=?",
        (now(), json.dumps(script, ensure_ascii=False), caption,
         round(duration, 2), size_kb, video_path, job_id),
    )


def fail_job(job_id: str, error: str) -> None:
    connect().execute(
        "UPDATE jobs SET status='failed', finished_at=?, error=? WHERE id=?",
        (now(), error[:500], job_id),
    )


def requeue(user_id: str, job_id: str) -> bool:
    cur = connect().execute(
        "UPDATE jobs SET status='queued', progress=0, error='', started_at=NULL,"
        " finished_at=NULL WHERE id=? AND user_id=? AND status IN ('failed','ready')",
        (job_id, user_id),
    )
    return cur.rowcount > 0


def mark_posted(user_id: str, job_id: str) -> bool:
    cur = connect().execute(
        "UPDATE jobs SET status='posted', finished_at=? WHERE id=? AND user_id=?"
        " AND status='ready'",
        (now(), job_id, user_id),
    )
    return cur.rowcount > 0


def reclaim_stale(max_seconds: int = 900) -> int:
    """A worker that died mid-render leaves a job 'rendering' forever."""
    cutoff = datetime.now(timezone.utc).timestamp() - max_seconds
    rows = connect().execute(
        "SELECT id, started_at, attempts FROM jobs WHERE status='rendering'"
    ).fetchall()
    stale = []
    for row in rows:
        try:
            started = datetime.fromisoformat(row["started_at"]).timestamp()
        except (TypeError, ValueError):
            started = 0
        if started < cutoff:
            stale.append(row["id"])
    for job_id in stale:
        connect().execute(
            "UPDATE jobs SET status='queued', progress=0,"
            " error='worker หายไประหว่างเรนเดอร์ ส่งกลับเข้าคิว' WHERE id=?",
            (job_id,),
        )
    return len(stale)


# ---------------------------------------------------------- conversions

def job_by_sub(user_id: str, sub_id: str) -> sqlite3.Row | None:
    return connect().execute(
        "SELECT * FROM jobs WHERE user_id = ? AND sub_id = ?", (user_id, sub_id)
    ).fetchone()


def upsert_conversions(rows: list[dict]) -> tuple[int, int]:
    """Insert or refresh report rows. Returns (new, updated).

    An order moves pending -> completed -> sometimes cancelled, so a sync has
    to overwrite what it already has rather than insert blindly.
    """
    if not rows:
        return 0, 0
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    known = {
        r["id"] for r in connect().execute(
            f"SELECT id FROM conversions WHERE id IN ({marks})", ids
        ).fetchall()
    }
    connect().executemany(
        "INSERT INTO conversions (id,user_id,job_id,sub_id,item_id,item_name,qty,"
        "amount_satang,commission_satang,status,purchase_time,synced_at)"
        " VALUES (:id,:user_id,:job_id,:sub_id,:item_id,:item_name,:qty,"
        ":amount_satang,:commission_satang,:status,:purchase_time,:synced_at)"
        " ON CONFLICT(id) DO UPDATE SET"
        "  job_id=excluded.job_id, sub_id=excluded.sub_id, qty=excluded.qty,"
        "  amount_satang=excluded.amount_satang,"
        "  commission_satang=excluded.commission_satang,"
        "  status=excluded.status, purchase_time=excluded.purchase_time,"
        "  synced_at=excluded.synced_at",
        rows,
    )
    new = len([r for r in rows if r["id"] not in known])
    return new, len(rows) - new


def job_stats(user_id: str) -> dict[str, dict]:
    """Per-job totals. Cancelled orders are counted separately, never netted
    into earnings — an earnings figure that quietly includes refunds lies."""
    rows = connect().execute(
        "SELECT job_id,"
        "  SUM(CASE WHEN status='cancelled' THEN 0 ELSE qty END) AS orders,"
        "  SUM(CASE WHEN status='cancelled' THEN 0 ELSE amount_satang END) AS sales,"
        "  SUM(CASE WHEN status='cancelled' THEN 0 ELSE commission_satang END) AS commission,"
        "  SUM(CASE WHEN status='cancelled' THEN qty ELSE 0 END) AS cancelled"
        " FROM conversions WHERE user_id = ? AND job_id IS NOT NULL GROUP BY job_id",
        (user_id,),
    ).fetchall()
    return {
        r["job_id"]: {
            "orders": r["orders"] or 0,
            "salesSatang": r["sales"] or 0,
            "commissionSatang": r["commission"] or 0,
            "cancelled": r["cancelled"] or 0,
        }
        for r in rows
    }


def conversion_totals(user_id: str) -> dict:
    row = connect().execute(
        "SELECT COUNT(*) AS rows,"
        "  SUM(CASE WHEN status='cancelled' THEN 0 ELSE qty END) AS orders,"
        "  SUM(CASE WHEN status='cancelled' THEN 0 ELSE commission_satang END) AS commission,"
        "  SUM(CASE WHEN job_id IS NULL THEN 1 ELSE 0 END) AS unmatched,"
        "  MAX(synced_at) AS last_sync"
        " FROM conversions WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return {
        "rows": row["rows"] or 0,
        "orders": row["orders"] or 0,
        "commissionSatang": row["commission"] or 0,
        "unmatched": row["unmatched"] or 0,
        "lastSync": row["last_sync"] or "",
    }


# --------------------------------------------------------------- events

def log(user_id: str, message: str, level: str = "info", job_id: str | None = None) -> None:
    connect().execute(
        "INSERT INTO events (user_id, job_id, level, message, at) VALUES (?,?,?,?,?)",
        (user_id, job_id, level, message[:500], now()),
    )


def events(user_id: str, limit: int = 120) -> list[sqlite3.Row]:
    return connect().execute(
        "SELECT * FROM events WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
    ).fetchall()


def clear_events(user_id: str) -> None:
    connect().execute("DELETE FROM events WHERE user_id = ?", (user_id,))


# -------------------------------------------------------------- batches

def remember_batch(user_id: str, key: str, batch_id: str) -> str | None:
    """Returns the existing batch_id when this key was already used."""
    row = connect().execute(
        "SELECT batch_id FROM batches WHERE user_id = ? AND key = ?", (user_id, key)
    ).fetchone()
    if row:
        return row["batch_id"]
    connect().execute(
        "INSERT INTO batches (key, user_id, batch_id, created_at) VALUES (?,?,?,?)",
        (key, user_id, batch_id, now()),
    )
    return None
