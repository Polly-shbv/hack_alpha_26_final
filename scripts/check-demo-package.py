"""Verify the reviewer artifact and optionally smoke-test its running server."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import time
from zipfile import ZipFile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--url')
    args = parser.parse_args()
    checks = []
    with ZipFile(args.archive) as archive:
        prefix = 'alfa-context-demo/'
        names = archive.namelist()
        assert all(n.startswith(prefix) and '..' not in n.split('/') for n in names)
        assert not any(n.endswith(('.csv', '.sqlite3', '.pyc')) or any(p in n.split('/') for p in ('.env', '.venv', '.venv-demo', 'node_modules', 'uploads')) or '/data/local/' in n for n in names)
        checks.append('allowlist: no raw CSV, working DB, uploads, secrets or dependencies')
        manifest = json.loads(archive.read(prefix + 'manifest.sha256.json'))
        assert set(names) == {prefix + name for name in manifest} | {prefix + 'manifest.sha256.json'}
        assert all(hashlib.sha256(archive.read(prefix + name)).hexdigest() == sha for name, sha in manifest.items())
        checks.append('all archive checksums valid')
        assert (archive.getinfo(prefix + 'Запустить демо.command').external_attr >> 16) & 0o111
        assert all(prefix + name in names for name in ('start_demo.py', 'README.md', 'dist/index.html', 'backend/migrations/001_initial.sql', 'data/fixtures/profiles.json'))
        assert len([n for n in names if 'test-documents/' in n and n.endswith('.pdf')]) == 9
        compile(archive.read(prefix + 'start_demo.py'), 'start_demo.py', 'exec')
        checks.append('launchers, prebuilt UI, profiles and nine fixture PDFs present')
    if args.url:
        import httpx
        with httpx.Client(base_url=args.url, trust_env=False) as client:
            index = client.get('/select-company')
            assert index.status_code == 200 and '<div id="root">' in index.text
            assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', index.text)
            assert assets and all(client.get(path).status_code == 200 for path in assets)
            checks.append('production SPA route and all index assets return 200')
            me = client.get('/api/v1/me').json()
            client.headers.update({'X-CSRF-Token': me['csrf_token'], 'Origin': args.url})
            companies = client.get('/api/v1/companies').json()['items']
            assert {p['id'] for p in companies} == {'U_9250', 'U_1941', 'U_106783'}
            for company in companies:
                uid = company['id']
                response = client.post(f'/api/v1/companies/{uid}/runs', json={'mode': 'fixture'})
                assert response.status_code == 202, response.text
                rid = response.json()['run_id']
                for _ in range(100):
                    run = client.get('/api/v1/runs/' + rid).json()
                    if run['status'] not in ('queued', 'running'):
                        break
                    time.sleep(.05)
                assert run['status'] == 'completed_with_limits', run
                assert run['payload']['cost_rub'] == '0'
                assert len(run['payload']['steps']) == 7
                overview = client.get(f'/api/v1/companies/{uid}/overview').json()
                assert overview['items'] and overview['run']['id'] == rid
            checks.append('all three companies complete seven-step fixture analysis at 0 RUB')
            assert client.post('/api/v1/companies/U_9250/runs', json={'mode': 'live'}).status_code == 409
            checks.append('live calls remain blocked without credentials')
    print(json.dumps({'status': 'passed', 'checks': checks}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
