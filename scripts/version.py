from pathlib import Path
import hashlib,json
root=Path(__file__).resolve().parents[1];digest=hashlib.sha256();files=[]
for base in ['backend','frontend','scripts','tests','.github']:
 for p in sorted((root/base).rglob('*')):
  if not p.is_file() or '__pycache__' in p.parts:continue
  files.append(p)
for name in ['package.json','pnpm-lock.yaml','pnpm-workspace.yaml','requirements.lock.txt','requirements-ocr-macos.lock.txt','tsconfig.json','vite.config.ts','Dockerfile','compose.yaml','.dockerignore','.gitignore','.env.example','playwright.config.ts','index.html']:files.append(root/name)
for p in sorted(files):digest.update(str(p.relative_to(root)).encode());digest.update(p.read_bytes())
value={'build':'1.4.1','source_sha256':digest.hexdigest(),'files':len(files),'date':'2026-10-26'}
(root/'docs/audit/version.json').write_text(json.dumps(value,indent=2));print(json.dumps(value))
