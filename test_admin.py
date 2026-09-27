"""Administrator boundaries and data exports using only disposable synthetic data."""
import base64
import csv
import hashlib
import io
import json
import secrets
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request

from PIL import Image

import accounts
from demo_server import DemoHandler
from http.server import ThreadingHTTPServer
from manage import export_records, review
from rewards import configure_reward, import_inventory
from server import ROOT, create_server, process_next, validate_observation
from test_accounts import Browser, PASSWORD
from test_server import analysis, payload


class AdminBrowser(Browser):
    def raw(self, path):
        try:
            response = self.opener.open(Request(self.base + path), timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read(), response.headers


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'admin-test.sqlite3'
        self.server = create_server(0, self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.browser = AdminBrowser(self.base)
        self.photo_number = 0

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def register(self, admin=False, browser=None, email='reviewer@example.test'):
        browser = browser or self.browser
        status, body, _ = browser.register(email, 'Local reviewer')
        self.assertEqual(status, 201, body)
        self.assertEqual(body['user']['role'], 'member')
        user = body['user']
        if admin:
            with self.server.store.connect() as db:
                db.execute("UPDATE users SET role='admin' WHERE id=?", (user['id'],))
        return user

    def add(self, user_id=None, **changes):
        self.photo_number += 1
        image = io.BytesIO()
        Image.new('RGB', (800, 600), (self.photo_number * 19 % 256, 103, 157)).save(image, 'PNG')
        data = validate_observation(payload(photo='data:image/png;base64,' + base64.b64encode(image.getvalue()).decode(), **changes))
        data['user_id'] = user_id
        data['rewards_waived'] = user_id is None
        self.server.store.insert(data)
        return data

    def analyze_next(self):
        self.assertTrue(process_next(self.server.store, 'synthetic-key', 'synthetic-model', lambda *_: (analysis(), 'synthetic-response')))

    def assert_private_values_absent(self, raw, values):
        text = raw.decode('utf-8-sig') if isinstance(raw, bytes) else json.dumps(raw)
        for value in values:
            self.assertNotIn(value, text)
        for key in ('password_hash', 'token_hash', 'csrf_token', 'user_id', 'contributor_hash', 'voucher_code'):
            self.assertNotIn('"' + key + '"', text)

    def test_admin_routes_require_session_and_admin_role(self):
        routes = ('/api/admin/data', '/api/admin/export?format=json', '/api/admin/export?format=csv')
        for route in routes:
            self.assertEqual(self.browser.raw(route)[0], 401)
        self.register()
        for route in routes:
            self.assertEqual(self.browser.raw(route)[0], 403)
        data = self.add()
        status, _, _ = self.browser.request('/api/admin/reviews/' + data['id'], {'decision': 'rejected', 'note': 'Cannot review as member.'})
        self.assertEqual(status, 403)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 0)

    def test_member_cannot_supply_privileged_role_during_registration_login_or_upload(self):
        data = {'email': 'forged@example.test', 'display_name': 'Forged admin', 'password': PASSWORD, 'role': 'admin'}
        status, body, _ = self.browser.request('/api/auth/register', data)
        self.assertEqual(status, 400, body)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)
        self.register()
        status, body, _ = self.browser.request('/api/auth/login', {'email': 'reviewer@example.test', 'password': PASSWORD, 'role': 'admin'})
        self.assertEqual(status, 400, body)
        status, body, _ = self.browser.request('/api/observations', payload(as_guest=False, role='admin'))
        self.assertEqual(status, 400, body)
        self.assertEqual(self.browser.request('/api/auth/me')[1]['user']['role'], 'member')
        self.assertEqual(self.browser.raw('/api/admin/data')[0], 403)

    def test_role_changes_take_effect_on_existing_session_immediately(self):
        user = self.register(admin=True)
        self.assertEqual(self.browser.request('/api/auth/me')[1]['user']['role'], 'admin')
        self.assertEqual(self.browser.raw('/api/admin/data')[0], 200)
        with self.server.store.connect() as db:
            db.execute("UPDATE users SET role='member' WHERE id=?", (user['id'],))
        self.assertEqual(self.browser.request('/api/auth/me')[1]['user']['role'], 'member')
        self.assertEqual(self.browser.raw('/api/admin/data')[0], 403)
        self.assertEqual(self.browser.raw('/api/admin/export?format=csv')[0], 403)
        record = self.add()
        self.assertEqual(self.browser.request('/api/admin/reviews/' + record['id'], {'decision': 'rejected', 'note': 'No longer an admin.'})[0], 403)

    def test_admin_data_and_exports_exclude_accounts_credentials_vouchers_and_exact_gps(self):
        user = self.register(admin=True)
        record = self.add(user_id=user['id'], position={'latitude': -41.329123, 'longitude': 174.796123, 'accuracy': 12}, location_confirmed=True)
        configure_reward(self.server.store, 'private-test', 'Test issuer', 'Private fixture', 10, 'No real value', True)
        import_inventory(self.server.store, 'private-test', [{'code': 'PRIVATE-VOUCHER-CODE-DO-NOT-EXPORT', 'expires_at': None}], 'Private operator')
        with self.server.store.connect() as db:
            password_hash = db.execute('SELECT password_hash FROM users WHERE id=?', (user['id'],)).fetchone()[0]
            session = dict(db.execute('SELECT * FROM sessions').fetchone())
        private = [user['id'], user['email'], password_hash, session['token_hash'], session['csrf_token'],
                   '-41.329123', '174.796123', 'PRIVATE-VOUCHER-CODE-DO-NOT-EXPORT', 'Private operator']
        for path in ('/api/admin/data', '/api/admin/export?format=json', '/api/admin/export?format=csv'):
            with self.subTest(path=path):
                status, raw, headers = self.browser.raw(path)
                self.assertEqual(status, 200)
                self.assert_private_values_absent(raw, private)
                self.assertIn(record['id'], raw.decode('utf-8-sig'))
                self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_filters_apply_to_json_and_csv_without_leaking_other_communities(self):
        self.register(admin=True)
        lyall = self.add(community='Lyall Bay', feelings='Lyall fixture only')
        petone = self.add(community='Petone Beach', feelings='Petone fixture only')
        self.analyze_next()
        review(self.server.store, lyall['id'], 'approved', 'Fixture reviewer', 'Checked all evidence.')
        for suffix in ('/api/admin/data?', '/api/admin/export?format=json&', '/api/admin/export?format=csv&'):
            query = urlencode({'community': 'Lyall Bay', 'review': 'approved', 'status': 'analyzed'})
            status, raw, _ = self.browser.raw(suffix + query)
            self.assertEqual(status, 200)
            text = raw.decode('utf-8-sig')
            self.assertIn(lyall['id'], text)
            self.assertNotIn(petone['id'], text)
            self.assertNotIn('Petone fixture only', text)

    def test_invalid_filters_and_export_formats_are_rejected(self):
        self.register(admin=True)
        for query in ('community=Unknown', 'review=unchecked', 'status=confirmed', 'format=xml',
                      'community=%27+OR+1%3D1--', 'unknown=admin', 'review=pending&review=approved',
                      'format=csv&format=json'):
            with self.subTest(query=query):
                self.assertEqual(self.browser.raw('/api/admin/export?' + query)[0], 400)

    def test_raw_pending_data_is_included_but_never_presented_as_curated(self):
        self.register(admin=True)
        eligible = self.add()
        self.analyze_next()
        review(self.server.store, eligible['id'], 'approved', 'Fixture reviewer', 'Checked photo, time and place.')
        incomplete = self.add(observed_at=None)
        self.analyze_next()
        pending = self.add()
        status, body, _ = self.browser.request('/api/admin/data')
        self.assertEqual(status, 200)
        self.assertEqual(body['dataset_kind'], 'community-research-v1')
        self.assertEqual(body['summary']['observations'], 3)
        self.assertEqual(body['summary']['curated_observations'], 1)
        self.assertEqual(body['summary']['unverified_observations'], 2)
        rows = {row['id']: row for row in body['observations']}
        self.assertIs(rows[eligible['id']]['export_eligible'], True)
        self.assertEqual(rows[eligible['id']]['export_blocks'], [])
        for record in (incomplete, pending):
            self.assertIs(rows[record['id']]['export_eligible'], False)
            self.assertTrue(rows[record['id']]['export_blocks'])
        self.assertIsNone(rows[pending['id']]['analysis'])
        self.assertIn('raw research', body['notice'])
        self.assertEqual(export_records(self.server.store)['record_count'], 1)
        export = json.loads(self.browser.raw('/api/admin/export?format=json')[1])
        self.assertEqual(len(export['observations']), 3)
        self.assertEqual(sum(row['export_eligible'] for row in export['observations']), 1)

    def test_export_attachments_keep_raw_json_and_neutralize_csv_formulas(self):
        user = self.register(admin=True)
        formulas = ['=1+1', '+SUM(1,2)', '-1+3', '@SUM(1,2)', '\ufeff=1+1',
                    '=IMPORTXML("https://example.invalid/test","//item")',
                    '=HYPERLINK("https://example.invalid/","Click")',
                    'Ordinary text with a comma, a "quote", and\na second line.']
        records = [self.add(user_id=user['id'], feelings=feelings) for feelings in formulas]
        with self.server.store.connect() as db:
            db.execute('UPDATE users SET display_name=? WHERE id=?', ('=2+2', user['id']))
        status, raw_json, json_headers = self.browser.raw('/api/admin/export?format=json')
        self.assertEqual(status, 200)
        self.assertTrue(json_headers['Content-Type'].startswith('application/json'))
        self.assertIn('attachment;', json_headers['Content-Disposition'])
        self.assertIn('.json', json_headers['Content-Disposition'])
        json_rows = {row['id']: row for row in json.loads(raw_json)['observations']}
        status, raw_csv, csv_headers = self.browser.raw('/api/admin/export?format=csv')
        self.assertEqual(status, 200)
        self.assertTrue(csv_headers['Content-Type'].startswith('text/csv'))
        self.assertIn('attachment;', csv_headers['Content-Disposition'])
        self.assertIn('.csv', csv_headers['Content-Disposition'])
        self.assertTrue(raw_csv.startswith(b'\xef\xbb\xbf'))
        rows = list(csv.DictReader(io.StringIO(raw_csv.decode('utf-8-sig'), newline='')))
        self.assertEqual(len(rows), len(formulas))
        csv_rows = {row['id']: row for row in rows}
        for index, record in enumerate(records):
            original = record['feelings']
            self.assertEqual(json_rows[record['id']]['feelings'], original)
            self.assertEqual(csv_rows[record['id']]['feelings'], ("'" if index < len(formulas) - 1 else '') + original)
            self.assertEqual(csv_rows[record['id']]['author_name'], "'=2+2")
            self.assertEqual(csv_rows[record['id']]['export_eligible'], 'false')
        self.assertNotIn('user_id', rows[0])

    def test_role_cli_grants_and_revokes_only_existing_accounts(self):
        self.register()
        command = [sys.executable, str(ROOT / 'admin_manage.py'), '--db', str(self.path)]
        granted = subprocess.run(command + ['grant', '  REVIEWER@EXAMPLE.TEST  '], capture_output=True, text=True, timeout=20)
        self.assertEqual(granted.returncode, 0, granted.stderr)
        self.assertNotIn(PASSWORD, granted.stdout + granted.stderr)
        self.assertEqual(self.browser.request('/api/auth/me')[1]['user']['role'], 'admin')
        self.assertEqual(self.browser.raw('/api/admin/data')[0], 200)
        revoked = subprocess.run(command + ['revoke', 'reviewer@example.test'], capture_output=True, text=True, timeout=20)
        self.assertEqual(revoked.returncode, 0, revoked.stderr)
        self.assertEqual(self.browser.request('/api/auth/me')[1]['user']['role'], 'member')
        self.assertEqual(self.browser.raw('/api/admin/data')[0], 403)
        missing = subprocess.run(command + ['grant', 'missing@example.test'], capture_output=True, text=True, timeout=20)
        self.assertNotEqual(missing.returncode, 0)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0], 0)

    def test_admin_invitation_is_email_bound_hashed_and_consumed_once(self):
        invitation = accounts.create_admin_invite(self.server.store, 'invited@example.test')
        token = invitation['token']
        with self.server.store.connect() as db:
            stored = dict(db.execute('SELECT * FROM admin_invites').fetchone())
        self.assertNotIn(token, json.dumps(stored))
        self.assertEqual(stored['token_hash'], hashlib.sha256(token.encode()).hexdigest())
        self.assertIsNone(stored['used_at'])
        data = {'token': token, 'email': 'invited@example.test', 'display_name': 'Invited reviewer', 'password': PASSWORD}
        wrong = {**data, 'email': 'someone-else@example.test'}
        self.assertEqual(self.browser.request('/api/admin/setup', wrong)[0], 400)
        self.assertEqual(self.browser.request('/api/admin/setup', {**data, 'password': 'short'})[0], 400)
        with self.server.store.connect() as db:
            self.assertIsNone(db.execute('SELECT used_at FROM admin_invites').fetchone()[0])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)
        status, body, headers = self.browser.request('/api/admin/setup', data)
        self.assertEqual(status, 201, body)
        self.assertEqual(body['user']['role'], 'admin')
        self.assertEqual(body['user']['email'], 'invited@example.test')
        self.assertTrue(body['csrfToken'])
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertEqual(self.browser.raw('/api/admin/data')[0], 200)
        with self.server.store.connect() as db:
            self.assertIsNotNone(db.execute('SELECT used_at FROM admin_invites').fetchone()[0])
        another = AdminBrowser(self.base)
        self.assertEqual(another.request('/api/admin/setup', data)[0], 400)
        self.assertIsNone(another.request('/api/auth/me')[1]['user'])

    def test_expired_missing_or_unknown_invitation_cannot_create_an_admin(self):
        invitation = accounts.create_admin_invite(self.server.store, 'expired@example.test')
        with self.server.store.connect() as db:
            db.execute('UPDATE admin_invites SET expires_at=0')
        data = {'email': 'expired@example.test', 'display_name': 'Expired reviewer', 'password': PASSWORD}
        for token in ('', 'unknown-token', invitation['token']):
            self.assertEqual(self.browser.request('/api/admin/setup', {**data, 'token': token})[0], 400)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)

    def test_parallel_invitation_acceptance_creates_exactly_one_admin(self):
        invitation = accounts.create_admin_invite(self.server.store, 'parallel@example.test')
        data = {'token': invitation['token'], 'email': 'parallel@example.test', 'display_name': 'Parallel reviewer', 'password': PASSWORD}
        browsers = [AdminBrowser(self.base), AdminBrowser(self.base)]
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda browser: browser.request('/api/admin/setup', data), browsers))
        self.assertEqual(sorted(response[0] for response in responses), [201, 400])
        with self.server.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 1)

    def test_admin_setup_rejects_foreign_origin_without_consuming_invitation(self):
        invitation = accounts.create_admin_invite(self.server.store, 'origin@example.test')
        data = {'token': invitation['token'], 'email': 'origin@example.test', 'display_name': 'Origin reviewer', 'password': PASSWORD}
        self.assertEqual(self.browser.request('/api/admin/setup', data, {'Origin': 'https://foreign.example'})[0], 403)
        self.assertEqual(self.browser.request('/api/admin/setup', data, {'Sec-Fetch-Site': 'cross-site'})[0], 403)
        with self.server.store.connect() as db:
            self.assertIsNone(db.execute('SELECT used_at FROM admin_invites').fetchone()[0])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)

    def test_invitation_never_resets_or_promotes_an_existing_registered_account(self):
        invitation = accounts.create_admin_invite(self.server.store, 'reviewer@example.test')
        user = self.register()
        with self.server.store.connect() as db:
            before = dict(db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone())
        setup = {'token': invitation['token'], 'email': user['email'], 'display_name': 'Replacement name',
                 'password': 'A different password that must not replace anything'}
        guest = AdminBrowser(self.base)
        self.assertEqual(guest.request('/api/admin/setup', setup)[0], 400)
        with self.server.store.connect() as db:
            after = dict(db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone())
        self.assertEqual(after, before)
        self.assertEqual(guest.login(user['email'], PASSWORD)[0], 200)
        self.assertEqual(guest.request('/api/auth/me')[1]['user']['role'], 'member')
        with self.assertRaises(ValueError):
            accounts.create_admin_invite(self.server.store, user['email'])

    def test_bootstrap_does_not_refresh_expired_used_tokens_or_replace_existing_accounts(self):
        token = secrets.token_urlsafe(32)
        accounts.bootstrap_admin_invite(self.server.store, 'bootstrap@example.test', token)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)
            db.execute("UPDATE admin_invites SET expires_at=0,used_at='already-used'")
        accounts.bootstrap_admin_invite(self.server.store, 'bootstrap@example.test', token)
        with self.server.store.connect() as db:
            invitation = dict(db.execute('SELECT * FROM admin_invites').fetchone())
        self.assertEqual(invitation['expires_at'], 0)
        self.assertEqual(invitation['used_at'], 'already-used')
        self.assertNotIn(token, json.dumps(invitation))
        self.register()
        accounts.bootstrap_admin_invite(self.server.store, 'reviewer@example.test', secrets.token_urlsafe(32))
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM admin_invites').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT role FROM users').fetchone()[0], 'member')
            db.execute("UPDATE users SET role='admin'")
        accounts.bootstrap_admin_invite(self.server.store, 'new-admin@example.test', secrets.token_urlsafe(32))
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM admin_invites').fetchone()[0], 1)

    def test_invitation_cli_writes_secret_only_to_new_private_file(self):
        output = Path(self.temp.name) / 'private-admin-invitation.json'
        command = [sys.executable, str(ROOT / 'admin_manage.py'), '--db', str(self.path), 'invite',
                   'cli-admin@example.test', '--out', str(output), '--origin', 'https://wainet.example.test']
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        invitation = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(invitation['email'], 'cli-admin@example.test')
        self.assertTrue(invitation['url'].startswith('https://wainet.example.test/admin#setup='))
        self.assertIn(invitation['token'], invitation['url'])
        self.assertNotIn(invitation['token'], result.stdout + result.stderr)
        original = output.read_bytes()
        repeated = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(repeated.returncode, 0)
        self.assertEqual(output.read_bytes(), original)
        self.assertNotIn(invitation['token'], repeated.stdout + repeated.stderr)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM admin_invites').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)

    def test_public_https_origin_sets_secure_cookie_and_checks_host_and_origin(self):
        public = create_server(0, Path(self.temp.name) / 'public.sqlite3', public_origin='https://wainet.example.test')
        thread = threading.Thread(target=public.serve_forever, daemon=True)
        thread.start()
        browser = AdminBrowser(f'http://127.0.0.1:{public.server_port}')
        trusted = {'Host': 'wainet.example.test', 'Origin': 'https://wainet.example.test'}
        data = {'email': 'public@example.test', 'display_name': 'Public account', 'password': PASSWORD}
        try:
            status, body, headers = browser.request('/api/auth/register', data, trusted)
            self.assertEqual(status, 201, body)
            self.assertIn('; Secure', headers['Set-Cookie'])
            self.assertIn('HttpOnly', headers['Set-Cookie'])
            self.assertNotIn('Access-Control-Allow-Origin', headers)
            user = body['user']
            cookie = headers['Set-Cookie'].split(';', 1)[0]
            with public.store.connect() as db:
                db.execute("UPDATE users SET role='admin' WHERE id=?", (user['id'],))
            auth = {**trusted, 'Cookie': cookie}
            self.assertEqual(browser.request('/api/admin/data', headers=auth)[0], 200)
            for headers in ({'Host': 'attacker.example.test', 'X-Forwarded-Host': 'wainet.example.test'},
                            {**trusted, 'Origin': 'https://attacker.example.test'},
                            {**trusted, 'Origin': 'http://wainet.example.test', 'X-Forwarded-Proto': 'https'}):
                with self.subTest(headers=headers):
                    self.assertEqual(browser.request('/api/auth/login', data, headers)[0], 403)
            status, _, headers = browser.request('/api/auth/logout', {}, auth)
            self.assertEqual(status, 200)
            self.assertIn('; Secure', headers['Set-Cookie'])
            self.assertIn('Max-Age=0', headers['Set-Cookie'])
            self.assertEqual(browser.request('/api/admin/data', headers=auth)[0], 401)
        finally:
            public.shutdown()
            public.server_close()
            thread.join()

    def test_short_temporary_password_requires_change_before_any_privileged_action(self):
        temporary = 'T3st!'
        user = accounts.create_temporary_admin(self.server.store, 'temporary@example.test', 'Temporary reviewer', temporary)
        self.assertEqual(user['role'], 'admin')
        self.assertIs(user['password_change_required'], True)
        status, body, _ = self.browser.login(user['email'], temporary)
        self.assertEqual(status, 200, body)
        self.assertIs(body['user']['password_change_required'], True)
        for path in ('/api/admin/data', '/api/admin/export?format=json', '/api/admin/export?format=csv', '/api/admin/vouchers'):
            status, body, _ = self.browser.request(path)
            self.assertEqual(status, 403)
            self.assertEqual(body['code'], 'password_change_required')
        for path, data in [('/api/observations', payload(as_guest=True)),
                           ('/api/admin/reviews/' + self.add()['id'], {'decision': 'rejected', 'note': 'Not yet allowed.'})]:
            status, body, _ = self.browser.request(path, data)
            self.assertEqual(status, 403)
            self.assertEqual(body['code'], 'password_change_required')
        with self.server.store.connect() as db:
            saved = dict(db.execute('SELECT * FROM users').fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 0)
        self.assertNotIn(temporary, json.dumps(saved))
        self.assertTrue(accounts.check_password(temporary, saved['password_hash']))
        guest = AdminBrowser(self.base)
        self.assertEqual(guest.register('short@example.test', 'Short password account', temporary)[0], 400)
        self.assertEqual(self.browser.request('/api/auth/logout', {})[0], 200)

    def test_password_change_requires_current_password_csrf_and_strong_replacement(self):
        temporary = 'T3st!'
        accounts.create_temporary_admin(self.server.store, 'temporary@example.test', 'Temporary reviewer', temporary)
        self.assertEqual(self.browser.login('temporary@example.test', temporary)[0], 200)
        request = {'current_password': temporary, 'new_password': PASSWORD}
        self.assertEqual(self.browser.request('/api/auth/password', request, csrf=False)[0], 403)
        self.assertEqual(self.browser.request('/api/auth/password', request, {'X-CSRF-Token': 'wrong-token'}, csrf=False)[0], 403)
        self.assertIn(self.browser.request('/api/auth/password', {**request, 'current_password': 'incorrect'})[0], (400, 401))
        self.assertEqual(self.browser.request('/api/auth/password', {**request, 'new_password': 'short'})[0], 400)
        self.assertEqual(self.browser.request('/api/auth/password', {**request, 'new_password': 'a' * 129})[0], 400)
        self.assertEqual(self.browser.request('/api/auth/password', {**request, 'password_change_required': False})[0], 400)
        self.assertIs(self.browser.request('/api/auth/me')[1]['user']['password_change_required'], True)
        self.assertEqual(self.browser.raw('/api/admin/data')[0], 403)

    def test_password_change_rotates_cookie_and_revokes_every_previous_session(self):
        temporary = 'T3st!'
        user = accounts.create_temporary_admin(self.server.store, 'temporary@example.test', 'Temporary reviewer', temporary)
        other = AdminBrowser(self.base)
        self.assertEqual(self.browser.login(user['email'], temporary)[0], 200)
        self.assertEqual(other.login(user['email'], temporary)[0], 200)
        old_cookies = ['; '.join(f'{c.name}={c.value}' for c in browser.cookies) for browser in (self.browser, other)]
        old_csrf = self.browser.csrf
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 2)
        status, body, headers = self.browser.request('/api/auth/password', {'current_password': temporary, 'new_password': PASSWORD})
        self.assertEqual(status, 200, body)
        self.assertIs(body['user']['password_change_required'], False)
        self.assertNotEqual(body['csrfToken'], old_csrf)
        self.assertNotIn(headers['Set-Cookie'].split(';', 1)[0], old_cookies)
        self.assertEqual(self.browser.raw('/api/admin/data')[0], 200)
        self.assertIsNone(other.request('/api/auth/me')[1]['user'])
        for cookie in old_cookies:
            replay = AdminBrowser(self.base)
            self.assertEqual(replay.request('/api/admin/data', headers={'Cookie': cookie})[0], 401)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 1)
            row = dict(db.execute('SELECT * FROM users').fetchone())
        self.assertTrue(accounts.check_password(PASSWORD, row['password_hash']))
        self.assertFalse(accounts.check_password(temporary, row['password_hash']))
        fresh = AdminBrowser(self.base)
        self.assertEqual(fresh.login(user['email'], temporary)[0], 401)
        self.assertEqual(fresh.login(user['email'], PASSWORD)[0], 200)

    def test_temporary_admin_bootstrap_never_replaces_existing_accounts_or_hashes(self):
        encoded = accounts.hash_password('T3st!')
        user = accounts.bootstrap_temporary_admin(self.server.store, 'bootstrap-temp@example.test', encoded)
        self.assertTrue(user['password_change_required'])
        with self.server.store.connect() as db:
            before = dict(db.execute('SELECT * FROM users').fetchone())
        self.assertIsNone(accounts.bootstrap_temporary_admin(self.server.store, 'bootstrap-temp@example.test', accounts.hash_password('Different!')))
        self.assertIsNone(accounts.bootstrap_temporary_admin(self.server.store, 'another-admin@example.test', encoded))
        with self.server.store.connect() as db:
            self.assertEqual([dict(row) for row in db.execute('SELECT * FROM users')], [before])
        with self.assertRaises(ValueError):
            accounts.create_temporary_admin(self.server.store, 'bootstrap-temp@example.test', 'Replacement', 'Reset!')
        with self.assertRaises(ValueError):
            accounts.bootstrap_temporary_admin(self.server.store, 'bad@example.test', 'plaintext-is-not-a-hash')
        with self.assertRaises(ValueError):
            accounts.bootstrap_temporary_admin(self.server.store, 'bad@example.test', encoded.replace('$600000$', '$1000$'))

    def test_review_requires_csrf_and_uses_server_authenticated_reviewer(self):
        user = self.register(admin=True)
        record = self.add(user_id=user['id'])
        self.analyze_next()
        path = '/api/admin/reviews/' + record['id']
        request = {'decision': 'approved', 'note': 'Reviewed photo, date and location.'}
        self.assertEqual(self.browser.request(path, request, csrf=False)[0], 403)
        self.assertEqual(self.browser.request(path, request, {'X-CSRF-Token': 'forged-token'}, csrf=False)[0], 403)
        self.assertEqual(self.browser.request(path, {**request, 'reviewer': 'Forged operator'})[0], 400)
        status, body, _ = self.browser.request(path, request)
        self.assertEqual(status, 200, body)
        with self.server.store.connect() as db:
            audits = [dict(row) for row in db.execute('SELECT * FROM reviews')]
        self.assertEqual(len(audits), 1)
        self.assertIn('Local reviewer', audits[0]['reviewer'])
        self.assertEqual(audits[0]['note'], request['note'])
        self.assertEqual(self.server.store.wallet(user['id'])['points'], 10)

    def test_admin_cannot_approve_unanalyzed_or_incomplete_evidence(self):
        self.register(admin=True)
        pending = self.add(observed_at=None)
        request = {'decision': 'approved', 'note': 'Attempted approval.'}
        path = '/api/admin/reviews/' + pending['id']
        self.assertEqual(self.browser.request(path, request)[0], 400)
        self.analyze_next()
        self.assertEqual(self.browser.request(path, request)[0], 400)
        self.assertEqual(export_records(self.server.store)['record_count'], 0)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 0)
        self.assertEqual(self.browser.request(path, {'decision': 'rejected', 'note': 'Missing photo date.'})[0], 200)

    def test_demo_handler_ignores_a_genuine_admin_cookie_and_never_exports_live_admin_data(self):
        self.register(admin=True)
        # Sharing this disposable Store deliberately makes the cookie valid at the database level.
        # DemoHandler must still decline it rather than inherit real-app administrator access.
        demo = ThreadingHTTPServer(('127.0.0.1', 0), DemoHandler)
        demo.store, demo.api_key, demo.model = self.server.store, '', 'demo-simulated-v1'
        thread = threading.Thread(target=demo.serve_forever, daemon=True)
        thread.start()
        original = self.browser.base
        self.browser.base = f'http://127.0.0.1:{demo.server_port}'
        try:
            self.assertIsNone(self.browser.request('/api/auth/me')[1]['user'])
            for path in ('/api/admin/data', '/api/admin/export?format=json', '/api/admin/export?format=csv'):
                self.assertEqual(self.browser.raw(path)[0], 401)
            self.assertEqual(self.browser.request('/api/admin/reviews/not-a-real-id', {'decision': 'approved', 'note': 'Blocked demo action.'})[0], 405)
        finally:
            self.browser.base = original
            demo.shutdown()
            demo.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
