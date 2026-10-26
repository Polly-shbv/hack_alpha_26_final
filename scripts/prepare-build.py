"""Compile outside macOS Documents/File Provider; keep source and output local."""
import os,mmap,hashlib,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
build=Path('/private/tmp')/('alfa-context-build-'+hashlib.sha256(str(root).encode()).hexdigest()[:12])
def copy_file(src,dst):
    dst.parent.mkdir(parents=True,exist_ok=True)
    with src.open('rb') as f:
        data=mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ)[:] if os.fstat(f.fileno()).st_size else b''
    temp=dst.with_name(dst.name+'.tmp-build');temp.write_bytes(data);os.replace(temp,dst)
def copy_tree(src,dst):
    for folder,dirs,files in os.walk(src):
        dirs[:]=[d for d in dirs if d not in ('node_modules','__pycache__')]
        for name in files:copy_file(Path(folder)/name,dst/Path(folder).relative_to(src)/name)
if len(sys.argv)>1 and sys.argv[1]=='output':
    copy_tree(build/'dist',root/'dist');print('Production output: '+str(root/'dist'))
else:
    build.mkdir(exist_ok=True)
    for name in ['frontend','public']:copy_tree(root/name,build/name)
    for name in ['package.json','pnpm-lock.yaml','pnpm-workspace.yaml','tsconfig.json','vite.config.ts','index.html']:copy_file(root/name,build/name)
    print(build)
