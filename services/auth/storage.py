"""Робота з SQLite: користувачі та журнал входів."""

import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import closing

DB_PATH = os.getenv("AUTH_DB", "auth.db")
ITERATIONS = 200_000
ROLES = ("admin", "viewer")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(connect()) as conn, conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                username      TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'viewer',
                created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS login_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                username  TEXT NOT NULL,
                success   INTEGER NOT NULL,
                ip        TEXT,
                logged_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        has_users = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()

    # при першому запуску створюємо адміністратора
    if not has_users:
        save_user("admin", os.getenv("AUTH_ADMIN_PASSWORD", "admin123"), "admin")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, expected = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(digest.hex(), expected)


def save_user(username: str, password: str, role: str = "viewer"):
    if role not in ROLES:
        raise ValueError(f"невідома роль: {role}")
    with closing(connect()) as conn, conn:
        conn.execute(
            """INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)
               ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash,
                                                   role = excluded.role""",
            (username, hash_password(password), role),
        )


def delete_user(username: str) -> bool:
    with closing(connect()) as conn, conn:
        return conn.execute("DELETE FROM users WHERE username = ?", (username,)).rowcount > 0


def list_users() -> list[sqlite3.Row]:
    with closing(connect()) as conn:
        return conn.execute("SELECT username, role, created_at FROM users ORDER BY username").fetchall()


def authenticate(username: str, password: str, ip: str | None) -> sqlite3.Row | None:
    with closing(connect()) as conn, conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        ok = user is not None and check_password(password, user["password_hash"])
        conn.execute(
            "INSERT INTO login_log (username, success, ip) VALUES (?, ?, ?)",
            (username, int(ok), ip),
        )
    return user if ok else None


def user_exists(username: str) -> bool:
    with closing(connect()) as conn:
        return conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is not None
