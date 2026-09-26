"""Official-source ingestion checks with mocked GeoNet responses and temporary storage."""
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape, quoteattr

import official_data
import official_water
from server import COMMUNITIES, Store, create_server


NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)


def feature(identifier='2026p123456', hours=1, longitude=174.80, latitude=-41.30, **properties):
    return {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [longitude, latitude]},
            'properties': {'publicID': identifier, 'time': (NOW - timedelta(hours=hours)).isoformat(),
                           'magnitude': 4.2, 'depth': 12.5, 'locality': 'Near Wellington',
                           'quality': 'best', 'mmi': 4, **properties}}


class FeedOpener:
    def __init__(self, value):
        self.value = value
        self.calls = []
        self.read_sizes = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.value, Exception):
            raise self.value
        raw = self.value if isinstance(self.value, bytes) else json.dumps(self.value).encode()
        owner = self

        class Response(io.BytesIO):
            def read(self, amount=-1):
                owner.read_sizes.append(amount)
                return super().read(amount)

        return Response(raw)


def water_xml(station='Lyall Bay at Tirangi Road', unit='n/100ml', parameter='Enterococci Bacteria',
              sampled=None, value='<1', forecast=False):
    sampled = sampled or (NOW - timedelta(days=2)).isoformat()
    series = 'ForecastTimeseries' if forecast else 'MeasurementTimeseries'
    return f'''<wml2:Collection xmlns:wml2="http://www.opengis.net/waterml/2.0"
        xmlns:om="http://www.opengis.net/om/2.0" xmlns:xlink="http://www.w3.org/1999/xlink">
      <wml2:observationMember><om:OM_Observation>
        <om:featureOfInterest xlink:title={quoteattr(station)}/>
        <om:observedProperty xlink:title={quoteattr(parameter)}/>
        <om:resultTime>{NOW.isoformat()}</om:resultTime>
        <om:result><wml2:{series}>
          <wml2:defaultPointMetadata><wml2:DefaultTVPMeasurementMetadata>
            <wml2:uom code={quoteattr(unit)}/>
          </wml2:DefaultTVPMeasurementMetadata></wml2:defaultPointMetadata>
          <wml2:point><wml2:MeasurementTVP>
            <wml2:time>{escape(sampled)}</wml2:time><wml2:value>{escape(value)}</wml2:value>
          </wml2:MeasurementTVP></wml2:point>
        </wml2:{series}></om:result>
      </om:OM_Observation></wml2:observationMember>
    </wml2:Collection>'''.encode()


class OfficialDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'official-test.sqlite3')
        official_data.initialize(self.store)
        water_patch = patch.object(official_water, 'fetch', side_effect=OSError('Separate water provider test fixture.'))
        water_patch.start()
        self.addCleanup(water_patch.stop)

    def tearDown(self):
        self.temp.cleanup()

    def get(self, opener, community='Lyall Bay', now=NOW, refresh=True):
        return official_data.get(self.store, community, opener=opener, now=now, refresh=refresh)

    def test_events_keep_official_attribution_time_and_units_with_fixed_bounded_request(self):
        opener = FeedOpener({'type': 'FeatureCollection', 'features': [feature()]})
        result = self.get(opener)
        source = result['geonet']
        self.assertEqual(source['status'], 'available')
        self.assertEqual(source['source_name'], 'GeoNet')
        self.assertEqual(source['source_url'], 'https://www.geonet.org.nz')
        self.assertEqual(datetime.fromisoformat(source['retrieved_at']), NOW)
        self.assertEqual(len(source['events']), 1)
        event = source['events'][0]
        self.assertEqual(event['public_id'], '2026p123456')
        self.assertEqual(datetime.fromisoformat(event['time']), NOW - timedelta(hours=1))
        self.assertEqual(event['magnitude'], 4.2)
        self.assertEqual(event['depth_km'], 12.5)
        self.assertGreaterEqual(event['distance_km'], 0)
        self.assertLess(event['distance_km'], 10)
        self.assertIn('geonet.org.nz', event['url'])
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, 'https://api.geonet.org.nz/quake?MMI=3')
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.get_header('Accept'), 'application/vnd.geo+json;version=2')
        self.assertIsNone(request.get_header('Authorization'))
        self.assertLessEqual(timeout, 8)
        self.assertTrue(opener.read_sizes)
        self.assertTrue(all(0 < size <= 2 * 1024 * 1024 + 1 for size in opener.read_sizes))

    def test_old_future_far_deleted_and_invalid_geo_events_are_excluded_and_latest_five_are_sorted(self):
        invalid_geometry = feature('2026p900006')
        invalid_geometry['geometry']['type'] = 'LineString'
        malformed_time = feature('2026p900007', time='2026-09-26')
        excluded = [feature('2026p900000', hours=24 * 31), feature('2026p900001', hours=-1),
                    feature('2026p900002', latitude=-36.85, longitude=174.76), feature('2026p900003', latitude=174.80, longitude=-41.30),
                    feature('2026p900004', latitude=float('nan')), feature('2026p900005', quality='deleted'),
                    invalid_geometry, malformed_time]
        recent = [feature(f'2026p12345{index}', hours=index + 1) for index in range(7)]
        opener = FeedOpener({'type': 'FeatureCollection', 'features': excluded + list(reversed(recent))})
        result = self.get(opener)['geonet']
        self.assertEqual(result['status'], 'available')
        self.assertEqual([event['public_id'] for event in result['events']], [f'2026p12345{index}' for index in range(5)])
        for index in range(8):
            self.assertNotIn(f'2026p90000{index}', json.dumps(result['events']))

    def test_cache_is_shared_across_communities_persistent_and_failure_cooldown_uses_stale_data(self):
        opener = FeedOpener({'type': 'FeatureCollection', 'features': [feature()]})
        first = self.get(opener)
        self.get(opener, 'Oriental Bay', now=NOW + timedelta(minutes=5))
        snapshot = official_data.export_snapshot(self.store, opener=opener, now=NOW + timedelta(minutes=5))
        self.assertEqual(len(snapshot['communities']), len(COMMUNITIES))
        self.assertEqual(len(opener.calls), 1)
        reloaded = Store(self.store.path)
        offline = FeedOpener(OSError('PRIVATE NETWORK ERROR must not reach users'))
        cached = official_data.get(reloaded, 'Lyall Bay', opener=offline, now=NOW + timedelta(minutes=10), refresh=False)
        self.assertEqual(cached['geonet']['events'], first['geonet']['events'])
        self.assertEqual(len(offline.calls), 0)
        stale = self.get(offline, now=NOW + timedelta(minutes=16))
        self.assertEqual(stale['geonet']['status'], 'stale')
        self.assertEqual(stale['geonet']['retrieved_at'], first['geonet']['retrieved_at'])
        self.assertEqual(stale['geonet']['events'], first['geonet']['events'])
        self.assertNotIn('PRIVATE NETWORK ERROR', json.dumps(stale))
        self.get(offline, 'Island Bay', now=NOW + timedelta(minutes=17))
        self.assertEqual(len(offline.calls), 1)

    def test_cold_failure_malformed_and_oversized_payloads_never_create_observations(self):
        cases = [OSError('private provider details'), b'not json', {'type': 'FeatureCollection', 'features': 'wrong type'},
                 {'type': 'Unexpected', 'features': []}, b'x' * (2 * 1024 * 1024 + 2)]
        for index, value in enumerate(cases):
            with self.subTest(case=index):
                store = Store(Path(self.temp.name) / f'invalid-{index}.sqlite3')
                official_data.initialize(store)
                opener = FeedOpener(value)
                result = official_data.get(store, 'Lyall Bay', now=NOW, opener=opener)
                self.assertEqual(result['geonet']['status'], 'unavailable')
                self.assertEqual(result['geonet']['events'], [])
                self.assertNotIn('private provider details', json.dumps(result))
                self.assertEqual(len(opener.calls), 1)
                with store.connect() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 0)

    def test_empty_earthquake_feed_is_not_a_water_safety_or_green_status(self):
        opener = FeedOpener({'type': 'FeatureCollection', 'features': []})
        result = self.get(opener)
        self.assertEqual(result['geonet']['status'], 'available')
        self.assertEqual(result['geonet']['events'], [])
        for value in result.values():
            if isinstance(value, dict):
                self.assertNotIn(value.get('status'), ('green', 'safe', 'safe_to_swim'))
        self.assertNotEqual(result.get('status'), 'safe')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 0)

    def test_public_http_validates_community_query_and_host_without_external_requests(self):
        self.get(FeedOpener({'type': 'FeatureCollection', 'features': [feature()]}))
        server = create_server(0, self.store.path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original_get = official_data.get

        def cached_only(store, community, **_kwargs):
            return original_get(store, community, refresh=False, now=NOW, opener=FeedOpener(AssertionError('External network is forbidden in this test.')))

        def request(path, headers=None):
            try:
                response = urlopen(Request(f'http://127.0.0.1:{server.server_port}' + path, headers=headers or {}), timeout=5)
            except HTTPError as error:
                response = error
            with response:
                return response.status, response.read()

        try:
            with patch.object(official_data, 'get', side_effect=cached_only):
                status, raw = request('/api/official-data?community=Lyall+Bay')
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(raw)['geonet']['events'][0]['public_id'], '2026p123456')
                for path in ('/api/official-data', '/api/official-data?community=Unknown',
                             '/api/official-data?community=Lyall+Bay&community=Island+Bay',
                             '/api/official-data?community=Lyall+Bay&url=https%3A%2F%2Fevil.example'):
                    self.assertEqual(request(path)[0], 400)
                self.assertEqual(request('/api/official-data?community=Lyall+Bay', {'Host': 'evil.example'})[0], 403)
                for private in ('password_hash', 'csrfToken', 'token_hash', 'user_id', 'voucher_code'):
                    self.assertNotIn(private, raw.decode())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class OfficialWaterTests(unittest.TestCase):
    def test_sample_preserves_station_parameter_units_qualifier_and_sampling_time(self):
        data = official_water.parse(water_xml(), 'Lyall Bay', now=NOW)
        self.assertEqual(data['source_name'], 'Greater Wellington')
        self.assertIn('gw.govt.nz', data['source_url'])
        self.assertIn('lawa.org.nz', data['lawa_url'])
        self.assertEqual(data['station_name'], 'Lyall Bay at Tirangi Road')
        self.assertEqual(len(data['samples']), 1)
        sample = data['samples'][0]
        self.assertEqual(sample['parameter'], 'Enterococci Bacteria')
        self.assertEqual(sample['value_text'], '<1')
        self.assertEqual(sample['qualifier'], '<')
        self.assertEqual(sample['unit'], 'n/100ml')
        self.assertEqual(datetime.fromisoformat(sample['sampled_at']), NOW - timedelta(days=2))
        self.assertNotEqual(sample['sampled_at'], NOW.isoformat())
        self.assertNotIn('risk_level', data)
        self.assertNotIn('safe', data)

    def test_wrong_station_units_forecasts_dates_and_unsafe_xml_are_rejected(self):
        invalid = [water_xml(station='Island Bay at Surf Club'), water_xml(unit='mg/L'),
                   water_xml(parameter='Temperature'), water_xml(forecast=True),
                   water_xml(sampled=(NOW + timedelta(hours=1)).isoformat()),
                   water_xml(sampled='2026-09-24T12:00:00'), water_xml(value='NaN'),
                   b'<!DOCTYPE fake [<!ENTITY x "unsafe">]>' + water_xml(),
                   b'x' * (2 * 1024 * 1024 + 1)]
        for index, raw in enumerate(invalid):
            with self.subTest(case=index), self.assertRaises(ValueError):
                official_water.parse(raw, 'Lyall Bay', now=NOW)
        self.assertEqual(set(official_water.STATIONS), set(COMMUNITIES))
        unknown_coordinates = official_water.metadata('Houghton Bay')
        self.assertIsNone(unknown_coordinates['latitude'])
        self.assertIsNone(unknown_coordinates['longitude'])
        self.assertIsNone(unknown_coordinates['station_distance_km'])

    def test_water_fetch_has_fixed_station_query_bounded_response_and_rejects_redirects(self):
        opener = FeedOpener(water_xml())
        result = official_water.fetch('Lyall Bay', opener=opener, now=NOW)
        self.assertEqual(len(result['samples']), 1)
        request, timeout = opener.calls[0]
        parsed = urlsplit(request.full_url)
        self.assertEqual(parsed.scheme, 'https')
        self.assertEqual(parsed.netloc, 'hilltop.gw.govt.nz')
        self.assertEqual(parse_qs(parsed.query)['FeatureOfInterest'], ['Lyall Bay at Tirangi Road'])
        self.assertEqual(parse_qs(parsed.query)['ObservedProperty'], ['Enterococci Bacteria'])
        self.assertEqual(parse_qs(parsed.query)['Request'], ['GetObservation'])
        self.assertIsNone(request.get_header('Authorization'))
        self.assertLessEqual(timeout, 8)
        self.assertEqual(opener.read_sizes, [2 * 1024 * 1024 + 1])

        class Redirect(io.BytesIO):
            def geturl(self):
                return 'https://untrusted.example/fake-monitoring'

        with self.assertRaises(ValueError):
            official_water.fetch('Lyall Bay', opener=lambda *_args, **_kwargs: Redirect(water_xml()), now=NOW)

    def test_water_fetch_time_and_sample_time_stay_distinct_and_failed_refresh_is_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'combined-official.sqlite3')
            geonet = FeedOpener({'type': 'FeatureCollection', 'features': []})
            water = FeedOpener(water_xml())

            def sources(request, timeout):
                return (geonet if urlsplit(request.full_url).netloc == 'api.geonet.org.nz' else water)(request, timeout)

            first = official_data.get(store, 'Lyall Bay', now=NOW, opener=sources)
            self.assertEqual(first['water']['status'], 'available')
            self.assertEqual(first['water']['retrieved_at'], NOW.isoformat())
            self.assertEqual(first['water']['samples'][0]['sampled_at'], (NOW - timedelta(days=2)).isoformat())
            self.assertEqual(len(water.calls), 1)
            failure = FeedOpener(OSError('Do not expose source error details'))
            refreshed = official_data.get(store, 'Lyall Bay', now=NOW + timedelta(minutes=16), opener=failure)
            self.assertEqual(refreshed['water']['status'], 'stale')
            self.assertEqual(refreshed['water']['samples'], first['water']['samples'])
            self.assertEqual(refreshed['water']['retrieved_at'], NOW.isoformat())
            self.assertNotIn('Do not expose source error details', json.dumps(refreshed))
            self.assertNotIn('green', (refreshed['water'].get('status'), refreshed['geonet'].get('status')))


if __name__ == '__main__':
    unittest.main()
