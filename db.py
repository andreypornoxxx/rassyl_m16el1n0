import os

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    import pg8000.native
    import urllib.parse
    import re

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

    def _to_pg(q):
        i = 0
        def repl(m):
            nonlocal i
            i += 1
            return f"${i}"
        return re.sub(r'\?|%s', repl, q)

    def fetchall(q, p=()):
        con = db_connect()
        rows = con.run(_to_pg(q), *list(p))
        cols = [c["name"] for c in con.columns]
        con.close()
        return [dict(zip(cols, r)) for r in rows]

    def fetchone(q, p=()):
        rows = fetchall(q, p)
        return rows[0] if rows else None

    def execute(q, p=()):
        con = db_connect()
        try:
            rows = con.run(_to_pg(q), *list(p))
            result = rows[0][0] if rows else None
        except Exception:
            result = None
        con.close()
        return result

else:
    import sqlite3

    def db_connect():
        con = sqlite3.connect("shop.db")
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
            """CREATE TABLE IF NOT EXISTS products (
                id          SERIAL PRIMARY KEY,
                name        TEXT    NOT NULL,
                description TEXT    NOT NULL,
                price_stars INTEGER NOT NULL,
                content     TEXT,
                file_id     TEXT,
                file_name   TEXT,
                active      INTEGER DEFAULT 1
            )""",
            """CREATE TABLE IF NOT EXISTS sales (
                id         SERIAL PRIMARY KEY,
                user_id    BIGINT,
                product_id INTEGER,
                stars      INTEGER,
                ts         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS subscriptions (
                user_id     BIGINT PRIMARY KEY,
                username    TEXT,
                plan        TEXT,
                sub_end     TIMESTAMP,
                activated   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
        ]:
            con.run(q)
        con.close()
    else:
        con = db_connect()
        con.execute("""CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, description TEXT NOT NULL, price_stars INTEGER NOT NULL,
            content TEXT, file_id TEXT, file_name TEXT, active INTEGER DEFAULT 1
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, product_id INTEGER, stars INTEGER,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER PRIMARY KEY, username TEXT, plan TEXT,
            sub_end DATETIME, activated DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        con.commit()
        con.close()
