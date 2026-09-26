"""Synthetic dataset isolation and read-only demonstration checks."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from manage import export_records
from server import ROOT, Store, process_next
from seed_demo import generate_demo
from demo_server import create_demo_server
from build_presentation import build_presentation


class DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent = ROOT / 'data' / 'demo-tests'
        cls.parent.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=cls.parent)
        cls.directory = Path(cls.temp.name)
        cls.summary = generate_demo(cls.directory / 'dataset')
        cls.db_path = Path(cls.summary['db_path'])
        cls.store = Store(cls.db_path)
        cls.presentation_temp = tempfile.TemporaryDirectory(dir=ROOT, prefix='presentation-tests-')
        cls.presentation_directory = Path(cls.presentation_temp.name)
        cls.httpd = create_demo_server(0, cls.db_path)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.httpd.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()
        archive = cls.presentation_directory.parent / (cls.presentation_directory.name + '.zip')
        if archive.exists():
            if archive.resolve().parent != ROOT.resolve() or not archive.name.startswith('presentation-tests-'):
                raise AssertionError('Unexpected test archive path.')
            archive.unlink()
        cls.presentation_temp.cleanup()
        cls.temp.cleanup()

    def request(self, path, method='GET', headers=None):
        request = Request(self.base + path, method=method,
                          data=b'{}' if method != 'GET' else None,
                          headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read(), response.headers

    def database_snapshot(self):
        with self.store.connect() as db:
            names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {name: [tuple(row) for row in db.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                    for name in names}

    def test_dataset_has_expected_coverage_and_explicit_synthetic_labels(self):
        self.assertTrue(self.summary['synthetic'])
        self.assertEqual(self.summary['dataset_kind'], 'synthetic-demo-v1')
        self.assertEqual(self.summary['counts']['observations'], 240)
        self.assertEqual(self.summary['counts']['users'], 30)
        self.assertEqual(self.summary['counts']['communities'], 5)
        with self.store.connect() as db:
            rows = db.execute('SELECT schema_version,model,community,analysis FROM observations').fetchall()
            users = db.execute('SELECT email,display_name FROM users').fetchall()
        self.assertEqual(len(rows), 240)
        self.assertTrue(all(r['schema_version'].startswith(('demo-', 'synthetic-demo')) for r in rows))
        self.assertTrue(all(r['model'] == 'demo-simulated-v1' for r in rows))
        self.assertTrue(all(r['email'].endswith(('.test', '.invalid', '.example')) for r in users))
        for community in {r['community'] for r in rows}:
            self.assertEqual(sum(r['community'] == community for r in rows), 48)
        analyses = [json.loads(r['analysis']) for r in rows if r['analysis']]
        self.assertEqual({category for a in analyses for category in a['pollution_types']},
                         {'litter', 'suspected_industrial', 'oil_or_fuel', 'suspected_wastewater', 'unusual_water', 'other'})
        self.assertGreaterEqual(len({a['activity'] for a in analyses}), 6)
        self.assertEqual({a['observation_type'] for a in analyses}, {'moment', 'concern', 'uncertain'})

    def test_integrity_guest_waiver_and_no_redeemable_assets(self):
        with self.store.connect() as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertGreater(db.execute('SELECT COUNT(*) FROM observations WHERE user_id IS NULL').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM observations WHERE user_id IS NULL AND rewards_waived!=1').fetchone()[0], 0)
            self.assertEqual(db.execute('''SELECT COUNT(*) FROM points_ledger p JOIN observations o ON o.id=p.observation_id
                                          WHERE o.user_id IS NULL OR o.rewards_waived=1''').fetchone()[0], 0)
            for table in ('sessions', 'reward_vouchers', 'reward_redemptions'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reward_catalog WHERE enabled=1').fetchone()[0], 0)

    def test_generator_rerun_preserves_existing_rows(self):
        before = self.database_snapshot()
        repeated = generate_demo(self.db_path.parent)
        self.assertEqual(repeated['counts'], self.summary['counts'])
        self.assertEqual(self.database_snapshot(), before)

    def test_generator_refuses_live_directory(self):
        with self.assertRaises(ValueError):
            generate_demo(ROOT / 'data')

    def test_generator_refuses_unmarked_nonempty_directory(self):
        target = self.directory / 'unmarked'
        target.mkdir()
        existing = target / 'existing.txt'
        existing.write_text('Keep this data.', encoding='utf-8')
        with self.assertRaises(ValueError):
            generate_demo(target)
        self.assertEqual(existing.read_text(encoding='utf-8'), 'Keep this data.')
        self.assertEqual(list(target.iterdir()), [existing])

    def test_generator_refuses_real_database_even_with_directory_marker(self):
        target = self.directory / 'unmarked-database'
        target.mkdir()
        marker = '.coastkind-demo.json'
        (target / marker).write_bytes((self.db_path.parent / marker).read_bytes())
        database = target / self.db_path.name
        real_store = Store(database)
        with real_store.connect() as db:
            db.execute("CREATE TABLE keep_me(value TEXT)")
            db.execute("INSERT INTO keep_me VALUES ('untouched')")
        with self.assertRaises(ValueError):
            generate_demo(target)
        with real_store.connect() as db:
            self.assertEqual(db.execute('SELECT value FROM keep_me').fetchone()[0], 'untouched')
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='demo_metadata'").fetchone())

    def test_preview_refuses_unmarked_database(self):
        path = self.directory / 'unmarked-preview.sqlite3'
        Store(path)
        with self.assertRaises(ValueError):
            create_demo_server(0, path)

    def test_simulation_contains_repeated_reports_and_attention_signal(self):
        concerns = self.store.concerns()
        self.assertTrue(any(g['usable_photo_count'] >= 3 and g['usable_account_count'] >= 2
                            and g['usable_reporting_span_days'] >= 7 for g in concerns))
        self.assertTrue(self.store.alerts())
        with self.store.connect() as db:
            self.assertGreater(db.execute('SELECT COUNT(*) FROM observations WHERE duplicate_of IS NOT NULL').fetchone()[0], 0)
            self.assertGreater(db.execute("SELECT COUNT(*) FROM observations WHERE review_status='rejected'").fetchone()[0], 0)

    def test_simulated_rows_never_enter_operator_exports(self):
        self.assertEqual(export_records(self.store)['record_count'], 0)
        self.assertEqual(export_records(self.store, licensed=True)['cells'], [])

    def test_demo_sentinel_prevents_any_ai_request(self):
        with self.store.connect() as db:
            pending = db.execute("SELECT COUNT(*) FROM observations WHERE status='pending'").fetchone()[0]
        self.assertGreater(pending, 0, 'The guard must be checked with work pending.')
        analyzer = Mock(side_effect=AssertionError('Synthetic rows must not reach the provider.'))
        before = self.database_snapshot()
        self.assertFalse(process_next(self.store, 'fake-enabled-key', 'test-model', analyzer))
        analyzer.assert_not_called()
        self.assertEqual(self.database_snapshot(), before)

    def test_individual_synthetic_row_cannot_trigger_ai_in_an_unmarked_database(self):
        separate = Store(self.directory / 'copied-record.sqlite3')
        with self.store.connect() as db:
            copied = dict(db.execute("SELECT * FROM observations WHERE status='pending' AND duplicate_of IS NULL LIMIT 1").fetchone())
        copied.update(user_id=None, contributor_hash=None, rewards_waived=1)
        with separate.connect() as db:
            columns = list(copied)
            db.execute('INSERT INTO observations (' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')',
                       [copied[column] for column in columns])
        analyzer = Mock(side_effect=AssertionError('A copied synthetic row must not reach the provider.'))
        self.assertFalse(process_next(separate, 'fake-enabled-key', 'test-model', analyzer))
        analyzer.assert_not_called()
        with separate.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM observations').fetchone()[0], 'pending')

    def test_preview_health_and_dataset_are_marked_synthetic(self):
        status, raw, _ = self.request('/api/health')
        health = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertIs(health['demoMode'], True)
        self.assertIs(health['aiConfigured'], False)
        status, raw, _ = self.request('/api/demo-data')
        self.assertEqual(status, 200)
        data = json.loads(raw)
        self.assertTrue(data.get('synthetic') or data.get('demoMode'))
        self.assertIn('synthetic', raw.decode().lower())
        for private in ('password_hash', 'token_hash', 'csrf_token', 'voucher_code'):
            self.assertNotIn(private, raw.decode())
        status, raw, _ = self.request('/demo-data')
        self.assertEqual(status, 200)
        self.assertIn('synthetic', raw.decode().lower())

    def test_preview_disallows_writes_and_private_files(self):
        before = self.database_snapshot()
        for path in ('/api/observations', '/api/auth/register', '/api/auth/login', '/api/rewards/redeem'):
            with self.subTest(path=path):
                self.assertIn(self.request(path, 'POST')[0], (404, 405))
        for path in ('/data/coastkind.sqlite3', '/data/demo/coastkind-demo.sqlite3', '/.env', '/seed_demo.py',
                     '/../../server.py', '/api/rewards/mine', '/api/wallet'):
            with self.subTest(path=path):
                self.assertIn(self.request(path)[0], (401, 404, 405))
        self.assertEqual(self.database_snapshot(), before)

    def test_preview_ignores_account_cookie_and_rejects_untrusted_host(self):
        status, raw, headers = self.request('/api/auth/me', headers={'Cookie': 'coastkind_session=regular-app-session'})
        self.assertEqual(status, 200)
        self.assertIsNone(json.loads(raw)['user'])
        self.assertIsNone(headers.get('Set-Cookie'))
        self.assertEqual(self.request('/api/demo-data', headers={'Host': 'untrusted.example'})[0], 403)

    def test_static_presentation_packages_only_marked_public_data_and_relative_assets(self):
        result = build_presentation(self.presentation_directory, self.db_path)
        self.assertTrue(result['synthetic'])
        with zipfile.ZipFile(result['archive']) as package:
            names = package.namelist()
            self.assertTrue(all(not name.startswith('/') and '..' not in Path(name).parts for name in names))
            self.assertFalse(any(name.endswith(('.sqlite3', '.db', '.py')) or name == '.env' for name in names))
            data = json.loads(package.read('data/preview.json'))
            self.assertTrue(data['synthetic'])
            self.assertEqual(len(data['observations']), 240)
            for row in data['observations']:
                self.assertTrue(row['synthetic'])
                self.assertTrue(row['photo'].startswith('images/'))
                self.assertTrue(package.read(row['photo']).startswith(b'\xff\xd8\xff'))
            raw = package.read('data/preview.json').decode()
            for key in ('password_hash', 'token_hash', 'csrf_token', 'voucher_code'):
                self.assertNotIn(key, raw)
            index = package.read('index.html').decode()
            self.assertLess(index.index('src="static_demo.js"'), index.index('src="app.js"'))
            self.assertIn('SYNTHETIC DEMO', index)
            explorer = package.read('demo.html').decode()
            self.assertIn('data-demo-source="data/preview.json"', explorer)
            self.assertNotIn('href="/"', explorer)
            exported = json.loads(package.read('downloads/dataset.json'))
            self.assertNotIn('db_path', exported)
            self.assertNotIn('files', exported)
            self.assertEqual(len(exported['records']), 240)
        repeated = build_presentation(self.presentation_directory, self.db_path)
        self.assertEqual(repeated['files'], result['files'])

    def test_static_builder_rejects_unrelated_output_directory(self):
        with self.assertRaises(ValueError):
            build_presentation(ROOT / 'data', self.db_path)
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='presentation-tests-unmarked-') as directory:
            existing = Path(directory) / 'keep.txt'
            existing.write_text('existing material', encoding='utf-8')
            with self.assertRaises(ValueError):
                build_presentation(directory, self.db_path)
            self.assertEqual(existing.read_text(encoding='utf-8'), 'existing material')


if __name__ == '__main__':
    unittest.main()
