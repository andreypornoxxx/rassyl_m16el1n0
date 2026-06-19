"""
БД рассыльщика. Хранит аккаунты и задачи каждого пользователя отдельно.
"""
import os, sqlite3

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    import psycopg2
    PH = "%s"
    def db_connect():
        return psycopg2.connect(DATABASE_URL)
else:
    PH = "?"
    def db_connect():
        con = sqlite3.connect("broadcast.db")
        con.row_factory = sqlite3.Row
        return con


def db_init():
    con = db_connect()
    cur = con.cursor()

    # подписки (копируем логику из шоп-бота или держим здесь)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id  BIGINT PRIMARY KEY,
            username TEXT,
            plan     TEXT,
            sub_end  TIMESTAMP
        )
    """)

    # аккаунты Telethon каждого пользователя
    cur.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id        SERIAL PRIMARY KEY,
            user_id   BIGINT NOT NULL,
            phone     TEXT   NOT NULL,
            label     TEXT,
            active    INTEGER DEFAULT 1,
            UNIQUE(user_id, phone)
        )
    """)

    # активный аккаунт пользователя
    cur.execute("""
        CREATE TABLE IF NOT EXISTS active_account (
            user_id BIGINT PRIMARY KEY,
            phone   TEXT
        )
    """)

    # история рассылок
    cur.execute("""
        CREATE TABLE IF NOT EXISTS broadcasts (
            id         SERIAL PRIMARY KEY,
            user_id    BIGINT,
            phone      TEXT,
            total      INTEGER,
            ok         INTEGER,
            fail       INTEGER,
            ts         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    con.commit()
    cur.close()
    con.close()


def fetchall(q, p=()):
    q = q.replace("?", PH)
    con = db_connect()
    cur = con.cursor()
    cur.execute(q, p)
    if DATABASE_URL:
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    else:
        rows = [dict(r) for r in cur.fetchall()]
    cur.close(); con.close()
    return rows

def fetchone(q, p=()):
    q = q.replace("?", PH)
    con = db_connect()
    cur = con.cursor()
    cur.execute(q, p)
    if DATABASE_URL:
        cols = [d[0] for d in cur.description]
        row  = cur.fetchone()
        result = dict(zip(cols, row)) if row else None
    else:
        row = cur.fetchone()
        result = dict(row) if row else None
    cur.close(); con.close()
    return result

def execute(q, p=()):
    q = q.replace("?", PH)
    con = db_connect()
    cur = con.cursor()
    cur.execute(q, p)
    con.commit()
    cur.close(); con.close()

def is_subscribed(user_id: int) -> bool:
    row = fetchone(
        "SELECT sub_end FROM subscriptions WHERE user_id=?", (user_id,)
    )
    if not row:
        return False
    from datetime import datetime
    return datetime.fromisoformat(str(row["sub_end"])) > datetime.now()

def get_accounts(user_id: int):
    return fetchall(
        "SELECT * FROM accounts WHERE user_id=? AND active=1", (user_id,)
    )

def add_account(user_id: int, phone: str, label: str):
    execute("""
        INSERT INTO accounts (user_id, phone, label)
        VALUES (?,?,?)
        ON CONFLICT(user_id, phone) DO UPDATE SET label=EXCLUDED.label, active=1
    """, (user_id, phone, label))

def remove_account(user_id: int, phone: str):
    execute("UPDATE accounts SET active=0 WHERE user_id=? AND phone=?", (user_id, phone))

def get_active_phone(user_id: int):
    row = fetchone("SELECT phone FROM active_account WHERE user_id=?", (user_id,))
    return row["phone"] if row else None

def set_active_phone(user_id: int, phone: str):
    execute("""
        INSERT INTO active_account (user_id, phone) VALUES (?,?)
        ON CONFLICT(user_id) DO UPDATE SET phone=EXCLUDED.phone
    """, (user_id, phone))

def log_broadcast(user_id, phone, total, ok, fail):
    execute(
        "INSERT INTO broadcasts (user_id,phone,total,ok,fail) VALUES(?,?,?,?,?)",
        (user_id, phone, total, ok, fail)
    )
