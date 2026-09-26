"""The official map reads verified source positions without triggering downloads."""
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import official_data
import official_water
from server import Store, create_server
from test_official_data import NOW, FeedOpener, feature


class OfficialMapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "map.sqlite3")

    def seed_public_cache(self):
        water = {**official_water.metadata("Lyall Bay"), "samples": [{"parameter": "Enterococci Bacteria",
            "value": "<4", "unit": "n/100ml", "sampled_at": (NOW - timedelta(days=2)).isoformat()}]}
        feed = FeedOpener({"type": "FeatureCollection", "features": [feature()]})
        with patch.object(official_water, "fetch", return_value=water):
            official_data.get(self.store, "Lyall Bay", now=NOW, opener=feed)

    def test_empty_map_never_fetches_or_writes_cache_and_uses_only_published_station_positions(self):
        with patch.object(official_data, "urlopen", side_effect=AssertionError("Map must not fetch.")), \
             patch.object(official_water, "fetch", side_effect=AssertionError("Map must not fetch.")):
            result = official_data.map_snapshot(self.store, now=NOW)
        self.assertEqual(len(result["communities"]), 20)
        self.assertEqual(len(result["water_stations"]), 18)
        self.assertEqual(result["earthquakes"], [])
        stations = {item["community"]: item for item in result["water_stations"]}
        self.assertNotIn("Mākara Beach", stations)
        self.assertNotIn("Houghton Bay", stations)
        self.assertEqual(stations["Lyall Bay"]["latitude"], -41.32832071)
        self.assertEqual(stations["Lyall Bay"]["longitude"], 174.80137072)
        self.assertEqual(stations["Lyall Bay"]["water"]["status"], "unavailable")
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM official_source_cache").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)

    def test_cached_map_deduplicates_events_and_keeps_official_coordinates_and_detection_limits(self):
        self.seed_public_cache()
        with patch.object(official_data, "urlopen", side_effect=AssertionError("Map must not fetch.")), \
             patch.object(official_water, "fetch", side_effect=AssertionError("Map must not fetch.")):
            result = official_data.map_snapshot(self.store, now=NOW)
        self.assertEqual(len(result["earthquakes"]), 1)
        quake = result["earthquakes"][0]
        self.assertEqual((quake["latitude"], quake["longitude"]), (-41.30, 174.80))
        self.assertEqual(quake["public_id"], "2026p123456")
        self.assertGreater(len(quake["nearby_communities"]), 1)
        water = next(item["water"] for item in result["water_stations"] if item["community"] == "Lyall Bay")
        self.assertEqual(water["samples"][0]["value"], "<4")
        self.assertEqual(water["retrieved_at"], NOW.isoformat())

    def test_snapshot_fallback_is_offline_and_stale_without_false_missing_data_error(self):
        self.seed_public_cache()
        snapshot = official_data.export_snapshot(self.store, refresh=False, now=NOW)
        empty_store = Store(Path(self.temp.name) / "empty.sqlite3")
        current = NOW + timedelta(minutes=16)
        with patch.object(official_data, "urlopen", side_effect=AssertionError("Offline map must not fetch.")):
            cached_map = official_data.map_snapshot(empty_store, snapshot=snapshot, now=current)
            offline_map = official_data.offline_map(snapshot, now=current)
        self.assertEqual(len(cached_map["earthquakes"]), 1)
        self.assertEqual(cached_map["earthquakes"], offline_map["earthquakes"])
        source = next(item for item in cached_map["communities"] if item["community"] == "Lyall Bay")
        self.assertEqual(source["water"]["status"], "stale")
        self.assertEqual(source["geonet"]["status"], "stale")
        self.assertNotIn("error", source["water"])
        self.assertNotIn("error", source["geonet"])
        self.assertEqual(official_data.offline_map(snapshot, now=NOW + timedelta(days=31))["earthquakes"], [])

    def test_http_map_is_public_but_rejects_query_and_untrusted_host_without_network_fetch(self):
        server = create_server(0, Path(self.temp.name) / "http.sqlite3")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with patch.object(official_data, "urlopen", side_effect=AssertionError("Map must not fetch.")), \
                 patch.object(official_water, "fetch", side_effect=AssertionError("Map must not fetch.")):
                with urlopen(base + "/api/official-map", timeout=5) as response:
                    result = json.load(response)
                self.assertEqual(result["kind"], "official-map-v1")
                self.assertEqual(len(result["water_stations"]), 18)
                for suffix, headers, expected in [("?url=https://example.com", {}, 400), ("", {"Host": "evil.example"}, 403)]:
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(Request(base + "/api/official-map" + suffix, headers=headers), timeout=5)
                    self.assertEqual(raised.exception.code, expected)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
