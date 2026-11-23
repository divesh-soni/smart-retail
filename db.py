import os
import re

import pymysql
from dotenv import load_dotenv

load_dotenv()

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
COLUMNS = ["sku", "name", "stock", "avg_daily_sales",
           "lead_time_days", "expiry_days", "reorder_level"]


def env(name, default=""):
    # strip so a stray space/newline pasted into a hosting dashboard doesn't break the login
    return os.getenv(name, default).strip()


def db_name():
    return env("DB_NAME", "smart_retail")


def ssl_options():
    # hosted mysql (e.g. Aiven) needs SSL; set DB_SSL=1, and DB_SSL_CA to verify the server cert
    if os.getenv("DB_SSL", "").lower() not in ("1", "true", "yes"):
        return None
    ca = os.getenv("DB_SSL_CA")
    return {"ca": ca} if ca else {"check_hostname": False}


def get_connection(use_db=True):
    return pymysql.connect(
        ssl=ssl_options(),
        host=env("DB_HOST", "localhost"),
        port=int(env("DB_PORT", "3306")),
        user=env("DB_USER", "root"),
        password=env("DB_PASSWORD"),
        database=db_name() if use_db else None,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        charset="utf8mb4",
    )


def query(sql, args=None):
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def execute(sql, args=None):
    with get_connection() as conn, conn.cursor() as cur:
        return cur.execute(sql, args)


def _fix(row):
    # DECIMAL comes back as Decimal, logic.py wants float
    row["avg_daily_sales"] = float(row["avg_daily_sales"])
    return row


def fetch_all_products():
    return [_fix(r) for r in query("SELECT * FROM products ORDER BY sku")]


def fetch_product(sku):
    rows = query("SELECT * FROM products WHERE sku = %s", (sku,))
    return _fix(rows[0]) if rows else None


def fetch_all_skus():
    return [r["sku"] for r in query("SELECT sku FROM products")]


def insert_product(p):
    execute("INSERT INTO products (sku, name, stock, avg_daily_sales, lead_time_days,"
            " expiry_days, reorder_level) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [p[c] for c in COLUMNS])


def update_product(sku, p):
    execute("UPDATE products SET name=%s, stock=%s, avg_daily_sales=%s, lead_time_days=%s,"
            " expiry_days=%s, reorder_level=%s WHERE sku=%s",
            [p[c] for c in COLUMNS[1:]] + [sku])


def delete_product(sku):
    return execute("DELETE FROM products WHERE sku = %s", (sku,)) > 0


def delete_all_products():
    execute("DELETE FROM products")


def _schema_statements():
    with open(SCHEMA_FILE, encoding="utf-8") as f:
        # strip -- comments first, a ; inside one would break the split
        sql = "\n".join(re.sub(r"--.*$", "", line) for line in f)
    sql = sql.replace("smart_retail", db_name())
    return [s.strip() for s in sql.split(";") if s.strip()]


def reset_products():
    seed = next(s for s in _schema_statements() if s.upper().startswith("INSERT INTO PRODUCTS"))
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM products")
        cur.execute(seed)


def ensure_database():
    # returns True only when it had to create the db
    with get_connection(use_db=False) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM information_schema.tables"
            " WHERE table_schema = %s AND table_name = 'products'", (db_name(),))
        if cur.fetchone()["n"] > 0:
            return False
        for stmt in _schema_statements():
            cur.execute(stmt)
    return True


def count_users():
    return query("SELECT COUNT(*) AS n FROM users")[0]["n"]


def get_user(username):
    rows = query("SELECT * FROM users WHERE username = %s", (username,))
    return rows[0] if rows else None


def create_user(username, password_hash, recovery_hash):
    execute("INSERT INTO users (username, password_hash, recovery_hash) VALUES (%s, %s, %s)",
            (username, password_hash, recovery_hash))


def update_password(username, password_hash, recovery_hash=None):
    if recovery_hash:
        execute("UPDATE users SET password_hash = %s, recovery_hash = %s WHERE username = %s",
                (password_hash, recovery_hash, username))
    else:
        execute("UPDATE users SET password_hash = %s WHERE username = %s",
                (password_hash, username))
