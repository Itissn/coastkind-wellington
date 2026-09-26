"""Local account credentials and revocable, server-side browser sessions."""
import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie, CookieError
from uuid import uuid4

PASSWORD_ITERATIONS = 600_000
SESSION_SECONDS = 14 * 24 * 60 * 60
COOKIE_NAME = "coastkind_session"
_DUMMY_HASH = hashlib.pbkdf2_hmac("sha256", b"invalid password", b"coastkind-login-padding", PASSWORD_ITERATIONS)


class AccountError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def initialize_accounts(store):
    """Keep pre-account observations and points intact and unclaimed."""
    with store.connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                email_verified_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
                csrf_token TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
        """)
        for table in ("observations", "comments", "support", "points_ledger"):
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if "user_id" not in columns:
                db.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT REFERENCES users(id)")
        columns = {row[1] for row in db.execute("PRAGMA table_info(observations)")}
        if "rewards_waived" not in columns:
            db.execute("ALTER TABLE observations ADD COLUMN rewards_waived INTEGER NOT NULL DEFAULT 1 CHECK(rewards_waived IN (0,1))")
        db.execute("CREATE INDEX IF NOT EXISTS observations_user ON observations(user_id)")
        db.execute("CREATE INDEX IF NOT EXISTS points_ledger_user ON points_ledger(user_id)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS support_user ON support(observation_id,user_id)")


def public_user(row):
    return {"id": row["id"], "email": row["email"], "display_name": row["display_name"],
            "created_at": row["created_at"], "email_verified": bool(row["email_verified_at"])}


def normalize_email(value):
    if not isinstance(value, str) or not 3 <= len(value.strip()) <= 254:
        raise AccountError("Enter a valid email address.")
    email = value.strip().casefold()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or any(ord(c) < 32 for c in email):
        raise AccountError("Enter a valid email address.")
    return email


def hash_password(password):
    salt = secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def check_password(password, encoded):
    valid_input = isinstance(password, str) and 12 <= len(password) <= 128
    if not valid_input:
        password = "invalid password"
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(actual, bytes.fromhex(expected)) and valid_input
    except (AttributeError, TypeError, ValueError):
        # Missing accounts consume the same password work as incorrect passwords.
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"coastkind-login-padding", PASSWORD_ITERATIONS)
        hmac.compare_digest(actual, _DUMMY_HASH)
        return False


class AuthThrottle:
    """Bound per-process attempts; never persist passwords or session tokens."""
    def __init__(self):
        self.attempts = {}
        self.lock = threading.Lock()

    def check(self, address, email):
        current = time.monotonic()
        with self.lock:
            for key in list(self.attempts):
                self.attempts[key] = [value for value in self.attempts[key] if current - value < 600]
                if not self.attempts[key]:
                    del self.attempts[key]
            keys = (("address", address, 30), ("email", email, 10))
            if any(len(self.attempts.get((kind, value), [])) >= limit for kind, value, limit in keys):
                raise AccountError("Too many account attempts. Please try again in 10 minutes.", 429)
            for kind, value, _ in keys:
                self.attempts.setdefault((kind, value), []).append(current)


def register(store, data):
    email = normalize_email(data.get("email"))
    display_name, password = data.get("display_name"), data.get("password")
    if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 50 or any(ord(c) < 32 for c in display_name):
        raise AccountError("Choose a display name of 1 to 50 characters.")
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise AccountError("Use a password between 12 and 128 characters.")
    encoded = hash_password(password)
    user_id = str(uuid4())
    try:
        with store.connect() as db:
            db.execute("INSERT INTO users(id,email,display_name,password_hash,created_at) VALUES (?,?,?,?,?)",
                       (user_id, email, display_name.strip(), encoded, timestamp()))
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    except sqlite3.IntegrityError:
        raise AccountError("Unable to create an account with these details. Try signing in or use a different email.")
    return public_user(row)


def login(store, data):
    try:
        email = normalize_email(data.get("email"))
    except AccountError:
        email = ""
    password = data.get("password")
    with store.connect() as db:
        row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not check_password(password, row["password_hash"] if row else None):
        raise AccountError("The email or password is incorrect.", 401)
    return public_user(row)


def cookie_token(header):
    try:
        cookie = SimpleCookie()
        cookie.load(header or "")
        value = cookie.get(COOKIE_NAME)
        token = value.value if value else ""
        return token if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) else None
    except (CookieError, TypeError):
        return None


def session(store, cookie_header):
    token = cookie_token(cookie_header)
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with store.connect() as db:
        row = db.execute("""SELECT users.*,sessions.csrf_token FROM sessions
            JOIN users ON users.id=sessions.user_id WHERE token_hash=? AND expires_at>?""",
                         (token_hash, int(time.time()))).fetchone()
    return {"user": public_user(row), "csrfToken": row["csrf_token"]} if row else None


def issue_session(store, user):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with store.connect() as db:
        db.execute("DELETE FROM sessions WHERE expires_at<=?", (int(time.time()),))
        db.execute("INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at,created_at) VALUES (?,?,?,?,?)",
                   (hashlib.sha256(token.encode()).hexdigest(), user["id"], csrf, int(time.time()) + SESSION_SECONDS, timestamp()))
    return {"user": user, "csrfToken": csrf}, f"{COOKIE_NAME}={token}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Lax"


def logout(store, cookie_header):
    token = cookie_token(cookie_header)
    if token:
        with store.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    return f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"


def valid_csrf(auth_session, token):
    return bool(auth_session and isinstance(token, str) and hmac.compare_digest(auth_session["csrfToken"], token))
