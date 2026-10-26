from pathlib import Path
import json,re,sys
root=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(root))
from backend.app.datasets import import_datasets
spec=(root/'ALFA_CONTEXT_CODEX_SPEC.md').read_text()
events=[]
keys=['rate','china','copper','gold','conflict','no-bank']
for line in spec.splitlines():
 if line.startswith('| fixture-'):
  parts=[x.strip() for x in line.split('|')[1:-1]]
  events.append({'id':parts[0],'title':parts[1],'fact':parts[2].strip('«».')+'.','published_at':'2026-10-22T09:00:00+00:00','topic':next(k for k in keys if parts[0].startswith('fixture-'+k)),'is_synthetic':True})
texts={}
for line in spec.splitlines():
 if re.match(r'\| (Ставка|Китай|Медь) / U_',line):
  parts=[x.strip().strip('«».') for x in line.split('|')[1:-1]]; name,uid=parts[0].split(' / ');key={'Ставка':'rate','Китай':'china','Медь':'copper'}[name]
  texts[key+':'+uid]={'why':parts[1]+'.','risk':parts[2]+'.','action':parts[3]+'.'}
(root/'data/fixtures/events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
(root/'data/fixtures/texts.json').write_text(json.dumps(texts,ensure_ascii=False,indent=2))
report=import_datasets([(p.name,p.read_bytes()) for p in sorted(root.glob('df_*.csv'))])
(root/'docs/audit/dataset-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
from backend.app.db import db
with db() as c:
 profiles=[json.loads(r[0]) for r in c.execute('SELECT payload FROM companies')]
(root/'data/fixtures/profiles.json').write_text(json.dumps(profiles,ensure_ascii=False,indent=2))
print({k:v for k,v in report.items() if k not in ['preview','warnings','files']})
