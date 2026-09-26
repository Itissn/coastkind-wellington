"""Account-bound voucher inventory. Only authenticated routes may call mine/redeem.

The public catalog never includes voucher codes or user records. No retailer
integration, partnership, value, or points conversion is assumed: an operator
must configure a reward and import legitimately obtained voucher inventory.
"""
import hashlib
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4


PROPOSED_BRANDS = (
    ("new-world", "New World"),
    ("woolworths", "Woolworths"),
    ("farmers", "Farmers"),
    ("chemist-warehouse", "Chemist Warehouse"),
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def _reward_id(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", value)


def initialize_rewards(store):
    """Add inventory tables without changing existing observations or credits."""
    with store.connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS reward_catalog (
                id TEXT PRIMARY KEY,
                brand TEXT NOT NULL,
                title TEXT NOT NULL,
                points_cost INTEGER CHECK(points_cost IS NULL OR points_cost > 0),
                value_label TEXT,
                enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
                updated_at TEXT NOT NULL,
                CHECK(enabled = 0 OR (points_cost IS NOT NULL AND value_label IS NOT NULL))
            );
            CREATE TABLE IF NOT EXISTS reward_vouchers (
                id TEXT PRIMARY KEY,
                reward_id TEXT NOT NULL REFERENCES reward_catalog(id),
                code TEXT NOT NULL CHECK(length(code) > 0),
                code_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT,
                imported_at TEXT NOT NULL,
                imported_by TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS reward_vouchers_reward ON reward_vouchers(reward_id);
            CREATE TABLE IF NOT EXISTS reward_redemptions (
                id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL REFERENCES users(id),
                reward_id TEXT NOT NULL REFERENCES reward_catalog(id),
                voucher_id TEXT NOT NULL UNIQUE REFERENCES reward_vouchers(id),
                points_cost INTEGER NOT NULL CHECK(points_cost > 0),
                brand TEXT NOT NULL,
                title TEXT NOT NULL,
                value_label TEXT NOT NULL,
                redeemed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS reward_redemptions_user ON reward_redemptions(user_id, redeemed_at);
            CREATE TABLE IF NOT EXISTS reward_requests (
                id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL REFERENCES users(id),
                reward_id TEXT NOT NULL REFERENCES reward_catalog(id),
                brand TEXT NOT NULL,
                title TEXT NOT NULL,
                points_cost INTEGER NOT NULL CHECK(points_cost > 0),
                value_label TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','rejected','fulfilled')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by TEXT REFERENCES users(id),
                review_note TEXT NOT NULL DEFAULT '',
                redemption_id TEXT UNIQUE REFERENCES reward_redemptions(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS reward_requests_pending_user_reward
                ON reward_requests(user_id,reward_id) WHERE status='pending';
            CREATE INDEX IF NOT EXISTS reward_requests_user ON reward_requests(user_id,created_at);
            CREATE TABLE IF NOT EXISTS reward_request_keys (
                key TEXT PRIMARY KEY,
                reward_request_id TEXT NOT NULL REFERENCES reward_requests(id),
                user_id TEXT NOT NULL REFERENCES users(id),
                reward_id TEXT NOT NULL REFERENCES reward_catalog(id)
            );
            CREATE TABLE IF NOT EXISTS reward_request_events (
                id INTEGER PRIMARY KEY,
                reward_request_id TEXT NOT NULL REFERENCES reward_requests(id),
                action TEXT NOT NULL CHECK(action IN ('requested','approved','rejected')),
                actor_user_id TEXT NOT NULL REFERENCES users(id),
                note TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        for reward_id, brand in PROPOSED_BRANDS:
            db.execute("""INSERT OR IGNORE INTO reward_catalog
                (id,brand,title,enabled,updated_at) VALUES (?,?,?,0,?)""",
                       (reward_id, brand, f"{brand} voucher", _now()))


def _balance(db, user_id):
    earned = db.execute("SELECT COALESCE(SUM(delta),0) FROM points_ledger WHERE user_id=?", (user_id,)).fetchone()[0]
    spent = db.execute("SELECT COALESCE(SUM(points_cost),0) FROM reward_redemptions WHERE user_id=?", (user_id,)).fetchone()[0]
    # Keep negative corrections visible if an already-spent contribution is revoked.
    return earned - spent


def points_balance(store, user_id):
    with store.connect() as db:
        # A snapshot keeps an award or redemption from changing between both sums.
        db.execute("BEGIN")
        return _balance(db, user_id)


def catalog(store):
    """Public list: no codes, imported-by names, stock identifiers, or accounts."""
    with store.connect() as db:
        rows = db.execute("""SELECT r.id,r.brand,r.title,r.points_cost,r.value_label,r.enabled,
            EXISTS(SELECT 1 FROM reward_vouchers v WHERE v.reward_id=r.id
                AND (v.expires_at IS NULL OR v.expires_at > ?)
                AND NOT EXISTS(SELECT 1 FROM reward_redemptions d WHERE d.voucher_id=v.id)) AS has_stock
            FROM reward_catalog r ORDER BY r.rowid""", (_now(),)).fetchall()
        return [{"id": r["id"], "brand": r["brand"], "title": r["title"],
                 "points_cost": r["points_cost"], "value_label": r["value_label"],
                 "status": "unavailable" if not r["enabled"] else "available" if r["has_stock"] else "out_of_stock",
                 "available": bool(r["enabled"] and r["has_stock"])} for r in rows]


_OWN_REDEMPTIONS = """SELECT d.id,d.reward_id,d.brand,d.title,d.points_cost,d.value_label,
    v.code AS voucher_code,v.expires_at,d.redeemed_at,d.request_id
    FROM reward_redemptions d JOIN reward_vouchers v ON v.id=d.voucher_id"""


def mine(store, user_id):
    """Only this account's requests and issued vouchers; pending requests reveal no code."""
    with store.connect() as db:
        results = [_request_view(db, row) for row in db.execute(
            "SELECT * FROM reward_requests WHERE user_id=?", (user_id,))]
        results.extend(_legacy_view(row) for row in db.execute(_OWN_REDEMPTIONS + """ WHERE d.user_id=?
            AND NOT EXISTS(SELECT 1 FROM reward_requests r WHERE r.redemption_id=d.id)""", (user_id,)))
        return sorted(results, key=lambda item: (item['requested_at'], item['id']), reverse=True)


def _legacy_view(row):
    return {**dict(row), 'status': 'fulfilled', 'requested_at': row['redeemed_at'],
            'reviewed_at': None, 'review_note': 'Issued before the approval workflow.', 'redemption_id': row['id']}


def _request_view(db, row, include_code=True):
    value = {key: row[key] for key in ('id', 'request_id', 'reward_id', 'brand', 'title', 'points_cost', 'value_label',
                                      'status', 'reviewed_at', 'review_note', 'redemption_id')}
    value.update(requested_at=row['created_at'], voucher_code=None, expires_at=None, redeemed_at=None)
    if row['status'] == 'fulfilled' and row['redemption_id']:
        issued = db.execute(_OWN_REDEMPTIONS + ' WHERE d.id=? AND d.user_id=?',
                            (row['redemption_id'], row['user_id'])).fetchone()
        if issued:
            value.update(voucher_code=issued['voucher_code'] if include_code else None,
                         expires_at=issued['expires_at'], redeemed_at=issued['redeemed_at'])
    return value


def _account(db, user_id):
    row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    if not row:
        raise ValueError('Sign in to continue.')
    if 'password_change_required' in row.keys() and row['password_change_required']:
        raise ValueError('Change your password before continuing.')
    return row


def _require_admin(db, user_id):
    row = _account(db, user_id)
    if 'role' not in row.keys() or row['role'] != 'admin':
        raise ValueError('Administrator access is required.')
    return row


def _available_voucher(db, reward_id, current):
    return db.execute("""SELECT v.id FROM reward_vouchers v WHERE v.reward_id=?
        AND (v.expires_at IS NULL OR v.expires_at > ?)
        AND NOT EXISTS(SELECT 1 FROM reward_redemptions d WHERE d.voucher_id=v.id)
        ORDER BY v.expires_at IS NULL,v.expires_at,v.imported_at,v.id LIMIT 1""", (reward_id, current)).fetchone()


def redeem(store, user_id, reward_id, request_id):
    """Request approval. Points and voucher stock are unchanged until an admin approves."""
    if not _uuid(request_id):
        raise ValueError("A valid redemption request ID is required.")
    if not _reward_id(reward_id):
        raise ValueError("Choose a valid reward.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _account(db, user_id)
        key = db.execute('SELECT * FROM reward_request_keys WHERE key=?', (request_id,)).fetchone()
        if key:
            if key['user_id'] != user_id or key['reward_id'] != reward_id:
                raise ValueError('This redemption request ID has already been used.')
            return _request_view(db, db.execute('SELECT * FROM reward_requests WHERE id=?', (key['reward_request_id'],)).fetchone())
        previous = db.execute("SELECT user_id,reward_id FROM reward_redemptions WHERE request_id=?", (request_id,)).fetchone()
        if previous:
            if previous["user_id"] != user_id or previous["reward_id"] != reward_id:
                raise ValueError("This redemption request ID has already been used.")
            return _legacy_view(db.execute(_OWN_REDEMPTIONS + " WHERE d.user_id=? AND d.request_id=?", (user_id, request_id)).fetchone())
        pending = db.execute("SELECT * FROM reward_requests WHERE user_id=? AND reward_id=? AND status='pending'",
                             (user_id, reward_id)).fetchone()
        if pending:
            db.execute('INSERT INTO reward_request_keys(key,reward_request_id,user_id,reward_id) VALUES (?,?,?,?)',
                       (request_id, pending['id'], user_id, reward_id))
            return _request_view(db, pending)
        reward = db.execute("SELECT * FROM reward_catalog WHERE id=?", (reward_id,)).fetchone()
        if not reward or not reward["enabled"]:
            raise ValueError("This reward is not available yet.")
        current = _now()
        voucher = _available_voucher(db, reward_id, current)
        if not voucher:
            raise ValueError("This reward is out of stock. Your points have not been spent.")
        if _balance(db, user_id) < reward["points_cost"]:
            raise ValueError("You do not have enough points for this reward.")
        identifier = str(uuid4())
        db.execute("""INSERT INTO reward_requests(id,request_id,user_id,reward_id,brand,title,points_cost,value_label,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", (identifier, request_id, user_id, reward_id, reward['brand'], reward['title'],
                                               reward['points_cost'], reward['value_label'], current, current))
        db.execute('INSERT INTO reward_request_keys(key,reward_request_id,user_id,reward_id) VALUES (?,?,?,?)',
                   (request_id, identifier, user_id, reward_id))
        db.execute("""INSERT INTO reward_request_events(reward_request_id,action,actor_user_id,note,created_at)
            VALUES (?,'requested',?,?,?)""", (identifier, user_id, 'Voucher requested. No points have been spent.', current))
        return _request_view(db, db.execute('SELECT * FROM reward_requests WHERE id=?', (identifier,)).fetchone())


def review_request(store, identifier, admin_user_id, decision, note):
    """Approve and allocate in one transaction, or reject without charging points."""
    if not _uuid(identifier):
        raise ValueError('Choose a valid voucher request.')
    if decision not in ('approved', 'rejected'):
        raise ValueError('Choose approved or rejected.')
    if not isinstance(note, str) or not 1 <= len(note.strip()) <= 2000:
        raise ValueError('Add a review note of up to 2,000 characters.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        _require_admin(db, admin_user_id)
        row = db.execute('SELECT * FROM reward_requests WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise ValueError('Voucher request not found.')
        if row['status'] == 'fulfilled':
            if decision == 'approved':
                return _request_view(db, row)
            raise ValueError('An issued voucher cannot be rejected.')
        if row['status'] == 'rejected':
            if decision == 'rejected':
                return _request_view(db, row)
            raise ValueError('This request was rejected. The member can submit a new request.')
        current = _now()
        redemption_id = None
        if decision == 'approved':
            reward = db.execute('SELECT * FROM reward_catalog WHERE id=?', (row['reward_id'],)).fetchone()
            if not reward or not reward['enabled']:
                raise ValueError('This reward is not available. The request remains pending.')
            if reward['brand'] != row['brand'] or reward['value_label'] != row['value_label']:
                raise ValueError('Reward details have changed. Reject this request and ask the member to choose again.')
            if _balance(db, row['user_id']) < row['points_cost']:
                raise ValueError('The member does not have enough points. The request remains pending.')
            voucher = _available_voucher(db, row['reward_id'], current)
            if not voucher:
                raise ValueError('This reward is out of stock. The request remains pending and no points were spent.')
            redemption_id = str(uuid4())
            db.execute("""INSERT INTO reward_redemptions(id,request_id,user_id,reward_id,voucher_id,
                points_cost,brand,title,value_label,redeemed_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (redemption_id, row['request_id'], row['user_id'], row['reward_id'], voucher['id'], row['points_cost'],
                 row['brand'], row['title'], row['value_label'], current))
        db.execute("""UPDATE reward_requests SET status=?,updated_at=?,reviewed_at=?,reviewed_by=?,review_note=?,redemption_id=?
            WHERE id=?""", ('fulfilled' if decision == 'approved' else 'rejected', current, current, admin_user_id,
                            note.strip(), redemption_id, identifier))
        db.execute('''INSERT INTO reward_request_events(reward_request_id,action,actor_user_id,note,created_at)
            VALUES (?,?,?,?,?)''', (identifier, decision, admin_user_id, note.strip(), current))
        return _request_view(db, db.execute('SELECT * FROM reward_requests WHERE id=?', (identifier,)).fetchone())


def admin_requests(store):
    """Administrator queue; inventory codes are deliberately omitted from this overview."""
    with store.connect() as db:
        items = []
        for row in db.execute('SELECT * FROM reward_requests ORDER BY created_at DESC,id'):
            user = db.execute('SELECT * FROM users WHERE id=?', (row['user_id'],)).fetchone()
            item = _request_view(db, row, include_code=False)
            item.update(user_id=row['user_id'], display_name=user['display_name'] if 'display_name' in user.keys() else 'Member',
                        email=user['email'] if 'email' in user.keys() else None,
                        current_points=_balance(db, row['user_id']), reviewed_by=row['reviewed_by'],
                        audit=[dict(event) for event in db.execute('''SELECT action,actor_user_id,note,created_at
                            FROM reward_request_events WHERE reward_request_id=? ORDER BY id''', (row['id'],))])
            items.append(item)
        return items


def configure_reward(store, reward_id, brand, title, points_cost, value_label, enabled=False):
    """Operator-only; explicit values prevent fictitious conversion promises."""
    if not _reward_id(reward_id):
        raise ValueError("Use a reward ID with lowercase letters, numbers and hyphens.")
    for value, name, limit in [(brand, "Brand", 100), (title, "Title", 160), (value_label, "Voucher value", 100)]:
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"{name} is required and must be at most {limit} characters.")
    if type(points_cost) is not int or not 0 < points_cost <= 1000000000:
        raise ValueError("Points cost must be a positive whole number below 1,000,000,001.")
    if type(enabled) is not bool:
        raise ValueError("Invalid reward availability.")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT brand,value_label FROM reward_catalog WHERE id=?", (reward_id,)).fetchone()
        if existing and (existing["brand"] != brand.strip() or existing["value_label"] != value_label.strip()):
            if db.execute("SELECT 1 FROM reward_vouchers WHERE reward_id=? LIMIT 1", (reward_id,)).fetchone():
                raise ValueError("Create a new reward ID to change the brand or value of imported vouchers.")
        db.execute("""INSERT INTO reward_catalog(id,brand,title,points_cost,value_label,enabled,updated_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            brand=excluded.brand,title=excluded.title,points_cost=excluded.points_cost,
            value_label=excluded.value_label,enabled=excluded.enabled,updated_at=excluded.updated_at""",
            (reward_id, brand.strip(), title.strip(), points_cost, value_label.strip(), int(enabled), _now()))


def import_inventory(store, reward_id, vouchers, operator):
    """Operator-only atomic import. Return counts; never print or return codes.

    vouchers: [{"code": "legitimately obtained code", "expires_at": ISO timestamp
    with timezone or explicit null for a voucher with no expiry}].
    """
    if not isinstance(operator, str) or not operator.strip() or len(operator) > 100:
        raise ValueError("An operator name of at most 100 characters is required.")
    if not isinstance(vouchers, list) or not 1 <= len(vouchers) <= 10000:
        raise ValueError("The inventory file must contain between 1 and 10,000 vouchers.")
    prepared = []
    current = _now()
    seen = set()
    for item in vouchers:
        if not isinstance(item, dict) or set(item) != {"code", "expires_at"}:
            raise ValueError("Each inventory entry must contain only code and expires_at.")
        code = item["code"]
        if not isinstance(code, str) or not code.strip() or len(code) > 2000 or any(ord(c) < 32 for c in code):
            raise ValueError("The inventory contains an invalid voucher code.")
        code = code.strip()
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
        if digest in seen:
            raise ValueError("The inventory file contains duplicate voucher codes.")
        seen.add(digest)
        expiry = item["expires_at"]
        if expiry is not None:
            try:
                parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError()
                expiry = parsed.astimezone(timezone.utc).isoformat()
                if expiry <= current:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError, OverflowError):
                raise ValueError("Voucher expiry must be a future timestamp with timezone, or null for no expiry.") from None
        prepared.append((str(uuid4()), reward_id, code, digest, expiry, current, operator.strip()))
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM reward_catalog WHERE id=?", (reward_id,)).fetchone():
            raise ValueError("Configure this reward before importing inventory.")
        for item in prepared:
            if db.execute("SELECT 1 FROM reward_vouchers WHERE code_hash=?", (item[3],)).fetchone():
                raise ValueError("A voucher in this file has already been imported. No inventory was added.")
        db.executemany("""INSERT INTO reward_vouchers(id,reward_id,code,code_hash,expires_at,imported_at,imported_by)
            VALUES(?,?,?,?,?,?,?)""", prepared)
    return {"imported": len(prepared), "reward_id": reward_id}
