"""Map colours summarize eligible community evidence, never certified water safety."""
import base64
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import urlopen
from uuid import uuid4

from PIL import Image

from community_status import generate
from demo_server import create_demo_server
from manage import review
from server import COMMUNITIES, Store, validate_observation, create_server
from test_server import analysis, payload


class CommunityStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'map-status.sqlite3')
        self.anchor = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
        self.sequence = 0
        self.users = [str(uuid4()), str(uuid4())]
        with self.store.connect() as db:
            for index, identifier in enumerate(self.users):
                db.execute('''INSERT INTO users(id,email,display_name,password_hash,created_at)
                              VALUES(?,?,?,?,?)''', (identifier, f'private-map-{index}@example.test',
                                                    'Private map contributor', 'test-fixture-not-a-credential', self.anchor.isoformat()))

    def tearDown(self):
        self.temp.cleanup()

    def add(self, days=0, concern=False, category='litter', user=None, approved=True, community='Lyall Bay', **changes):
        self.sequence += 1
        buffer = io.BytesIO()
        Image.new('RGB', (800, 600), (self.sequence * 31 % 256, 113, 171)).save(buffer, 'PNG')
        observed = (self.anchor - timedelta(days=days)).isoformat()
        data = validate_observation(payload(community=community, observed_at=observed,
                                           hint='concern' if concern else 'moment',
                                           photo='data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode(), **changes))
        data.update(user_id=user, rewards_waived=user is None)
        self.store.insert(data)
        result = analysis()
        if concern:
            result.update(observation_type='concern', pollution_types=[category], sentiment='negative',
                          summary='Visible litter in a synthetic test image.')
        with self.store.connect() as db:
            db.execute("UPDATE observations SET created=?,status='analyzed',analysis=?,model='test-model' WHERE id=?",
                       (observed, json.dumps(result), data['id']))
        if approved:
            review(self.store, data['id'], 'approved', 'Synthetic fixture reviewer', 'Checked fixture evidence and date.')
        return data['id']

    def status(self, community='Lyall Bay', **kwargs):
        result = generate(self.store, now=self.anchor, **kwargs)
        return next(item for item in result['communities'] if item['community'] == community)

    def update(self, identifier, **values):
        with self.store.connect() as db:
            db.execute('UPDATE observations SET ' + ','.join(key + '=?' for key in values) + ' WHERE id=?',
                       [*values.values(), identifier])

    def test_empty_communities_are_gray_and_public_endpoint_exposes_no_identity_or_gps(self):
        result = generate(self.store, now=self.anchor)
        self.assertEqual({item['community'] for item in result['communities']}, set(COMMUNITIES))
        self.assertTrue(all(item['status'] == 'gray' for item in result['communities']))
        self.assertEqual(result['window_days'], 30)
        self.assertEqual(result['basis'], 'community_observations')
        self.add(user=self.users[0], position={'latitude': -41.329123, 'longitude': 174.796123, 'accuracy': 15}, location_confirmed=True)
        server = create_server(0, self.store.path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f'http://127.0.0.1:{server.server_port}/api/community-status', timeout=5) as response:
                self.assertEqual(response.status, 200)
                raw = response.read().decode()
            public = json.loads(raw)
            self.assertFalse(public['synthetic'])
            self.assertEqual(next(item['status'] for item in public['communities'] if item['community'] == 'Lyall Bay'), 'green')
            for private in [*self.users, 'private-map-', 'Private map contributor', 'test-fixture-not-a-credential',
                            '-41.329123', '174.796123', 'password_hash', 'user_id', 'latitude', 'longitude']:
                self.assertNotIn(private, raw)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_green_requires_positive_analysis_and_latest_human_approval(self):
        identifier = self.add(approved=False)
        self.assertEqual(self.status()['status'], 'orange')
        self.update(identifier, status='pending', analysis=None)
        self.assertEqual(self.status()['status'], 'orange')
        self.update(identifier, status='analyzed', analysis=json.dumps(analysis()))
        review(self.store, identifier, 'approved', 'Fixture reviewer', 'Reviewed the positive observation.')
        self.assertEqual(self.status()['status'], 'green')
        with self.store.connect() as db:
            db.execute('DELETE FROM reviews WHERE observation_id=?', (identifier,))
        self.assertNotEqual(self.status()['status'], 'green')
        review(self.store, identifier, 'approved', 'Fixture reviewer', 'Approved with a new audit entry.')
        review(self.store, identifier, 'rejected', 'Fixture reviewer', 'Subsequent evidence changes the review.')
        self.update(identifier, review_status='approved')
        self.assertNotEqual(self.status()['status'], 'green')

    def test_excluded_uncertain_and_incomplete_records_cannot_turn_map_green(self):
        identifier = self.add()
        with self.store.connect() as db:
            original = dict(db.execute('SELECT * FROM observations WHERE id=?', (identifier,)).fetchone())
        cases = [
            ({'review_status': 'rejected'}, 'gray'),
            ({'duplicate_of': identifier, 'quality_flags': '["duplicate_photo"]'}, 'gray'),
            ({'observed_at': (self.anchor - timedelta(days=31)).isoformat()}, 'gray'),
            ({'observed_at': (self.anchor + timedelta(days=1)).isoformat()}, 'gray'),
            ({'observed_at': 'not-a-date'}, 'gray'),
            ({'quality_flags': '["low_resolution"]'}, 'gray'),
            ({'quality_flags': '["imprecise_gps"]'}, 'gray'),
            ({'quality_flags': '["location_far_from_community"]'}, 'gray'),
            ({'analysis': json.dumps({**analysis(), 'image_relevant': False})}, 'gray'),
            ({'analysis': json.dumps({**analysis(), 'text_image_consistency': 'conflicting'})}, 'orange'),
            ({'analysis': json.dumps({**analysis(), 'observation_type': 'uncertain'})}, 'orange'),
            ({'observed_at': None}, 'orange'),
            ({'status': 'failed', 'analysis': None}, 'orange'),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                self.update(identifier, **changes)
                try:
                    self.assertEqual(self.status()['status'], expected)
                finally:
                    self.update(identifier, **{key: original[key] for key in changes})

    def test_unreviewed_concern_prevents_green_despite_positive_reviewed_observation(self):
        self.add()
        negative = self.add(concern=True, approved=False)
        self.update(negative, status='pending', analysis=None)
        self.assertEqual(self.status()['status'], 'orange')
        self.assertGreaterEqual(self.status()['counts']['unresolved_observations'], 1)
        review(self.store, negative, 'rejected', 'Fixture reviewer', 'The reported issue does not match the evidence.')
        self.assertEqual(self.status()['status'], 'green')

    def test_red_requires_reviewed_distinct_photos_two_accounts_and_seven_day_span(self):
        records = [self.add(days=10, concern=True, user=self.users[0]),
                   self.add(days=5, concern=True, user=self.users[1]),
                   self.add(days=0, concern=True, user=self.users[0])]
        signal = self.status()
        self.assertEqual(signal['status'], 'red')
        self.assertEqual(signal['counts']['repeated_concern_photos'], 3)
        self.assertEqual(signal['counts']['repeated_concern_accounts'], 2)
        with self.store.connect() as db:
            originals = {row['id']: dict(row) for row in db.execute('SELECT * FROM observations')}
        cases = [(records[1], {'user_id': self.users[0]}),
                 (records[1], {'user_id': None}),
                 (records[2], {'image_hash': originals[records[0]]['image_hash']}),
                 (records[2], {'review_status': 'pending'}),
                 (records[2], {'quality_flags': '["low_resolution"]'}),
                 (records[2], {'duplicate_of': records[0]}),
                 (records[0], {'observed_at': (self.anchor - timedelta(days=6)).isoformat()}),
                 (records[2], {'analysis': json.dumps({**analysis(), 'observation_type': 'concern',
                                                      'pollution_types': ['oil_or_fuel'], 'sentiment': 'negative'})})]
        for identifier, changes in cases:
            with self.subTest(changes=changes):
                self.update(identifier, **changes)
                try:
                    self.assertNotEqual(self.status()['status'], 'red')
                finally:
                    self.update(identifier, **{key: originals[identifier][key] for key in changes})
        with self.store.connect() as db:
            db.execute('DELETE FROM reviews WHERE observation_id=?', (records[2],))
        self.assertNotEqual(self.status()['status'], 'red')

    def test_demo_uses_explicit_historical_anchor_without_colouring_real_map(self):
        identifier = self.add(days=300)
        historic = (self.anchor - timedelta(days=300)).isoformat()
        self.update(identifier, schema_version='synthetic-demo-status-test', model='demo-simulated-v1')
        self.assertEqual(self.status()['status'], 'gray')
        demo = generate(self.store, now=self.anchor, synthetic=True)
        self.assertTrue(demo['synthetic'])
        self.assertEqual(datetime.fromisoformat(demo['as_of']), datetime.fromisoformat(historic))
        self.assertEqual(next(item['status'] for item in demo['communities'] if item['community'] == 'Lyall Bay'), 'green')
        with self.store.connect() as db:
            db.execute('CREATE TABLE demo_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute("INSERT INTO demo_metadata VALUES('dataset_kind','synthetic-demo-v1')")
        server = create_demo_server(0, self.store.path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f'http://127.0.0.1:{server.server_port}/api/community-status', timeout=5) as response:
                data = json.load(response)
            self.assertTrue(data['synthetic'])
            self.assertEqual(next(item['status'] for item in data['communities'] if item['community'] == 'Lyall Bay'), 'green')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.add(community='Oriental Bay')
        self.assertEqual(self.status('Oriental Bay')['status'], 'green')
        self.assertEqual(self.status('Oriental Bay', synthetic=True)['status'], 'gray')


if __name__ == '__main__':
    unittest.main()
