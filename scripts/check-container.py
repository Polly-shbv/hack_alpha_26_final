"""Run after docker compose up. No model calls; persist a fixture run through restart."""
import json
import argparse
import shutil
from pathlib import Path
import subprocess
import time
from urllib.request import build_opener,HTTPCookieProcessor,ProxyHandler,Request

parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8000');args=parser.parse_args()
BASE=args.url.rstrip('/')
client=build_opener(ProxyHandler({}),HTTPCookieProcessor())
def request(path,body=None,headers=None):
    raw=None if body is None else json.dumps(body).encode()
    with client.open(Request(BASE+path,data=raw,headers={'Content-Type':'application/json',**(headers or {})}),timeout=5) as r:
        return json.loads(r.read())
def healthy():
    for _ in range(90):
        try:request('/api/v1/health');return
        except Exception:time.sleep(1)
    raise SystemExit('Container health timeout')
healthy();me=request('/api/v1/me');headers={'X-CSRF-Token':me['csrf_token'],'Origin':BASE}
companies=request('/api/v1/companies')['items'];assert len(companies)==3
result=request('/api/v1/companies/U_9250/runs',{'mode':'fixture'},headers);rid=result['run_id']
for _ in range(100):
    run=request('/api/v1/runs/'+rid)
    if run['status'] not in ('queued','running'):break
    time.sleep(.1)
assert run['status']=='completed_with_limits' and len(run['payload']['steps'])==7
docker=shutil.which('docker') or '/Applications/Docker.app/Contents/Resources/bin/docker'
assert Path(docker).is_file(),'Docker CLI unavailable'
subprocess.run([docker,'compose','restart'],check=True)
healthy();assert request('/api/v1/runs/'+rid)['id']==rid
assert request('/api/v1/collection')['manual_only'] is True
print('PASS: container health, three profiles, seven-step fixture run, persistent history, manual-only mode')
