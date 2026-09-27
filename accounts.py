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
COOKIE_NAME = "wainet_session"
_DUMMY_HASH = hashlib.pbkdf2_hmac("sha256", b"invalid password", b"wainet-login-padding", PASSWORD_ITERATIONS)


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
            CREATE TABLE IF NOT EXISTS admin_invites (
                token_hash TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                used_at TEXT,
                created_at TEXT NOT NULL
            );
        """)
        user_columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        if "role" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('member','admin'))")
        if "password_change_required" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN password_change_required INTEGER NOT NULL DEFAULT 0 CHECK(password_change_required IN (0,1))")
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
            "created_at": row["created_at"], "email_verified": bool(row["email_verified_at"]), "role": row["role"],
            "password_change_required": bool(row["password_change_required"])}


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
    # A local operator may provision a temporary password that must be changed.
    # Normal registration, invitations and password changes still require 12 characters.
    valid_input = isinstance(password, str) and 1 <= len(password) <= 128
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
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"wainet-login-padding", PASSWORD_ITERATIONS)
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
    if {"role", "is_admin", "admin", "permissions", "privileges", "password_change_required"}.intersection(data):
        raise AccountError("Account permissions cannot be selected during registration.")
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


def create_temporary_admin(store, email, display_name, password):
    """Privileged local provisioning only; never called from a public route."""
    email = normalize_email(email)
    if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 50 or any(ord(c) < 32 for c in display_name):
        raise ValueError("Choose a display name of 1 to 50 characters.")
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        raise ValueError("A temporary password of 1 to 128 characters is required.")
    encoded = hash_password(password)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise ValueError("That account already exists. Temporary provisioning cannot replace its password or role.")
        user_id = str(uuid4())
        db.execute("""INSERT INTO users(id,email,display_name,password_hash,created_at,role,password_change_required)
            VALUES(?,?,?,?,?,'admin',1)""", (user_id, email, display_name.strip(), encoded, timestamp()))
        row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return public_user(row)


def bootstrap_temporary_admin(store, email, password_hash):
    """First deployment may use a private hash; never overwrite any existing user."""
    if not email and not password_hash:
        return None
    if not email or not password_hash:
        raise ValueError("Set both the administrator email and private password hash, or neither.")
    email = normalize_email(email)
    match = re.fullmatch(r"pbkdf2_sha256\$(\d+)\$([0-9a-f]{48})\$([0-9a-f]{64})", password_hash) if isinstance(password_hash, str) else None
    if not match or not PASSWORD_ITERATIONS <= int(match[1]) <= 5_000_000:
        raise ValueError("The private administrator password hash must use the supported salted PBKDF2 format.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM users WHERE role='admin' OR email=? LIMIT 1", (email,)).fetchone():
            return None
        user_id = str(uuid4())
        db.execute("""INSERT INTO users(id,email,display_name,password_hash,created_at,role,password_change_required)
            VALUES(?,?,'Administrator',?,?,'admin',1)""", (user_id, email, password_hash, timestamp()))
        row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return public_user(row)


def change_password(store, user_id, current_password, new_password):
    if not isinstance(new_password, str) or not 12 <= len(new_password) <= 128:
        raise AccountError("Use a new password between 12 and 128 characters.")
    if current_password == new_password:
        raise AccountError("Choose a different password from your current password.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row or not check_password(current_password, row["password_hash"]):
            raise AccountError("The current password is incorrect.", 401)
        encoded = hash_password(new_password)
        db.execute("UPDATE users SET password_hash=?,password_change_required=0 WHERE id=?", (encoded, user_id))
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        updated = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return public_user(updated)


def create_admin_invite(store, email, token=None, expires_in=24 * 60 * 60):
    """Local operator only. Callers must put the returned secret only in a private file."""
    email = normalize_email(email)
    token = token if token is not None else secrets.token_urlsafe(32)
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token):
        raise ValueError("Use a random URL-safe setup token with at least 43 characters.")
    if type(expires_in) is not int or not 60 <= expires_in <= 7 * 24 * 60 * 60:
        raise ValueError("The setup invitation must expire between one minute and seven days from now.")
    expires_at = int(time.time()) + expires_in
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise ValueError("That account already exists. Use the local grant command; setup cannot reset an existing password.")
        if db.execute("SELECT 1 FROM admin_invites WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone():
            raise ValueError("That setup token has already been configured. Generate a new token.")
        db.execute("INSERT INTO admin_invites(token_hash,email,expires_at,created_at) VALUES(?,?,?,?)",
                   (hashlib.sha256(token.encode()).hexdigest(), email, expires_at, timestamp()))
    return {"token": token, "email": email, "expires_at": expires_at}


def bootstrap_admin_invite(store, email, token):
    """First-deploy environment bootstrap; restarting never refreshes an invitation."""
    if not email and not token:
        return
    if not email or not token:
        raise ValueError("Set both the administrator email and setup token, or neither.")
    email = normalize_email(email)
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token):
        raise ValueError("The administrator setup token must be a random URL-safe value of at least 43 characters.")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
            return
        if db.execute("SELECT 1 FROM admin_invites WHERE token_hash=?", (digest,)).fetchone():
            return
        if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            return
        db.execute("INSERT INTO admin_invites(token_hash,email,expires_at,created_at) VALUES(?,?,?,?)",
                   (digest, email, int(time.time()) + 24 * 60 * 60, timestamp()))


def accept_admin_invite(store, data):
    """A secret and the intended email authorize one new administrator account."""
    if set(data) != {"token", "email", "display_name", "password"}:
        raise AccountError("Enter the setup token, invited email, display name and a new password.")
    token = data.get("token")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token):
        raise AccountError("This administrator invitation is invalid or expired.")
    email = normalize_email(data.get("email"))
    name, password = data.get("display_name"), data.get("password")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 50 or any(ord(c) < 32 for c in name):
        raise AccountError("Choose a display name of 1 to 50 characters.")
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise AccountError("Use a password between 12 and 128 characters.")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        invite = db.execute("SELECT * FROM admin_invites WHERE token_hash=? AND email=? AND used_at IS NULL AND expires_at>?",
                            (digest, email, int(time.time()))).fetchone()
        if not invite:
            raise AccountError("This administrator invitation is invalid or expired.")
        if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise AccountError("That account already exists. Ask the local operator to grant administrator access; its password has not changed.")
        user_id = str(uuid4())
        db.execute("INSERT INTO users(id,email,display_name,password_hash,created_at,role) VALUES(?,?,?,?,?,'admin')",
                   (user_id, email, name.strip(), hash_password(password), timestamp()))
        db.execute("UPDATE admin_invites SET used_at=? WHERE token_hash=?", (timestamp(), digest))
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return public_user(user)


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


def issue_session(store, user, secure=False):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with store.connect() as db:
        db.execute("DELETE FROM sessions WHERE expires_at<=?", (int(time.time()),))
        db.execute("INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at,created_at) VALUES (?,?,?,?,?)",
                   (hashlib.sha256(token.encode()).hexdigest(), user["id"], csrf, int(time.time()) + SESSION_SECONDS, timestamp()))
    return {"user": user, "csrfToken": csrf}, f"{COOKIE_NAME}={token}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")


def logout(store, cookie_header, secure=False):
    token = cookie_token(cookie_header)
    if token:
        with store.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    return f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")


def valid_csrf(auth_session, token):
    return bool(auth_session and isinstance(token, str) and hmac.compare_digest(auth_session["csrfToken"], token))
