"""Mirror sources via mmap for verification when File Provider read() stalls."""
import os,mmap
from pathlib import Path
root=Path.cwd();target=Path('/private/tmp/alfa-context-clean-check')
def cp(src,dst):
    dst.parent.mkdir(parents=True,exist_ok=True)
    with src.open('rb') as f:
        mm=mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) if os.fstat(f.fileno()).st_size else None
        data=mm[:] if mm else b''
        if mm:mm.close()
    dst.write_bytes(data)
    os.chmod(dst,src.stat().st_mode&0o777)
for name in ['backend','frontend','scripts','data/fixtures','public','tests','docs']:
    src=root/name
    for folder,dirs,files in os.walk(src):
        dirs[:]=[d for d in dirs if d not in ('node_modules','__pycache__','screenshots')]
        for filename in files:
            p=Path(folder)/filename;cp(p,target/p.relative_to(root))
for name in ['package.json','pnpm-lock.yaml','pnpm-workspace.yaml','requirements.lock.txt','requirements.txt','tsconfig.json','vite.config.ts','playwright.config.ts','index.html','README.md','Dockerfile','compose.yaml']:
    cp(root/name,target/name)
print('Current sources mirrored to',target,flush=True)
