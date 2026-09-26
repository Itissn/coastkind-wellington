"""Member feedback and administrator operations, called through authenticated routes."""
from datetime import datetime, timezone
from uuid import uuid4

import rewards

FEEDBACK_CATEGORIES = {'general', 'bug', 'suggestion', 'data_quality', 'reward'}
FEEDBACK_STATUSES = {'pending', 'in_progress', 'resolved', 'dismissed'}


def _now():
    return datetime.now(timezone.utc).isoformat()


def initialize(store):
    with store.connect() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
                category TEXT NOT NULL CHECK(category IN ('general','bug','suggestion','data_quality','reward')),
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','in_progress','resolved','dismissed')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                admin_note TEXT NOT NULL DEFAULT '',
                updated_by TEXT REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS feedback_user ON feedback(user_id,created_at);
            CREATE TABLE IF NOT EXISTS feedback_events (
                id INTEGER PRIMARY KEY,
                feedback_id TEXT NOT NULL REFERENCES feedback(id),
                actor_user_id TEXT NOT NULL REFERENCES users(id),
                previous_status TEXT,
                status TEXT NOT NULL CHECK(status IN ('pending','in_progress','resolved','dismissed')),
                note TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        ''')


def _data(value, allowed):
    if not isinstance(value, dict) or set(value).difference(allowed):
        raise ValueError('The request contains unsupported fields.')
    return value


def vouchers(store):
    """Server route must enforce administrator identity before calling this read."""
    return {'requests': rewards.admin_requests(store)}


def review_voucher(store, identifier, admin_user_id, data):
    data = _data(data, {'decision', 'note'})
    return {'request': rewards.review_request(store, identifier, admin_user_id, data.get('decision'), data.get('note'))}


def _feedback_view(db, row, admin=False):
    result = {key: row[key] for key in ('id', 'category', 'body', 'status', 'created_at', 'updated_at', 'admin_note')}
    if admin:
        user = db.execute('SELECT * FROM users WHERE id=?', (row['user_id'],)).fetchone()
        result.update(user_id=row['user_id'], display_name=user['display_name'] if 'display_name' in user.keys() else 'Member',
                      email=user['email'] if 'email' in user.keys() else None, updated_by=row['updated_by'],
                      audit=[dict(event) for event in db.execute('''SELECT actor_user_id,previous_status,status,note,created_at
                          FROM feedback_events WHERE feedback_id=? ORDER BY id''', (row['id'],))])
    return result


def feedback_mine(store, user_id):
    with store.connect() as db:
        if not db.execute('SELECT 1 FROM users WHERE id=?', (user_id,)).fetchone():
            raise ValueError('Sign in to view your feedback.')
        return {'feedback': [_feedback_view(db, row) for row in db.execute(
            'SELECT * FROM feedback WHERE user_id=? ORDER BY created_at DESC,id', (user_id,))]}


def submit_feedback(store, user_id, data):
    data = _data(data, {'category', 'body'})
    category, body = data.get('category', 'general'), data.get('body')
    if not isinstance(category, str) or category not in FEEDBACK_CATEGORIES:
        raise ValueError('Choose a valid feedback category.')
    if not isinstance(body, str) or not 1 <= len(body.strip()) <= 3000:
        raise ValueError('Write feedback of up to 3,000 characters.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        rewards._account(db, user_id)
        identifier, current = str(uuid4()), _now()
        db.execute('''INSERT INTO feedback(id,user_id,category,body,created_at,updated_at)
            VALUES (?,?,?,?,?,?)''', (identifier, user_id, category, body.strip(), current, current))
        db.execute('''INSERT INTO feedback_events(feedback_id,actor_user_id,status,note,created_at)
            VALUES (?,?,'pending','Feedback submitted.',?)''', (identifier, user_id, current))
        return {'feedback': _feedback_view(db, db.execute('SELECT * FROM feedback WHERE id=?', (identifier,)).fetchone())}


def feedback_all(store):
    """Server route must enforce administrator identity before calling this read."""
    with store.connect() as db:
        return {'feedback': [_feedback_view(db, row, admin=True) for row in db.execute(
            'SELECT * FROM feedback ORDER BY created_at DESC,id')]}


def update_feedback(store, identifier, admin_user_id, data):
    data = _data(data, {'status', 'note'})
    status, note = data.get('status'), data.get('note')
    if not rewards._uuid(identifier):
        raise ValueError('Choose valid feedback to update.')
    if not isinstance(status, str) or status not in FEEDBACK_STATUSES:
        raise ValueError('Choose a valid feedback status.')
    if not isinstance(note, str) or not 1 <= len(note.strip()) <= 2000:
        raise ValueError('Add a response of up to 2,000 characters.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        rewards._require_admin(db, admin_user_id)
        row = db.execute('SELECT * FROM feedback WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise ValueError('Feedback not found.')
        if row['status'] == status and row['admin_note'] == note.strip():
            return {'feedback': _feedback_view(db, row, admin=True)}
        current = _now()
        db.execute('UPDATE feedback SET status=?,admin_note=?,updated_at=?,updated_by=? WHERE id=?',
                   (status, note.strip(), current, admin_user_id, identifier))
        db.execute('''INSERT INTO feedback_events(feedback_id,actor_user_id,previous_status,status,note,created_at)
            VALUES (?,?,?,?,?,?)''', (identifier, admin_user_id, row['status'], status, note.strip(), current))
        return {'feedback': _feedback_view(db, db.execute('SELECT * FROM feedback WHERE id=?', (identifier,)).fetchone(), admin=True)}
