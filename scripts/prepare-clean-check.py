from pathlib import Path
import shutil,json
root=Path(__file__).resolve().parents[1];target=Path('/private/tmp/alfa-context-clean-check')
target.mkdir(exist_ok=True)
for name in ['backend','frontend','scripts','data/fixtures','public']:
 dest=target/name
 if dest.exists():shutil.rmtree(dest)
 shutil.copytree(root/name,dest,ignore=shutil.ignore_patterns('__pycache__'))
for name in ['package.json','pnpm-lock.yaml','pnpm-workspace.yaml','requirements.lock.txt','requirements.txt','tsconfig.json','vite.config.ts','index.html']:
 shutil.copy2(root/name,target/name)
print(target)
