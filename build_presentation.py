"""Package an explicitly synthetic, read-only site for GitHub Pages."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

from demo_server import DEFAULT_DB, dataset_payload, validate_demo_database
from server import ROOT, Store

PACKAGE_KIND = 'coastkind-static-synthetic-v1'
MARKER = '.coastkind-presentation.json'
ASSETS = ('app.js', 'styles.css', 'map.css', 'demo.js', 'demo.css', 'static_demo.js')
FORBIDDEN_KEYS = {'password', 'password_hash', 'token_hash', 'csrf_token', 'csrfToken', 'voucher_code', 'api_key', 'email', 'image'}


def safe_relative(value):
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and '..' not in path.parts and '\\' not in value


def reject_private_fields(value):
    if isinstance(value, dict):
        if FORBIDDEN_KEYS.intersection(value):
            raise ValueError('Private fields must not be included in a static presentation.')
        for child in value.values():
            reject_private_fields(child)
    elif isinstance(value, list):
        for child in value:
            reject_private_fields(child)


def validate_output(path):
    path = Path(path).resolve()
    if path.parent != ROOT.resolve() or not (path.name in ('docs', 'presentation') or path.name.startswith('presentation-')):
        raise ValueError('Use docs, presentation or a presentation-* directory directly inside this workspace.')
    if path.exists():
        if not path.is_dir():
            raise ValueError('The presentation output must be a directory.')
        files = list(path.rglob('*'))
        if any(file.is_symlink() for file in files):
            raise ValueError('Presentation output must not contain symbolic links.')
        if files:
            try:
                marker = json.loads((path / MARKER).read_text(encoding='utf-8'))
                if marker.get('package_kind') != PACKAGE_KIND:
                    raise ValueError('Unrecognized presentation marker.')
                allowed = set(marker['files']) | {MARKER}
                if not all(safe_relative(name) for name in allowed):
                    raise ValueError('Invalid presentation manifest path.')
                if any(file.is_file() and file.relative_to(path).as_posix() not in allowed for file in files):
                    raise ValueError('Output contains files not created by the presentation builder.')
            except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
                raise ValueError('Refusing to overwrite a nonempty directory without a valid presentation manifest.') from error
    return path


def source_text(filename):
    path = ROOT / filename
    if path.is_symlink():
        raise ValueError('Presentation source assets must be ordinary workspace files.')
    return path.read_text(encoding='utf-8')


def build_presentation(output_dir=ROOT / 'presentation', db_path=DEFAULT_DB):
    output = validate_output(output_dir)
    database = validate_demo_database(db_path)
    archive = output.parent / (output.name + '.zip')
    if archive.exists():
        if archive.is_symlink():
            raise ValueError('The existing presentation archive must not be a symbolic link.')
        try:
            with zipfile.ZipFile(archive) as old:
                if json.loads(old.read(MARKER))['package_kind'] != PACKAGE_KIND:
                    raise ValueError('Refusing to replace an unrelated archive.')
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as error:
            raise ValueError('Refusing to replace an unrelated archive.') from error
    store = Store(database)
    payload = dataset_payload(store)
    if payload.get('synthetic') is not True or any(row.get('synthetic') is not True for row in payload['observations']):
        raise ValueError('Every published record must be explicitly synthetic.')
    reject_private_fields(payload)
    plan = {name: source_text(name).encode('utf-8') for name in ASSETS}
    main = source_text('index.html')
    if 'window.coastkindDemoApi' not in plan['app.js'].decode('utf-8'):
        raise ValueError('The application does not yet support the static demonstration adapter.')
    main = main.replace('<script src="app.js" defer></script>', '<script src="static_demo.js" defer></script><script src="app.js" defer></script>')
    main = main.replace('</head>', '<link rel="stylesheet" href="demo.css"></head>')
    banner = ('<div class="synthetic-banner"><strong>SYNTHETIC DEMO &middot; READ ONLY</strong>'
              '<span>Fictional observations, analysis and points. No uploads or real rewards.</span>'
              '<a href="demo.html">Explore the dataset &nearr;</a></div>')
    if '<body>' not in main:
        raise ValueError('The community page body could not be prepared for presentation.')
    main = main.replace('<body>', '<body class="demo-preview">' + banner, 1)
    plan['index.html'] = main.encode('utf-8')
    explorer = source_text('demo.html')
    explorer = re.sub(r'<body([^>]*)>', r'<body\1 data-demo-source="data/preview.json">', explorer, count=1)
    explorer = explorer.replace('href="/"', 'href="index.html"').replace('href="/demo-data"', 'href="demo.html"')
    explorer = explorer.replace('/demo-download/', 'downloads/')
    explorer = re.sub(r'(src|href)="/(demo\.(?:js|css)|styles\.css|map\.css)"', r'\1="\2"', explorer)
    plan['demo.html'] = explorer.encode('utf-8')
    with store.connect() as db:
        for record in payload['observations']:
            identifier = record['id']
            if not re.fullmatch(r'[a-f0-9-]{36}', identifier):
                raise ValueError('Invalid synthetic observation identifier.')
            image = db.execute('SELECT image,mime FROM observations WHERE id=?', (identifier,)).fetchone()
            if not image or image['mime'] != 'image/jpeg':
                raise ValueError('A synthetic observation image is missing or has an unexpected format.')
            record['photo'] = f'images/{identifier}.jpg'
            plan[record['photo']] = bytes(image['image'])
    plan['data/preview.json'] = json.dumps(payload, indent=2, ensure_ascii=False).encode('utf-8')
    for name in ('observations.csv', 'dataset.json', 'CODEBOOK.md'):
        source = database.parent / name
        if source.is_symlink():
            raise ValueError('Synthetic exports must be ordinary generated files.')
        content = source.read_bytes()
        if name.endswith('.json'):
            data = json.loads(content)
            reject_private_fields(data)
            if (not isinstance(data, dict) or data.get('synthetic') is not True
                    or any(record.get('synthetic') is not True for record in data.get('records', []))):
                raise ValueError('Only explicitly synthetic dataset exports may be published.')
            data.pop('db_path', None)
            data.pop('files', None)
            content = json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')
        elif name.endswith('.csv'):
            rows = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
            if len(rows) != len(payload['observations']) or any(row.get('synthetic') != '1' for row in rows):
                raise ValueError('Only marked synthetic CSV rows may be published.')
        plan['downloads/' + ('codebook.md' if name == 'CODEBOOK.md' else name)] = content
    plan['.nojekyll'] = b''
    plan['README.md'] = source_text('PRESENTATION.md').encode('utf-8')
    marker = {'package_kind': PACKAGE_KIND, 'synthetic': True,
              'built_at': datetime.now(timezone.utc).isoformat(),
              'files': {name: hashlib.sha256(content).hexdigest() for name, content in sorted(plan.items())},
              'observations': len(payload['observations']),
              'notice': 'Static fictional demonstration only. No database, credentials, account sessions or voucher codes.'}
    plan[MARKER] = json.dumps(marker, indent=2).encode('utf-8')
    # Only the explicit package plan is written or archived; no folder-wide copying or deletion.
    output.mkdir(parents=True, exist_ok=True)
    (output / MARKER).write_bytes(plan[MARKER])
    for name, content in plan.items():
        target = output / name
        if target.is_symlink():
            raise ValueError('Refusing to overwrite a symbolic link.')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name, content in sorted(plan.items()):
            bundle.writestr(name, content)
    return {'directory': str(output), 'archive': str(archive), 'files': len(plan),
            'observations': len(payload['observations']), 'synthetic': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default=str(ROOT / 'presentation'))
    parser.add_argument('--db', default=str(DEFAULT_DB))
    args = parser.parse_args()
    try:
        result = build_presentation(args.out, args.db)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
