import os
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    import pg8000.native
    import urllib.parse

    def db_connect():
        r = urllib.parse.urlparse(DATABASE_URL)
        return pg8000.native.Connection(
            host=r.hostname,
            port=r.port or 5432,
            database=r.path.lstrip("/"),
            user=r.username,
            password=r.password,
            ssl_context=True,
        )

    def fetchall(q, p=()):
        q = q.replace("?", "%s")
        con = db_connect()
        rows = con.run(q, *p)
        cols = [c["name"] for c in con.columns]
        con.close()
        return [dict(zip(cols, r)) for r in rows]

    def fetchone(q, p=()):
        rows = fetchall(q, p)
        return rows[0] if rows else None

    def execute(q, p=()):
        q = q.replace("?", "%s")
        con = db_connect()
        rows = con.run(q, *p)
        con.close()
        return rows[0][0] if rows else None

else:
    import sqlite3

    def db_connect():
        con = sqlite3.connect("broadcast.db")
        con.row_factory = sqlite3.Row
        return con

    def fetchall(q, p=()):
        con = db_connect()
        rows = [dict(r) for r in con.execute(q, p).fetchall()]
        con.close()
        return rows

    def fetchone(q, p=()):
        con = db_connect()
        row = con.execute(q, p).fetchone()
        con.close()
        return dict(row) if row else None

    def execute(q, p=()):
        con = db_connect()
        cur = con.execute(q, p)
        con.commit()
        lastrowid = cur.lastrowid
        con.close()
        return lastrowid


def db_init():
    if DATABASE_URL:
        con = db_connect()
        for q in [
            """CREATE TABLE IF NOT EXISTS subscriptions (
                user_id  BIGINT PRIMARY KEY,
                username TEXT,
                plan     TEXT,
                sub_end  TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS accounts (
                id        SERIAL PRIMARY KEY,
                user_id   BIGINT NOT NULL,
                phone     TEXT   NOT NULL,
                label     TEXT,
                active    INTEGER DEFAULT 1,
                UNIQUE(user_id, phone)
            )""",
            """CREATE TABLE IF NOT EXISTS active_account (
                user_id BIGINT PRIMARY KEY,
                phone   TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS broadcasts (
                id      SERIAL PRIMARY KEY,
                user_id BIGINT,
                phone   TEXT,
                total   INTEGER,
                ok      INTEGER,
                fail    INTEGER,
                ts      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
        ]:
            con.run(q)
        con.close()
    else:
        con = db_connect()
        con.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER PRIMARY KEY, username TEXT, plan TEXT, sub_end DATETIME
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, phone TEXT NOT NULL, label TEXT, active INTEGER DEFAULT 1,
            UNIQUE(user_id, phone)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS active_account (
            user_id INTEGER PRIMARY KEY, phone TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, phone TEXT, total INTEGER, ok INTEGER, fail INTEGER,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        con.commit()
        con.close()


def is_subscribed(user_id: int) -> bool:
    row = fetchone("SELECT sub_end FROM subscriptions WHERE user_id=?", (user_id,))
    if not row:
        return False
    return datetime.fromisoformat(str(row["sub_end"])) > datetime.now()

def get_accounts(user_id: int):
    return fetchall("SELECT * FROM accounts WHERE user_id=? AND active=1", (user_id,))

def add_account(user_id: int, phone: str, label: str):
    if DATABASE_URL:
        execute("""
            INSERT INTO accounts (user_id, phone, label)
            VALUES (?,?,?)
            ON CONFLICT(user_id, phone) DO UPDATE SET label=EXCLUDED.label, active=1
        """, (user_id, phone, label))
    else:
        execute("""
            INSERT OR REPLACE INTO accounts (user_id, phone, label, active)
            VALUES (?,?,?,1)
        """, (user_id, phone, label))

def remove_account(user_id: int, phone: str):
    execute("UPDATE accounts SET active=0 WHERE user_id=? AND phone=?", (user_id, phone))

def get_active_phone(user_id: int):
    row = fetchone("SELECT phone FROM active_account WHERE user_id=?", (user_id,))
    return row["phone"] if row else None

def set_active_phone(user_id: int, phone: str):
    if DATABASE_URL:
        execute("""
            INSERT INTO active_account (user_id, phone) VALUES (?,?)
            ON CONFLICT(user_id) DO UPDATE SET phone=EXCLUDED.phone
        """, (user_id, phone))
    else:
        execute("""
            INSERT OR REPLACE INTO active_account (user_id, phone) VALUES (?,?)
        """, (user_id, phone))

def log_broadcast(user_id, phone, total, ok, fail):
    execute(
        "INSERT INTO broadcasts (user_id,phone,total,ok,fail) VALUES(?,?,?,?,?)",
        (user_id, phone, total, ok, fail)
    )
