import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4
from PIL import Image
from server import Store, analyze, create_server, process_next, validate_analysis, validate_observation
from manage import export_records, review
from accounts import register


def payload(**changes):
    buffer = io.BytesIO()
    Image.new('RGB', (800, 600), '#456c83').save(buffer, 'PNG')
    data = {'id': str(uuid4()), 'community': 'Lyall Bay', 'feelings': 'A peaceful swim.',
            'photo': 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode(),
            'observed_at': '2026-01-02T03:00:00Z'}
    data.update(changes)
    return data


def analysis():
    return {'summary': 'A coastal scene with limited visible detail.', 'observation_type': 'moment',
            'pollution_types': [], 'visible_evidence': ['A blue surface.'],
            'reported_experience': 'The user reports a peaceful swim.', 'sentiment': 'positive',
            'activity': 'Swimming', 'uncertainties': ['Water safety cannot be assessed.'],
            'needs_human_review': False, 'image_relevant': True, 'text_image_consistency': 'consistent'}


class DataQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def add(self, **changes):
        data = validate_observation(payload(**changes))
        self.store.insert(data)
        return data

    def test_database_survives_restart_and_retries_are_idempotent(self):
        data = self.add()
        self.assertFalse(self.store.insert(data))
        reloaded = Store(self.store.path).list()
        self.assertEqual(len(reloaded), 1)
        self.assertEqual(reloaded[0]['feelings'], data['feelings'])
        self.assertEqual(reloaded[0]['status'], 'pending')
        with self.assertRaises(ValueError):
            self.store.insert({**data, 'feelings': 'Changed'})

    def test_missing_key_keeps_evidence_and_does_not_fabricate_analysis(self):
        self.add(observed_at=None)
        self.assertFalse(process_next(self.store, '', 'model'))
        record = self.store.list()[0]
        self.assertIsNone(record['analysis'])
        self.assertIn('missing_photo_date', record['quality_flags'])
        self.assertEqual(export_records(self.store)['record_count'], 0)

    def test_duplicate_photo_is_linked_and_not_analyzed_or_exported_twice(self):
        first = self.add()
        second = self.add(feelings='New words on the same photo')
        duplicate = next(r for r in self.store.list() if r['id'] == second['id'])
        self.assertEqual(duplicate['duplicate_of'], first['id'])
        self.assertEqual(duplicate['status'], 'duplicate')
        self.assertIn('duplicate_photo', duplicate['quality_flags'])

    def test_gps_requires_confirmation_and_is_coarsened_publicly(self):
        position = {'latitude': -41.329123, 'longitude': 174.796123, 'accuracy': 12}
        with self.assertRaises(ValueError):
            validate_observation(payload(position=position))
        self.add(position=position, location_confirmed=True)
        self.assertEqual(self.store.list()[0]['position']['latitude'], -41.33)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT latitude FROM observations').fetchone()[0], position['latitude'])

    def test_shared_concerns_count_photos_and_supporters_separately(self):
        first = validate_observation(payload(hint='concern'))
        first['contributor_hash'] = 'browser-a'
        self.store.insert(first)
        duplicate = validate_observation(payload(hint='concern'))
        duplicate['contributor_hash'] = 'browser-a'
        self.store.insert(duplicate)
        buffer = io.BytesIO()
        Image.new('RGB', (800, 600), '#006755').save(buffer, 'PNG')
        different = validate_observation(payload(hint='concern', photo='data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()))
        different['contributor_hash'] = 'browser-b'
        self.store.insert(different)
        with self.store.connect() as db:
            db.execute("UPDATE observations SET created='2026-01-01T00:00:00+00:00' WHERE id=?", (first['id'],))
            db.execute("UPDATE observations SET created='2026-01-21T00:00:00+00:00' WHERE id!=?", (first['id'],))
            for record in (first, different):
                db.execute('INSERT INTO support(observation_id,client_id) VALUES (?,?)', (record['id'], 'same-supporter'))
            db.execute('INSERT INTO comments(observation_id,author,body,created) VALUES (?,?,?,?)', (first['id'], 'Local', 'Still visible', '2026-01-22'))
        group = self.store.concerns()[0]
        self.assertEqual(group['report_count'], 3)
        self.assertEqual(group['distinct_photos'], 2)
        self.assertEqual(group['contributing_browsers'], 2)
        self.assertEqual(group['supporting_browsers'], 1)
        self.assertEqual(group['duplicates'], 1)
        self.assertEqual(group['followups'], 1)
        self.assertEqual(group['reporting_span_days'], 20)
        self.assertEqual(Store(self.store.path).concerns()[0]['report_count'], 3)
        self.assertEqual(export_records(self.store)['record_count'], 0)

    def test_conflicting_or_unrelated_ai_results_block_review(self):
        record = self.add()
        result = {**analysis(), 'text_image_consistency': 'conflicting'}
        process_next(self.store, 'fake-key', 'model', lambda *_: (result, 'test'))
        with self.assertRaises(ValueError):
            review(self.store, record['id'], 'approved', 'Reviewer', 'Review')

    def test_alerts_need_distinct_photos_multiple_accounts_and_time(self):
        users = [register(self.store, {'email': f'observer{index}@example.test', 'display_name': 'Observer',
                                      'password': 'A reliable coastal password 42!'}) for index in range(2)]
        ids = []
        for index in range(3):
            buffer = io.BytesIO()
            Image.new('RGB', (800,600), (40+index*30,100,140)).save(buffer,'PNG')
            data = validate_observation(payload(hint='concern',photo='data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()))
            data['user_id'] = users[index % 2]['id']
            data['rewards_waived'] = False
            self.store.insert(data)
            ids.append(data['id'])
        self.store.refresh_alerts()
        self.assertEqual(self.store.alerts(), [])
        with self.store.connect() as db:
            db.execute("UPDATE observations SET created='2026-01-01T00:00:00+00:00' WHERE id=?", (ids[0],))
        self.store.refresh_alerts()
        alerts = self.store.alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['status'], 'open')
        self.store.refresh_alerts()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM alert_events').fetchone()[0], 1)
        review(self.store, ids[0], 'rejected', 'Reviewer', 'Inconsistent evidence')
        self.assertEqual(self.store.alerts()[0]['status'], 'inactive')

    def test_licensed_export_requires_opt_in_review_and_minimum_group_size(self):
        for index in range(6):
            buffer = io.BytesIO()
            Image.new('RGB', (800,600), (index*30,70,130)).save(buffer,'PNG')
            record = self.add(photo='data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode(), dataset_consent=index<5)
            process_next(self.store,'fake-key','model',lambda *_:(analysis(),'response'))
            review(self.store,record['id'],'approved','Reviewer','Checked photo, time and location')
            if index<4:
                self.assertEqual(export_records(self.store,licensed=True)['cells'], [])
        result = export_records(self.store,licensed=True)
        self.assertEqual(result['cells'][0]['reviewed_observations'],5)
        self.assertEqual(set(result['cells'][0]), {'community','month','category','reviewed_observations'})
        self.assertNotIn('A peaceful swim', json.dumps(result))
        self.assertEqual(export_records(self.store)['record_count'],6)

    def test_points_are_reviewed_idempotent_reversible_and_not_tied_to_licensing(self):
        user = register(self.store, {'email': 'observer@example.test', 'display_name': 'Observer',
                                     'password': 'A reliable coastal password 42!'})
        record = validate_observation(payload(dataset_consent=False))
        record['user_id'] = user['id']
        record['rewards_waived'] = False
        self.store.insert(record)
        self.assertEqual(self.store.wallet(user['id'])['points'],0)
        process_next(self.store,'fake-key','model',lambda *_:(analysis(),'response'))
        self.assertEqual(self.store.wallet(user['id'])['points'],0)
        review(self.store,record['id'],'approved','Reviewer','Good evidence')
        self.assertEqual(self.store.wallet(user['id'])['points'],10)
        review(self.store,record['id'],'approved','Reviewer','Checked again')
        self.assertEqual(self.store.wallet(user['id'])['points'],10)
        self.assertEqual(len(self.store.wallet(user['id'])['entries']),1)
        review(self.store,record['id'],'rejected','Reviewer','Correcting an earlier review')
        self.assertEqual(self.store.wallet(user['id'])['points'],0)
        self.assertEqual(len(self.store.wallet(user['id'])['entries']),2)
        self.assertFalse(self.store.wallet(user['id'])['redemption_available'])
        self.assertEqual(self.store.wallet(str(uuid4()))['points'],0)

    def test_invalid_images_dates_locations_are_rejected(self):
        for change in [{'photo': 'data:image/jpeg;base64,/9j/AAAA'}, {'photo': 'https://example.com/photo'},
                       {'observed_at': '2999-01-01T00:00:00Z'}, {'observed_at': '2025-01-01'},
                       {'community': 'Unknown'}, {'feelings': 'a'*3001},
                       {'position': {'latitude': float('nan'), 'longitude': 174, 'accuracy': 1}, 'location_confirmed': True}]:
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                validate_observation(payload(**change))

    def test_quality_flags_for_small_image_and_wrong_location(self):
        data = self.add(position={'latitude':0, 'longitude':0, 'accuracy':800}, location_confirmed=True)
        self.assertIn('location_far_from_community', data['quality_flags'])
        self.assertIn('imprecise_gps', data['quality_flags'])

    def test_ai_success_requires_review_before_export(self):
        data = self.add()
        self.assertTrue(process_next(self.store, 'fake-key', 'test-model', lambda *_: (analysis(), 'test-response')))
        record = self.store.list()[0]
        self.assertEqual(record['status'], 'analyzed')
        self.assertTrue(record['analysis']['needs_human_review'])
        self.assertEqual(export_records(self.store)['record_count'], 0)
        review(self.store, data['id'], 'approved', 'Test reviewer', 'Reviewed evidence, date, and community location.')
        exported = export_records(self.store)
        self.assertEqual(exported['record_count'], 1)
        self.assertEqual(exported['records'][0]['location_precision'], 'community_only')
        self.assertEqual(exported['records'][0]['review']['reviewer'], 'Test reviewer')
        review(self.store, data['id'], 'rejected', 'Test reviewer', 'New information requires correction.')
        self.assertEqual(export_records(self.store)['record_count'], 0)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 2)

    def test_bad_analysis_and_provider_failure_preserve_original(self):
        data = self.add()
        def failed(*_):
            raise RuntimeError('provider failure')
        process_next(self.store, 'fake-key', 'test-model', failed)
        record = self.store.list()[0]
        self.assertEqual(record['status'], 'failed')
        self.assertIsNone(record['analysis'])
        self.assertEqual(record['feelings'], data['feelings'])
        with self.assertRaises(ValueError):
            validate_analysis({**analysis(), 'pollution_types': ['industrial_confirmed']})
        with self.assertRaises(ValueError):
            validate_analysis({**analysis(), 'pollution_types': ['litter']})

    def test_incomplete_conflicting_data_cannot_be_approved(self):
        data = self.add(observed_at=None)
        process_next(self.store, 'fake-key', 'test-model', lambda *_: (analysis(), 'test'))
        with self.assertRaises(ValueError):
            review(self.store, data['id'], 'approved', 'Reviewer', 'Review')
        self.assertEqual(export_records(self.store)['record_count'], 0)

    def test_provider_request_and_structured_response_contract(self):
        data = self.add()
        def opener(request, timeout):
            body = json.loads(request.data)
            self.assertEqual(request.full_url, 'https://api.openai.com/v1/responses')
            self.assertFalse(body['store'])
            self.assertTrue(body['text']['format']['strict'])
            self.assertEqual(body['input'][0]['content'][1]['type'], 'input_image')
            self.assertNotIn('latitude', body['input'][0]['content'][0]['text'])
            return io.BytesIO(json.dumps({'id':'mock-response','status':'completed', 'output':[{'content':[{'type':'output_text','text':json.dumps(analysis())}]}]}).encode())
        result, response = analyze(data, 'fake-key', 'test-model', opener)
        self.assertEqual(response, 'mock-response')
        self.assertTrue(result['needs_human_review'])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = create_server(0, Path(self.temp.name) / 'http.sqlite3')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.csrf = None

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        request_headers = {'Content-Type':'application/json', **(headers or {})}
        if data is not None and self.csrf:
            request_headers['X-CSRF-Token'] = self.csrf
        return self.opener.open(Request(self.base+path, data=json.dumps(data).encode() if data is not None else None,
                                       headers=request_headers), timeout=10)

    def test_real_http_upload_read_image_comments_and_support(self):
        data = payload(as_guest=True)
        with self.request('/api/observations', data) as result:
            self.assertEqual(result.status, 201)
        with self.request('/api/observations', data) as result:
            self.assertEqual(result.status, 200)
        with self.request(f'/api/observations/{data["id"]}/photo') as result:
            self.assertEqual(result.headers['Content-Type'], 'image/jpeg')
            self.assertGreater(len(result.read()), 100)
        with self.request('/api/auth/register', {'email':'local@example.test', 'display_name':'Local',
                                                'password':'A reliable coastal password 42!'}) as result:
            self.csrf = json.load(result)['csrfToken']
        with self.request(f'/api/observations/{data["id"]}/comments', {'body':'Follow-up'}):
            pass
        client = str(uuid4())
        for _ in range(2):
            with self.request(f'/api/observations/{data["id"]}/support', {'supported':True}, {'X-Client-ID':client}):
                pass
        with self.request('/api/observations', headers={'X-Client-ID':client}) as result:
            record = json.load(result)['observations'][0]
            self.assertEqual(record['comments'][0]['body'], 'Follow-up')
            self.assertEqual(record['likes'], 1)
            self.assertTrue(record['liked'])

    def test_database_and_secrets_are_not_served_and_foreign_origin_is_blocked(self):
        for path in ['/.env', '/data/wainet.sqlite3', '/server.py', '/../server.py']:
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.request(path)
            self.assertEqual(error.exception.code, 404)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/observations', payload(), {'Origin':'https://unrelated.example'})
        self.assertEqual(error.exception.code, 403)


if __name__ == '__main__':
    unittest.main()
