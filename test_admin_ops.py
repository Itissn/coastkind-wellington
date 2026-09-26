"""Approval, voucher ownership, and private feedback checks with temporary data."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import rewards
from server import create_server, validate_observation
from test_accounts import PASSWORD
from test_admin import AdminBrowser
from test_server import payload


class AdminOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = create_server(0, Path(self.temp.name) / 'admin-operations.sqlite3')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.member, self.user = self.account('member@example.test')
        self.admin, self.operator = self.account('admin@example.test', admin=True)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def account(self, email, admin=False):
        browser = AdminBrowser(self.base)
        status, body, _ = browser.register(email, 'Test operator' if admin else 'Test member', PASSWORD)
        self.assertEqual(status, 201, body)
        user = body['user']
        if admin:
            with self.server.store.connect() as db:
                db.execute("UPDATE users SET role='admin' WHERE id=?", (user['id'],))
        return browser, user

    def points(self, delta=100, user=None):
        user = user or self.user
        record = validate_observation(payload())
        record.update(user_id=user['id'], rewards_waived=False)
        self.server.store.insert(record)
        with self.server.store.connect() as db:
            db.execute('''INSERT INTO points_ledger(contributor_hash,observation_id,delta,reason,created,user_id)
                          VALUES(?,?,?,?,?,?)''', (hashlib.sha256(user['id'].encode()).hexdigest(), record['id'], delta,
                                                  'Synthetic test balance', '2026-01-01T00:00:00Z', user['id']))

    def stock(self, count=1, price=50):
        rewards.configure_reward(self.server.store, 'test-voucher', 'Test issuer', 'Synthetic voucher', price, 'No real monetary value', True)
        if count:
            rewards.import_inventory(self.server.store, 'test-voucher', [
                {'code': 'SYNTHETIC-NO-VALUE-' + str(uuid4()), 'expires_at': None} for _ in range(count)], 'Test operator')

    def request_voucher(self, browser=None, key=None):
        browser = browser or self.member
        status, body, _ = browser.request('/api/rewards/redeem', {'reward_id': 'test-voucher', 'request_id': key or str(uuid4())})
        self.assertEqual(status, 201, body)
        return body['redemption']

    def review(self, request, decision='approved', browser=None, note='Checked this synthetic request.'):
        return (browser or self.admin).request('/api/admin/vouchers/' + request['id'], {'decision': decision, 'note': note})

    def mine(self, browser=None):
        status, body, _ = (browser or self.member).request('/api/rewards/mine')
        self.assertEqual(status, 200, body)
        return body['redemptions']

    def test_request_requires_sign_in_and_csrf_and_pending_never_delivers_code(self):
        self.points()
        self.stock()
        body = {'reward_id': 'test-voucher', 'request_id': str(uuid4())}
        guest = AdminBrowser(self.base)
        self.assertEqual(guest.request('/api/rewards/redeem', body)[0], 401)
        self.assertEqual(self.member.request('/api/rewards/redeem', body, csrf=False)[0], 403)
        request = self.request_voucher(key=body['request_id'])
        self.assertEqual(request['status'], 'pending')
        self.assertFalse(request.get('voucher_code'))
        self.assertEqual(self.server.store.wallet(self.user['id'])['points'], 100)
        self.assertEqual(self.mine()[0]['status'], 'pending')
        self.assertFalse(self.mine()[0].get('voucher_code'))
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reward_redemptions').fetchone()[0], 0)
            code = db.execute('SELECT code FROM reward_vouchers').fetchone()[0]
        self.assertNotIn(code, json.dumps(self.admin.request('/api/admin/vouchers')[1]))

    def test_approval_allocates_one_code_and_debits_once_even_when_retried(self):
        self.points()
        self.stock(2)
        key = str(uuid4())
        request = self.request_voucher(key=key)
        self.assertEqual(self.request_voucher(key=key)['id'], request['id'])
        self.assertEqual(self.request_voucher()['id'], request['id'])
        self.assertEqual(self.member.request('/api/admin/vouchers/' + request['id'], {'decision': 'approved', 'note': 'Cannot self-approve.'})[0], 403)
        self.assertEqual(self.admin.request('/api/admin/vouchers/' + request['id'], {'decision': 'approved', 'note': 'Missing CSRF.'}, csrf=False)[0], 403)
        status, body, _ = self.review(request)
        self.assertEqual(status, 200, body)
        self.assertEqual(body['request']['status'], 'fulfilled')
        own = self.mine()[0]
        self.assertTrue(own['voucher_code'].startswith('SYNTHETIC-NO-VALUE-'))
        self.assertEqual(self.server.store.wallet(self.user['id'])['points'], 50)
        with self.server.store.connect() as db:
            before = db.execute('SELECT COUNT(*) FROM reward_request_events').fetchone()[0]
        self.assertEqual(self.review(request)[0], 200)
        self.assertEqual(self.mine(), [own])
        self.assertEqual(self.server.store.wallet(self.user['id'])['points'], 50)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reward_redemptions').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reward_request_events').fetchone()[0], before)

    def test_rejection_records_audit_without_charging_or_allocating_inventory(self):
        self.points()
        self.stock()
        request = self.request_voucher()
        status, body, _ = self.review(request, 'rejected', note='Need clearer eligibility information.')
        self.assertEqual(status, 200, body)
        self.assertEqual(body['request']['status'], 'rejected')
        own = self.mine()[0]
        self.assertEqual(own['status'], 'rejected')
        self.assertFalse(own.get('voucher_code'))
        self.assertEqual(own['review_note'], 'Need clearer eligibility information.')
        self.assertEqual(self.server.store.wallet(self.user['id'])['points'], 100)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reward_redemptions').fetchone()[0], 0)
            self.assertGreater(db.execute('SELECT COUNT(*) FROM reward_request_events').fetchone()[0], 0)
        self.assertTrue(next(item for item in rewards.catalog(self.server.store) if item['id'] == 'test-voucher')['available'])

    def test_approval_failure_is_atomic_when_points_fall_after_request(self):
        self.points()
        self.stock()
        request = self.request_voucher()
        self.points(-80)
        with self.server.store.connect() as db:
            before = db.execute('SELECT COUNT(*) FROM reward_request_events').fetchone()[0]
        self.assertEqual(self.review(request)[0], 400)
        self.assertEqual(self.mine()[0]['status'], 'pending')
        self.assertFalse(self.mine()[0].get('voucher_code'))
        self.assertEqual(self.server.store.wallet(self.user['id'])['points'], 20)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reward_redemptions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reward_request_events').fetchone()[0], before)
        self.assertTrue(next(item for item in rewards.catalog(self.server.store) if item['id'] == 'test-voucher')['available'])

    def test_approval_failure_is_atomic_when_inventory_expires_after_request(self):
        self.points()
        self.stock()
        request = self.request_voucher()
        with self.server.store.connect() as db:
            db.execute("UPDATE reward_vouchers SET expires_at='2000-01-01T00:00:00+00:00'")
            before = db.execute('SELECT COUNT(*) FROM reward_request_events').fetchone()[0]
        self.assertEqual(self.review(request)[0], 400)
        self.assertEqual(self.mine()[0]['status'], 'pending')
        self.assertEqual(self.server.store.wallet(self.user['id'])['points'], 100)
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reward_redemptions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reward_request_events').fetchone()[0], before)

    def test_member_cannot_read_other_requests_codes_or_administrator_queue(self):
        self.points()
        self.stock()
        request = self.request_voucher()
        other, _ = self.account('another@example.test')
        self.assertEqual(other.request('/api/admin/vouchers')[0], 403)
        self.assertEqual(self.mine(other), [])
        self.assertEqual(self.review(request, browser=other)[0], 403)
        self.assertEqual(self.review(request)[0], 200)
        code = self.mine()[0]['voucher_code']
        self.assertEqual(self.mine(other), [])
        for path in ('/api/observations', '/api/concerns', '/api/rewards/catalog'):
            self.assertNotIn(code, json.dumps(other.request(path)[1]))
        self.assertNotIn(code, json.dumps(self.admin.request('/api/admin/data')[1]))

    def test_concurrent_admin_approvals_cannot_issue_the_last_voucher_twice(self):
        self.points()
        self.stock()
        other, other_user = self.account('another@example.test')
        self.points(user=other_user)
        requests = [self.request_voucher(), self.request_voucher(browser=other)]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.review, requests))
        self.assertEqual(sorted(result[0] for result in results), [200, 400])
        rows = self.mine() + self.mine(other)
        self.assertEqual(sum(row['status'] == 'fulfilled' for row in rows), 1)
        self.assertEqual(sum(bool(row.get('voucher_code')) for row in rows), 1)
        self.assertEqual(sorted([self.server.store.wallet(self.user['id'])['points'],
                                 self.server.store.wallet(other_user['id'])['points']]), [50, 100])
        with self.server.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reward_redemptions').fetchone()[0], 1)

    def test_feedback_requires_member_session_and_csrf_and_stays_private(self):
        data = {'category': 'data_quality', 'body': 'PRIVATE FEEDBACK: this photo date needs correction.'}
        guest = AdminBrowser(self.base)
        self.assertEqual(guest.request('/api/feedback', data)[0], 401)
        self.assertEqual(guest.request('/api/feedback')[0], 401)
        self.assertEqual(self.member.request('/api/feedback', data, csrf=False)[0], 403)
        status, body, _ = self.member.request('/api/feedback', data)
        self.assertEqual(status, 201, body)
        feedback = body['feedback']
        self.assertEqual(feedback['status'], 'pending')
        self.assertEqual(feedback['body'], data['body'])
        own = self.member.request('/api/feedback')[1]['feedback']
        self.assertEqual(len(own), 1)
        other, _ = self.account('another@example.test')
        self.assertEqual(self.member.request('/api/feedback', {**data, 'user_id': self.operator['id']})[0], 400)
        self.assertEqual(self.member.request('/api/feedback', {**data, 'category': 'invalid'})[0], 400)
        self.assertEqual(self.member.request('/api/feedback', {**data, 'body': '   '})[0], 400)
        self.assertEqual(other.request('/api/feedback')[1]['feedback'], [])
        self.assertEqual(other.request('/api/admin/feedback')[0], 403)
        for path in ('/api/observations', '/api/concerns', '/api/admin/data', '/api/admin/export?format=json'):
            self.assertNotIn(data['body'], self.admin.raw(path)[1].decode())
        queue = self.admin.request('/api/admin/feedback')[1]['feedback']
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]['id'], feedback['id'])

    def test_feedback_status_changes_are_admin_only_csrf_protected_and_audited(self):
        _, body, _ = self.member.request('/api/feedback', {'category': 'bug', 'body': 'A synthetic bug report.'})
        feedback = body['feedback']
        path = '/api/admin/feedback/' + feedback['id']
        change = {'status': 'in_progress', 'note': 'Investigating the reported issue.'}
        self.assertEqual(self.member.request(path, change)[0], 403)
        self.assertEqual(self.admin.request(path, change, csrf=False)[0], 403)
        self.assertEqual(self.admin.request(path, {**change, 'status': 'deleted'})[0], 400)
        self.assertEqual(self.admin.request(path, {**change, 'note': '   '})[0], 400)
        status, body, _ = self.admin.request(path, change)
        self.assertEqual(status, 200, body)
        self.assertEqual(body['feedback']['status'], 'in_progress')
        self.assertEqual(self.admin.request(path, {'status': 'resolved', 'note': 'Fixed and checked.'})[0], 200)
        with self.server.store.connect() as db:
            events = [dict(row) for row in db.execute('SELECT * FROM feedback_events ORDER BY rowid')]
        self.assertGreaterEqual(len(events), 2)
        self.assertIn('Investigating the reported issue.', json.dumps(events))
        self.assertIn('Fixed and checked.', json.dumps(events))
        self.assertIn(self.operator['id'], json.dumps(events))
        own = self.member.request('/api/feedback')[1]['feedback'][0]
        self.assertEqual(own['status'], 'resolved')


if __name__ == '__main__':
    unittest.main()
