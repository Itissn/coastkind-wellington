"""Read-only local preview of a separately generated, explicitly synthetic dataset."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from server import Handler, ROOT, Store
import community_status
import official_data

DEMO_KIND = 'synthetic-demo-v1'
DEFAULT_DB = ROOT / 'data' / 'demo' / 'wainet-demo.sqlite3'


def validate_demo_database(path):
    path = Path(path).resolve()
    if path == (ROOT / 'data' / 'wainet.sqlite3').resolve() or not path.is_file():
        raise ValueError('Choose a generated demonstration database, never the community database.')
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        try:
            marker = db.execute("SELECT value FROM demo_metadata WHERE key='dataset_kind'").fetchone()
            if not marker or marker[0] != DEMO_KIND:
                raise ValueError('The database is not marked as a synthetic demonstration.')
            unmarked = db.execute("""SELECT COUNT(*) FROM observations
                WHERE schema_version NOT LIKE 'demo-%' AND schema_version NOT LIKE 'synthetic-demo%'""").fetchone()[0]
            if unmarked:
                raise ValueError('Every preview observation must be explicitly synthetic.')
        except sqlite3.Error as error:
            raise ValueError('The database does not have the required demonstration metadata.') from error
    db.close()
    return path


def dataset_payload(store):
    records = store.list()
    scenario_records = {}
    export_path = store.path.parent / 'dataset.json'
    if export_path.is_file():
        export = json.loads(export_path.read_text(encoding='utf-8'))
        if export.get('synthetic') is True and export.get('dataset_kind') == DEMO_KIND:
            scenario_records = {row['id']: row for row in export.get('records', []) if row.get('synthetic') is True}
    with store.connect() as db:
        rows = {r['id']: dict(r) for r in db.execute("""SELECT id,user_id,dataset_consent,
            schema_version,prompt_version,analysis_error FROM observations""")}
        points = {r['observation_id']: r['points'] for r in db.execute(
            'SELECT observation_id,SUM(delta) AS points FROM points_ledger GROUP BY observation_id')}
        users = [dict(r) for r in db.execute("""SELECT u.id,u.display_name,COUNT(DISTINCT o.id) AS contributions,
            COALESCE((SELECT SUM(p.delta) FROM points_ledger p WHERE p.user_id=u.id),0) AS points
            FROM users u LEFT JOIN observations o ON o.user_id=u.id GROUP BY u.id ORDER BY u.display_name""")]
        table_counts = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                        for table in ('observations', 'users', 'comments', 'support', 'reviews', 'points_ledger', 'alerts')}
    for record in records:
        record.update(rows[record['id']])
        record.update(synthetic=True, simulated_analysis=bool(record['analysis']),
                      contributor_type='guest' if record['rewards_waived'] else 'account',
                      points_awarded=points.get(record['id'], 0))
        scenario = scenario_records.get(record['id'], {})
        record['activity'] = scenario.get('activity') or (record['analysis'] or {}).get('activity', 'unknown')
        record['scenario_id'] = scenario.get('scenario_id', '')
        record['scenario_type'] = scenario.get('scenario_type', '')
    for user in users:
        user['synthetic'] = True
    summary = {'observations': len(records), 'users': len(users), 'table_counts': table_counts,
               'guest_observations': sum(r['rewards_waived'] for r in records),
               'duplicates': sum(bool(r['duplicate_of']) for r in records),
               'communities': dict(Counter(r['community'] for r in records)),
               'analysis_status': dict(Counter(r['status'] for r in records)),
               'review_status': dict(Counter(r['review_status'] for r in records)),
               'activities': dict(Counter(r['activity'] for r in records)),
               'categories': dict(Counter(category for r in records for category in (r['analysis'] or {}).get('pollution_types', []))),
               'simulated_points': sum(points.values()),
               'submitted_from': min((r['created'] for r in records), default=None),
               'submitted_to': max((r['created'] for r in records), default=None)}
    alerts = [{**a, 'synthetic': True} for a in store.alerts()]
    summary['alerts'] = len(alerts)
    return {'synthetic': True, 'dataset_kind': DEMO_KIND,
            'notice': 'Fictional data for software and analysis practice. No real environmental findings, AI calls, human assessments or monetary rewards.',
            'summary': summary, 'observations': records, 'users': users, 'alerts': alerts,
            'community_status': community_status.generate(store, synthetic=True),
            'concerns': [{**group, 'synthetic': True} for group in store.concerns()]}


class DemoHandler(Handler):
    def auth_session(self):
        # A localhost cookie from the normal website must never log in to this preview.
        return None

    def do_GET(self):
        if not self.trusted_host():
            return self.json(403, {'error': 'Invalid host.'})
        path = urlsplit(self.path).path
        if path == '/api/health':
            return self.json(200, {'storage': 'sqlite', 'aiConfigured': False,
                                  'demoMode': True, 'synthetic': True})
        if path == '/api/demo-data':
            return self.json(200, dataset_payload(self.server.store))
        if path == '/api/community-status':
            return self.json(200, community_status.generate(self.server.store, synthetic=True))
        if path == '/api/official-map':
            if urlsplit(self.path).query:
                return self.json(400, {'error': 'The official map does not accept query parameters.'})
            snapshot_path = ROOT / 'data' / 'official' / 'official-snapshot.json'
            try:
                snapshot = json.loads(snapshot_path.read_text(encoding='utf-8')) if snapshot_path.is_file() and snapshot_path.stat().st_size <= official_data.MAX_PAYLOAD_BYTES else None
            except (OSError, ValueError):
                snapshot = None
            return self.json(200, official_data.offline_map(snapshot))
        if path == '/api/official-data':
            try:
                params = parse_qs(urlsplit(self.path).query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
                if set(params) != {'community'} or len(params['community']) != 1:
                    raise ValueError('Choose one community.')
                snapshot_path = ROOT / 'data' / 'official' / 'official-snapshot.json'
                try:
                    snapshot = json.loads(snapshot_path.read_text(encoding='utf-8')) if snapshot_path.is_file() else None
                except (OSError, ValueError):
                    snapshot = None
                return self.json(200, official_data.offline(snapshot, params['community'][0]))
            except (ValueError, TypeError):
                return self.json(400, {'error': 'Choose one valid coastal community.'})
        assets = {'/demo-data': ('demo.html', 'text/html'),
                  '/demo.js': ('demo.js', 'text/javascript'), '/demo.css': ('demo.css', 'text/css')}
        if path in assets:
            name, mime = assets[path]
            return self.send_bytes(200, (ROOT / name).read_bytes(), mime + '; charset=utf-8')
        downloads = {'/demo-download/observations.csv': ('observations.csv', 'text/csv'),
                     '/demo-download/dataset.json': ('dataset.json', 'application/json'),
                     '/demo-download/codebook.md': ('CODEBOOK.md', 'text/plain')}
        if path in downloads:
            name, mime = downloads[path]
            target = self.server.store.path.parent / name
            if target.is_file():
                return self.send_bytes(200, target.read_bytes(), mime + '; charset=utf-8')
            return self.json(404, {'error': 'Generate the dataset exports first.'})
        if path in ('/', '/index.html'):
            html = (ROOT / 'index.html').read_text(encoding='utf-8')
            banner = ('<div class="synthetic-banner"><strong>SYNTHETIC DEMO · READ ONLY</strong>'
                      '<span>Fictional community posts. Official source snapshots are labelled separately.</span>'
                      '<a href="/demo-data">Explore the dataset ↗</a>'
                      '<a href="http://localhost:8000">Main website ↗</a></div>')
            html = html.replace('<body>', '<body class="demo-preview">' + banner)
            html = html.replace('</head>', '<link rel="stylesheet" href="/demo.css"></head>')
            return self.send_bytes(200, html.encode('utf-8'), 'text/html; charset=utf-8')
        return super().do_GET()

    def do_POST(self):
        if not self.trusted_host():
            return self.json(403, {'error': 'Invalid host.'})
        return self.json(405, {'error': 'This synthetic data preview is read-only. Use the main website to contribute.'})

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST


def create_demo_server(port=8001, db_path=None):
    path = validate_demo_database(db_path or DEFAULT_DB)
    store = Store(path)
    preview = ThreadingHTTPServer(('127.0.0.1', port), DemoHandler)
    preview.store, preview.api_key, preview.model = store, '', 'demo-simulated-v1'
    return preview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8001)
    parser.add_argument('--db', default=str(DEFAULT_DB))
    args = parser.parse_args()
    try:
        preview = create_demo_server(args.port, args.db)
    except ValueError as error:
        parser.error(str(error))
    print(f'Synthetic dataset: http://localhost:{preview.server_port}/demo-data', flush=True)
    print('Read only. No AI worker, real rewards, or government submission.', flush=True)
    try:
        preview.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        preview.server_close()


if __name__ == '__main__':
    main()
