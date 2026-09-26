"""Account ownership and guest reward-waiver checks against a disposable server."""
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

from manage import review
from accounts import register
from server import Store, create_server, process_next, validate_observation
from test_server import analysis, payload


PASSWORD = 'A reliable coastal password 42!'


class Browser:
    def __init__(self, base):
        self.base = base
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.csrf = None

    def request(self, path, data=None, headers=None, csrf=True):
        request_headers = {'Content-Type': 'application/json', **(headers or {})}
        if data is not None and csrf and self.csrf:
            request_headers.setdefault('X-CSRF-Token', self.csrf)
        request = Request(self.base + path, data=json.dumps(data).encode() if data is not None else None,
                          headers=request_headers)
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            body = json.load(response)
            status, response_headers = response.status, response.headers
        if body.get('csrfToken'):
            self.csrf = body['csrfToken']
        return status, body, response_headers

    def register(self, email='coastal@example.test', name='Coastal observer', password=PASSWORD):
        return self.request('/api/auth/register', {'email': email, 'display_name': name, 'password': password})

    def login(self, email='coastal@example.test', password=PASSWORD):
        return self.request('/api/auth/login', {'email': email, 'password': password})


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / 'accounts.sqlite3'
        self.server = create_server(0, self.db_path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.browser = Browser(self.base)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def register(self, browser=None, email='coastal@example.test'):
        browser = browser or self.browser
        status, body, headers = browser.register(email)
        self.assertEqual(status, 201, body)
        self.assertTrue(body['user']['id'])
        self.assertTrue(body['csrfToken'])
        return body['user'], headers

    def saved_row(self, identifier):
        with self.server.store.connect() as db:
            row = db.execute('SELECT * FROM observations WHERE id=?', (identifier,)).fetchone()
            return dict(row) if row else None

    def approve(self, identifier):
        self.assertTrue(process_next(self.server.store, 'mock-key', 'mock-model', lambda *_: (analysis(), 'mock-response')))
        review(self.server.store, identifier, 'approved', 'Test reviewer', 'Checked photo, date and community location.')

    def test_registration_uses_normalized_unique_email_and_hashed_password(self):
        status, body, headers = self.browser.register('  Coastal@Example.Test  ')
        self.assertEqual(status, 201, body)
        self.assertEqual(body['user']['email'], 'coastal@example.test')
        self.assertNotIn('password', json.dumps(body).lower())
        cookie = headers.get('Set-Cookie', '')
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=', cookie)
        self.assertIn('Path=/', cookie)
        self.assertTrue(self.browser.cookies)
        with self.server.store.connect() as db:
            users = [dict(row) for row in db.execute('SELECT * FROM users')]
        self.assertEqual(len(users), 1)
        self.assertNotIn(PASSWORD, json.dumps(users))
        self.assertIn('password_hash', users[0])
        self.assertNotEqual(users[0]['password_hash'], PASSWORD)
        algorithm, iterations, salt, digest = users[0]['password_hash'].split('$')
        self.assertEqual(algorithm, 'pbkdf2_sha256')
        self.assertGreaterEqual(int(iterations), 600_000)
        self.assertGreaterEqual(len(bytes.fromhex(salt)), 16)
        self.assertEqual(len(bytes.fromhex(digest)), 32)
        token = next(iter(self.browser.cookies)).value
        with self.server.store.connect() as db:
            stored_sessions = [dict(row) for row in db.execute('SELECT * FROM sessions')]
        self.assertEqual(stored_sessions[0]['token_hash'], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, json.dumps(stored_sessions))
        other = Browser(self.base)
        status, _, _ = other.register('COASTAL@example.test')
        self.assertEqual(status, 400)

    def test_registration_rejects_bad_email_weak_password_and_empty_name(self):
        for changes in ({'email': 'invalid'}, {'email': 'a\nb@example.test'}, {'password': 'short'},
                        {'display_name': '   '}, {'display_name': 'a' * 81}):
            with self.subTest(changes=changes):
                data = {'email': 'valid@example.test', 'display_name': 'Local', 'password': PASSWORD, **changes}
                status, _, _ = self.browser.request('/api/auth/register', data)
                self.assertEqual(status, 400)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)

    def test_login_persists_identity_across_browser_and_database_reopen(self):
        user, _ = self.register()
        self.assertEqual(Store(self.db_path).path, self.server.store.path)
        another = Browser(self.base)
        status, body, _ = another.login(' COASTAL@EXAMPLE.TEST ')
        self.assertEqual(status, 200, body)
        self.assertEqual(body['user']['id'], user['id'])
        self.assertNotEqual(another.csrf, self.browser.csrf)
        status, body, _ = another.request('/api/auth/me')
        self.assertEqual(status, 200)
        self.assertEqual(body['user']['id'], user['id'])
        self.assertTrue(body['csrfToken'])

    def test_bad_login_has_no_authenticated_session(self):
        self.register()
        another = Browser(self.base)
        for email, password in [('coastal@example.test', 'Wrong coastal password 42!'),
                                ('unknown@example.test', PASSWORD)]:
            status, body, _ = another.login(email, password)
            self.assertEqual(status, 401, body)
        status, body, _ = another.request('/api/auth/me')
        self.assertEqual(status, 200)
        self.assertIsNone(body['user'])
        self.assertFalse(body.get('csrfToken'))

    def test_guest_upload_automatically_waives_rewards_and_cannot_claim_after_registration(self):
        client = str(uuid4())
        data = payload(as_guest=True)
        status, body, _ = self.browser.request('/api/observations', data, {'X-Client-ID': client})
        self.assertEqual(status, 201, body)
        saved = self.saved_row(data['id'])
        self.assertIsNone(saved['user_id'])
        self.assertEqual(saved['rewards_waived'], 1)
        self.approve(data['id'])
        self.assertEqual(Store(self.db_path).list()[0]['id'], data['id'])
        self.register()
        status, _, _ = self.browser.request('/api/observations', {**data, 'as_guest': False})
        self.assertEqual(status, 400)
        status, wallet, _ = self.browser.request('/api/wallet', headers={'X-Client-ID': client})
        self.assertEqual(status, 200)
        self.assertEqual(wallet['points'], 0)
        self.assertEqual(wallet['pending_observations'], 0)
        self.assertEqual(wallet['entries'], [])
        review(self.server.store, data['id'], 'approved', 'Reviewer', 'Rechecked after registration.')
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM points_ledger WHERE observation_id=?', (data['id'],)).fetchone()[0], 0)
        saved = self.saved_row(data['id'])
        self.assertIsNone(saved['user_id'])
        self.assertEqual(saved['rewards_waived'], 1)

    def test_explicit_guest_upload_by_signed_in_user_is_permanently_waived(self):
        user, _ = self.register()
        data = payload(as_guest=True)
        status, body, _ = self.browser.request('/api/observations', data)
        self.assertEqual(status, 201, body)
        self.approve(data['id'])
        saved = self.saved_row(data['id'])
        self.assertIsNone(saved['user_id'])
        self.assertEqual(saved['rewards_waived'], 1)
        status, wallet, _ = self.browser.request('/api/wallet')
        self.assertEqual(status, 200)
        self.assertEqual(wallet['points'], 0)
        self.assertEqual(wallet['entries'], [])

    def test_account_required_for_reward_upload_and_wallet(self):
        for path, data in [('/api/observations', payload(as_guest=False)), ('/api/wallet', None)]:
            with self.subTest(path=path):
                status, _, _ = self.browser.request(path, data, {'X-Client-ID': str(uuid4())})
                self.assertEqual(status, 401)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 0)

    def test_signed_in_mutations_require_valid_csrf_even_when_uploading_as_guest(self):
        self.register()
        for guest in (True, False):
            for headers in ({}, {'X-CSRF-Token': 'not-the-session-token'}):
                with self.subTest(guest=guest, headers=headers):
                    status, _, _ = self.browser.request('/api/observations', payload(as_guest=guest), headers, csrf=False)
                    self.assertEqual(status, 403)
        status, _, _ = self.browser.request('/api/auth/logout', {}, csrf=False)
        self.assertEqual(status, 403)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 0)

    def test_comments_and_support_use_account_identity_and_require_csrf(self):
        data = payload(as_guest=True)
        self.assertEqual(self.browser.request('/api/observations', data)[0], 201)
        comment_path = f'/api/observations/{data["id"]}/comments'
        support_path = f'/api/observations/{data["id"]}/support'
        for path, body in [(comment_path, {'body': 'Still present today.'}), (support_path, {'supported': True})]:
            self.assertEqual(self.browser.request(path, body, {'X-Client-ID': str(uuid4())})[0], 401)
        self.register()
        for path, body in [(comment_path, {'body': 'Still present today.'}), (support_path, {'supported': True})]:
            self.assertEqual(self.browser.request(path, body, csrf=False)[0], 403)
            self.assertEqual(self.browser.request(path, body)[0], 200)
        self.assertEqual(self.browser.request(comment_path, {'body': 'Forged name', 'author': 'Someone else'})[0], 400)
        self.assertEqual(self.browser.request(support_path, {'supported': True}, {'X-User-ID': str(uuid4())})[0], 400)
        self.assertEqual(self.browser.request(support_path, {'supported': True}, {'X-Client-ID': str(uuid4())})[0], 200)
        record = self.browser.request('/api/observations')[1]['observations'][0]
        self.assertEqual(record['comments'][0]['author'], 'Coastal observer')
        self.assertEqual(len(record['comments']), 1)
        self.assertEqual(record['likes'], 1)
        self.assertTrue(record['liked'])
        self.assertNotIn('coastal@example.test', json.dumps(record))

    def test_reward_ownership_comes_from_session_and_wallet_is_isolated(self):
        owner, _ = self.register()
        other = Browser(self.base)
        other_user, _ = self.register(other, 'another@example.test')
        old_client = str(uuid4())
        forged = payload(as_guest=False, user_id=other_user['id'], rewards_waived=False,
                         contributor_hash=hashlib.sha256(old_client.encode()).hexdigest())
        status, _, _ = self.browser.request('/api/observations', forged)
        self.assertEqual(status, 400)
        self.assertIsNone(self.saved_row(forged['id']))
        data = payload(as_guest=False)
        status, body, _ = self.browser.request('/api/observations', data, {'X-Client-ID': old_client})
        self.assertEqual(status, 201, body)
        saved = self.saved_row(data['id'])
        self.assertEqual(saved['user_id'], owner['id'])
        self.assertEqual(saved['rewards_waived'], 0)
        self.approve(data['id'])
        status, wallet, _ = self.browser.request('/api/wallet')
        self.assertEqual(status, 200)
        self.assertEqual(wallet['points'], 10)
        status, wallet, _ = other.request('/api/wallet', headers={'X-Client-ID': old_client})
        self.assertEqual(status, 200)
        self.assertEqual(wallet['points'], 0)
        self.assertEqual(wallet['entries'], [])
        new_browser = Browser(self.base)
        self.assertEqual(new_browser.login()[0], 200)
        self.assertEqual(new_browser.request('/api/wallet')[1]['points'], 10)

    def test_anonymous_cannot_forge_reward_ownership(self):
        user, _ = self.register()
        guest = Browser(self.base)
        for changes in ({'user_id': user['id']}, {'rewards_waived': False}, {'contributor_hash': user['id']}):
            data = payload(as_guest=True, **changes)
            with self.subTest(changes=changes):
                status, _, _ = guest.request('/api/observations', data)
                self.assertEqual(status, 400)
                self.assertIsNone(self.saved_row(data['id']))

    def test_expired_session_cannot_access_wallet_or_submit_for_rewards(self):
        self.register()
        with self.server.store.connect() as db:
            db.execute('UPDATE sessions SET expires_at=0')
        status, body, _ = self.browser.request('/api/auth/me')
        self.assertEqual(status, 200)
        self.assertIsNone(body['user'])
        self.assertEqual(self.browser.request('/api/wallet')[0], 401)
        self.assertEqual(self.browser.request('/api/observations', payload(as_guest=False))[0], 401)

    def test_legacy_browser_records_and_points_migrate_without_account_claim(self):
        legacy_path = Path(self.temp.name) / 'legacy.sqlite3'
        identifier = str(uuid4())
        browser_id = str(uuid4())
        contributor = hashlib.sha256(browser_id.encode()).hexdigest()
        data = validate_observation(payload(id=identifier))
        with closing(sqlite3.connect(legacy_path)) as db, db:
            db.executescript('''
                CREATE TABLE observations (
                    id TEXT PRIMARY KEY, community TEXT NOT NULL, feelings TEXT NOT NULL,
                    hint TEXT NOT NULL, created TEXT NOT NULL, latitude REAL, longitude REAL,
                    accuracy REAL, image BLOB NOT NULL, mime TEXT NOT NULL,
                    content_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    analysis TEXT, model TEXT, response_id TEXT, analyzed_at TEXT,
                    analysis_error TEXT, schema_version TEXT NOT NULL, prompt_version TEXT NOT NULL,
                    width INTEGER NOT NULL, height INTEGER NOT NULL, image_hash TEXT NOT NULL,
                    observed_at TEXT, quality_flags TEXT NOT NULL, duplicate_of TEXT,
                    review_status TEXT NOT NULL DEFAULT 'pending', contributor_hash TEXT,
                    dataset_consent INTEGER NOT NULL DEFAULT 0, consent_version TEXT, consent_at TEXT
                );
                CREATE TABLE points_ledger (
                    id INTEGER PRIMARY KEY, contributor_hash TEXT NOT NULL,
                    observation_id TEXT NOT NULL REFERENCES observations(id),
                    delta INTEGER NOT NULL, reason TEXT NOT NULL, created TEXT NOT NULL
                );
            ''')
            db.execute('''INSERT INTO observations
                (id,community,feelings,hint,created,image,mime,content_hash,schema_version,prompt_version,
                 width,height,image_hash,observed_at,quality_flags,contributor_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (identifier, data['community'], data['feelings'], data['hint'], '2026-01-03T00:00:00+00:00',
                        data['image'], data['mime'], 'legacy-content-hash', 'legacy-schema', 'legacy-prompt',
                        data['width'], data['height'], data['image_hash'], data['observed_at'],
                        json.dumps(data['quality_flags']), contributor))
            db.execute('''INSERT INTO points_ledger(contributor_hash,observation_id,delta,reason,created)
                          VALUES (?,?,?,?,?)''', (contributor, identifier, 10, 'Legacy review', '2026-01-04T00:00:00+00:00'))
        migrated = Store(legacy_path)
        migrated_again = Store(legacy_path)
        self.assertEqual(migrated_again.list()[0]['feelings'], data['feelings'])
        user = register(migrated, {'email': 'legacy@example.test', 'display_name': 'Local', 'password': PASSWORD})
        self.assertEqual(migrated.wallet(user['id'])['points'], 0)
        self.assertEqual(migrated.wallet(browser_id)['points'], 0)
        with migrated.connect() as db:
            record = dict(db.execute('SELECT * FROM observations WHERE id=?', (identifier,)).fetchone())
            old_points = dict(db.execute('SELECT * FROM points_ledger').fetchone())
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertIsNone(record['user_id'])
        self.assertEqual(record['rewards_waived'], 1)
        self.assertIsNone(old_points['user_id'])
        self.assertEqual(old_points['delta'], 10)
        self.assertTrue(process_next(migrated, 'mock-key', 'mock-model', lambda *_: (analysis(), 'mock-response')))
        review(migrated, identifier, 'approved', 'Reviewer', 'Reviewed existing evidence.')
        self.assertEqual(migrated.wallet(user['id'])['points'], 0)
        with migrated.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM points_ledger').fetchone()[0], 1)

    def test_logout_revokes_cookie_and_csrf(self):
        self.register()
        cookie_header = '; '.join(f'{cookie.name}={cookie.value}' for cookie in self.browser.cookies)
        old_csrf = self.browser.csrf
        self.assertEqual(self.browser.request('/api/auth/logout', {})[0], 200)
        status, body, _ = self.browser.request('/api/auth/me')
        self.assertEqual(status, 200)
        self.assertIsNone(body['user'])
        replay = Browser(self.base)
        status, _, _ = replay.request('/api/wallet', headers={'Cookie': cookie_header})
        self.assertEqual(status, 401)
        status, _, _ = replay.request('/api/observations', payload(as_guest=False),
                                     {'Cookie': cookie_header, 'X-CSRF-Token': old_csrf})
        self.assertEqual(status, 401)

    def test_foreign_origin_cannot_register_or_modify_session(self):
        registration = {'email': 'coastal@example.test', 'display_name': 'Local', 'password': PASSWORD}
        status, _, _ = self.browser.request('/api/auth/register', registration, {'Origin': 'https://outside.example'})
        self.assertEqual(status, 403)
        self.register()
        status, _, _ = self.browser.request('/api/auth/logout', {}, {'Origin': 'https://outside.example'})
        self.assertEqual(status, 403)
        self.assertIsNotNone(self.browser.request('/api/auth/me')[1]['user'])


if __name__ == '__main__':
    unittest.main()
