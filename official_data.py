"""Cached public monitoring data, stored separately from community observations."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import tempfile
import threading
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

import official_water

GEONET_URL = "https://api.geonet.org.nz/quake?MMI=3"
CACHE_TTL_SECONDS = 15 * 60
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = 8
_LOCK_CREATION = threading.Lock()
_FETCH_LOCKS = {}
GEONET_NOTICE = ("Latest available GeoNet events within 100 km and the last 30 days, limited to five. "
                 "The national feed contains at most 100 possibly felt earthquakes (MMI 3 or greater), "
                 "so this is not a complete earthquake catalogue. Earthquake information is not a water-quality "
                 "measurement or a tsunami warning. Source: GeoNet, CC BY 3.0 NZ.")
WATER_FIELDS = {"source_name", "source_url", "station_name", "station_url", "lawa_url", "station_distance_km",
                "station_latitude", "station_longitude", "samples", "notice", "attribution", "documentation_url"}


def initialize(store):
    with store.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS official_source_cache (
            provider TEXT PRIMARY KEY,
            payload TEXT,
            fetched_at TEXT,
            last_error TEXT,
            last_attempt_at TEXT
        )""")


def _time(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (AttributeError, ValueError, TypeError, OverflowError):
        return None


def _now(value):
    parsed = datetime.now(timezone.utc) if value is None else _time(value)
    if parsed is None:
        raise ValueError("A timestamp with timezone is required.")
    return parsed


def _communities():
    from server import COMMUNITY_POINTS
    return COMMUNITY_POINTS


def _community(value):
    if not isinstance(value, str) or value not in _communities():
        raise ValueError("Choose a valid coastal community.")
    return value


def _provider_lock(provider):
    if provider != "geonet" and (not provider.startswith("water:") or provider[6:] not in _communities()):
        raise ValueError("Unknown official source.")
    # At most one GeoNet key and one water key per supported community.
    with _LOCK_CREATION:
        return _FETCH_LOCKS.setdefault(provider, threading.Lock())


def _number(value, minimum, maximum):
    return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum


def distance_km(latitude, longitude, other_latitude, other_longitude):
    lat1, lat2 = math.radians(latitude), math.radians(other_latitude)
    dlat, dlon = lat2 - lat1, math.radians(other_longitude - longitude)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 12742 * math.asin(math.sqrt(min(1, max(0, a))))


def parse_geonet(payload, now=None):
    current = _now(now)
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise ValueError("GeoNet returned an invalid earthquake feed.")
    if len(payload["features"]) > 1000:
        raise ValueError("GeoNet returned an oversized earthquake list.")
    events, seen = [], set()
    for feature in payload["features"]:
        if not isinstance(feature, dict):
            continue
        props, geometry = feature.get("properties"), feature.get("geometry")
        if not isinstance(props, dict) or not isinstance(geometry, dict) or geometry.get("type") != "Point":
            continue
        coords = geometry.get("coordinates")
        if not isinstance(coords, list) or len(coords) < 2 or not _number(coords[0], -180, 180) or not _number(coords[1], -90, 90):
            continue
        public_id, quality = props.get("publicID"), props.get("quality")
        observed = _time(props.get("time"))
        if (not isinstance(public_id, str) or not re.fullmatch(r"[0-9]{4}p[0-9]{6}", public_id)
                or public_id in seen or not isinstance(quality, str) or quality not in {"best", "preliminary", "automatic"}
                or observed is None or observed > current or observed < current - timedelta(days=365)
                or not _number(props.get("magnitude"), -3, 10) or not _number(props.get("depth"), 0, 1000)):
            continue
        seen.add(public_id)
        locality = props.get("locality")
        events.append({"public_id": public_id, "time": observed.isoformat(), "magnitude": props["magnitude"],
                       "depth_km": props["depth"], "latitude": coords[1], "longitude": coords[0],
                       "locality": locality[:300] if isinstance(locality, str) else "", "quality": quality,
                       "url": f"https://www.geonet.org.nz/earthquake/{public_id}"})
    return {"events": sorted(events, key=lambda event: event["time"], reverse=True)}


def fetch_geonet(opener=None, now=None):
    opener = opener or urlopen
    request = Request(GEONET_URL, headers={"Accept": "application/vnd.geo+json;version=2",
                                        "User-Agent": "Coastkind/1.0 (public coastal observations)"})
    with opener(request, timeout=REQUEST_TIMEOUT) as response:
        if callable(getattr(response, "geturl", None)):
            destination = urlsplit(response.geturl())
            if destination.scheme != "https" or destination.netloc != "api.geonet.org.nz" or destination.path != "/quake":
                raise ValueError("GeoNet redirected to an unexpected source.")
        raw = response.read(MAX_PAYLOAD_BYTES + 1)
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ValueError("The official response exceeded the size limit.")
    return parse_geonet(json.loads(raw), now)


def _read_cache(store, provider):
    with store.connect() as db:
        row = db.execute("SELECT * FROM official_source_cache WHERE provider=?", (provider,)).fetchone()
    return dict(row) if row else {"provider": provider, "payload": None, "fetched_at": None, "last_error": None, "last_attempt_at": None}


def _fresh(value, current):
    parsed = _time(value)
    return parsed is not None and 0 <= (current - parsed).total_seconds() < CACHE_TTL_SECONDS


def _cached(store, provider, fetcher, refresh, current):
    lock = _provider_lock(provider)
    # Read-only map requests may use the previous snapshot while a refresh runs.
    with lock if refresh else nullcontext():
        cached = _read_cache(store, provider)
        if refresh and not _fresh(cached["last_attempt_at"], current):
            try:
                payload = fetcher()
                serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
                if len(serialized.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                    raise ValueError("The normalized source response exceeded the size limit.")
                with store.connect() as db:
                    db.execute("""INSERT INTO official_source_cache(provider,payload,fetched_at,last_error,last_attempt_at)
                        VALUES(?,?,?,NULL,?) ON CONFLICT(provider) DO UPDATE SET payload=excluded.payload,
                        fetched_at=excluded.fetched_at,last_error=NULL,last_attempt_at=excluded.last_attempt_at""",
                               (provider, serialized, current.isoformat(), current.isoformat()))
            except Exception:
                # Retain the last good data and never return provider error bodies or stack traces.
                with store.connect() as db:
                    db.execute("""INSERT INTO official_source_cache(provider,last_error,last_attempt_at)
                        VALUES(?,?,?) ON CONFLICT(provider) DO UPDATE SET last_error=excluded.last_error,last_attempt_at=excluded.last_attempt_at""",
                               (provider, "The official source could not be refreshed.", current.isoformat()))
            cached = _read_cache(store, provider)
        try:
            payload = json.loads(cached["payload"]) if cached["payload"] else None
        except (ValueError, TypeError):
            payload = None
        status = "unavailable" if payload is None else "available" if _fresh(cached["fetched_at"], current) and not cached["last_error"] else "stale"
        metadata = {"status": status, "retrieved_at": cached["fetched_at"], "last_attempt_at": cached["last_attempt_at"]}
        if cached["last_error"]:
            metadata["error"] = cached["last_error"]
        return payload, metadata


def _geonet_for(community, payload, metadata, current):
    latitude, longitude = _communities()[community]
    result = {"source_name": "GeoNet", "source_url": "https://www.geonet.org.nz", "notice": GEONET_NOTICE,
              "events": [], **metadata}
    for event in (payload or {}).get("events", []):
        occurred = _time(event.get("time"))
        if occurred is None or not current - timedelta(days=30) <= occurred <= current:
            continue
        distance = distance_km(latitude, longitude, event["latitude"], event["longitude"])
        if distance > 100:
            continue
        result["events"].append({key: value for key, value in event.items() if key not in {"latitude", "longitude"}} | {"distance_km": round(distance, 1)})
    result["events"] = sorted(result["events"], key=lambda item: item["time"], reverse=True)[:5]
    return result


def get(store, community, refresh=True, now=None, opener=None):
    community, current = _community(community), _now(now)
    opener = opener or urlopen
    geonet_payload, geonet_metadata = _cached(store, "geonet", lambda: fetch_geonet(opener, current), refresh, current)
    water_payload, water_metadata = _cached(store, "water:" + community,
                                           lambda: official_water.fetch(community, opener=opener, now=current), refresh, current)
    water_values = {**official_water.metadata(community), **(water_payload or {})}
    water = {**{key: value for key, value in water_values.items() if key in WATER_FIELDS}, **water_metadata}
    return {"community": community, "water": water, "geonet": _geonet_for(community, geonet_payload, geonet_metadata, current)}


def export_snapshot(store, refresh=True, now=None, opener=None):
    current = _now(now)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(get, store, community, refresh, current, opener) for community in _communities()]
        communities = [future.result() for future in futures]
    cached, _ = _cached(store, "geonet", lambda: None, False, current)
    return {"kind": "official-data-snapshot-v1", "generated_at": current.isoformat(), "communities": communities,
            "map": _map_points(communities, cached, current)}


def _map_points(communities, geonet_payload, current, fallback_map=None):
    stations, selected_events = [], {}
    for item in communities:
        water = item["water"]
        latitude, longitude = water.get("station_latitude"), water.get("station_longitude")
        if _number(latitude, -90, 90) and _number(longitude, -180, 180):
            stations.append({"community": item["community"], "station_name": water.get("station_name"),
                             "latitude": latitude, "longitude": longitude, "water": water})
        for event in item["geonet"].get("events", []):
            selected_events.setdefault(event["public_id"], {"event": event, "status": item["geonet"]["status"],
                                                          "retrieved_at": item["geonet"].get("retrieved_at"), "communities": []})["communities"].append(item["community"])
    positions = {event["public_id"]: event for event in (geonet_payload or {}).get("events", [])}
    for event in (fallback_map or {}).get("earthquakes", []):
        if isinstance(event, dict) and isinstance(event.get("public_id"), str):
            positions.setdefault(event["public_id"], event)
    earthquakes = []
    for public_id, selected in selected_events.items():
        position, event = positions.get(public_id, {}), selected["event"]
        latitude, longitude = position.get("latitude"), position.get("longitude")
        if not _number(latitude, -90, 90) or not _number(longitude, -180, 180):
            continue
        occurred = _time(event.get("time"))
        if occurred is None or not current - timedelta(days=30) <= occurred <= current:
            continue
        earthquakes.append({**{key: value for key, value in event.items() if key != "distance_km"},
                            "latitude": latitude, "longitude": longitude, "status": selected["status"],
                            "retrieved_at": selected["retrieved_at"], "nearby_communities": selected["communities"]})
    return {"water_stations": stations, "earthquakes": sorted(earthquakes, key=lambda event: event["time"], reverse=True),
            "notice": "Official monitoring stations and nearby events from the limited GeoNet feed. Marker colours do not establish swimming safety or tsunami risk."}


def map_snapshot(store, snapshot=None, now=None):
    """Read cached public data only; the initial map never triggers source requests."""
    current = _now(now)
    cached_snapshot = export_snapshot(store, refresh=False, now=current)
    communities = cached_snapshot["communities"]
    for item in communities:
        fallback = offline(snapshot, item["community"], current)
        for source in ("water", "geonet"):
            if item[source]["status"] == "unavailable" and fallback[source]["status"] != "unavailable":
                item[source] = fallback[source]
    geonet_payload, _ = _cached(store, "geonet", lambda: None, False, current)
    fallback_map = snapshot.get("map") if isinstance(snapshot, dict) and isinstance(snapshot.get("map"), dict) else None
    points = _map_points(communities, geonet_payload, current, fallback_map)
    return {"kind": "official-map-v1", "generated_at": current.isoformat(), "communities": communities, **points}


def offline_map(snapshot, now=None):
    """A packaged demonstration uses only the public snapshot, never a database."""
    current = _now(now)
    communities = [offline(snapshot, community, current) for community in _communities()]
    fallback_map = snapshot.get("map") if isinstance(snapshot, dict) and isinstance(snapshot.get("map"), dict) else None
    return {"kind": "official-map-v1", "generated_at": current.isoformat(), "communities": communities,
            **_map_points(communities, None, current, fallback_map)}


def offline(snapshot, community, now=None):
    community, current = _community(community), _now(now)
    unavailable = {"status": "unavailable", "retrieved_at": None, "last_attempt_at": None,
                   "error": "No official data snapshot is available. Check the linked official source."}
    water_values = official_water.metadata(community)
    result = {"community": community, "water": {**{key: value for key, value in water_values.items() if key in WATER_FIELDS}, **unavailable},
              "geonet": {"source_name": "GeoNet", "source_url": "https://www.geonet.org.nz", "notice": GEONET_NOTICE,
                         "events": [], **unavailable}}
    if not isinstance(snapshot, dict) or snapshot.get("kind") != "official-data-snapshot-v1" or not isinstance(snapshot.get("communities"), list):
        return result
    item = next((item for item in snapshot["communities"] if isinstance(item, dict) and item.get("community") == community), None)
    if not item:
        return result
    common = {"status", "retrieved_at", "last_attempt_at", "error", "source_name", "source_url", "notice"}
    for source, extra in (("water", WATER_FIELDS), ("geonet", {"events"})):
        if not isinstance(item.get(source), dict) or item[source].get("status") not in {"available", "stale", "unavailable"}:
            continue
        if item[source]["status"] != "unavailable":
            result[source].pop("error", None)
        result[source].update({key: value for key, value in item[source].items() if key in common | extra})
        if result[source].get("status") == "available" and not _fresh(result[source].get("retrieved_at"), current):
            result[source]["status"] = "stale"
    # A packaged snapshot cannot describe earthquakes occurring after its build time.
    events = result["geonet"].get("events")
    events = events if isinstance(events, list) else []
    result["geonet"]["events"] = [event for event in events if isinstance(event, dict)
        and _time(event.get("time")) is not None and current - timedelta(days=30) <= _time(event["time"]) <= current][:5]
    return result


def main():
    parser = argparse.ArgumentParser(description="Refresh the public official-data snapshot using the fixed council and GeoNet sources.")
    parser.add_argument("--refresh-snapshot", action="store_true", help="Refresh cached official data and atomically save data/official/official-snapshot.json.")
    args = parser.parse_args()
    if not args.refresh_snapshot:
        parser.print_help()
        return
    from server import ROOT, Store

    directory = (ROOT / "data" / "official").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    store = Store(directory / "source-cache.sqlite3")
    snapshot = export_snapshot(store, refresh=True)
    destination = directory / "official-snapshot.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix="official-snapshot-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name).resolve()
            json.dump(snapshot, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
        if temporary.parent != directory or destination.resolve().parent != directory:
            raise ValueError("The official snapshot must stay in its data directory.")
        temporary.replace(destination)
        temporary = None
    finally:
        if temporary is not None and temporary.parent == directory:
            temporary.unlink(missing_ok=True)
    print(json.dumps({"generated_at": snapshot["generated_at"], "communities": len(snapshot["communities"]),
                      "water_samples": sum(len(item["water"].get("samples", [])) for item in snapshot["communities"]),
                      "water_sources_available": sum(item["water"]["status"] == "available" for item in snapshot["communities"]),
                      "mapped_stations": len(snapshot["map"]["water_stations"]),
                      "nearby_earthquakes": len(snapshot["map"]["earthquakes"])}))


if __name__ == "__main__":
    main()
