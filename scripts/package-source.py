"""Allowlisted technical source ZIP, with locks, tests, CI and reviewed OCR corpus."""
import argparse,hashlib,json,stat,zipfile,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TOP=['README.md','THIRD_PARTY_NOTICES.md','Dockerfile','compose.yaml','.dockerignore','.gitignore','.env.example','package.json','pnpm-lock.yaml','pnpm-workspace.yaml','requirements.lock.txt','requirements.txt','requirements-ocr-macos.lock.txt','tsconfig.json','vite.config.ts','playwright.config.ts','index.html']
FOLDERS=['backend','frontend','scripts','tests','public','docs','data/fixtures','data/reference/bank','.github']
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,default=ROOT);parser.add_argument('--output',type=Path,default=ROOT/'exports/alfa-context-source.zip');args=parser.parse_args();root=args.source.resolve()
    names=set(TOP)
    for folder in FOLDERS:
        for path in (root/folder).rglob('*'):
            if not path.is_file() or path.is_symlink():continue
            rel=path.relative_to(root)
            if any(p in rel.parts for p in ('__pycache__','screenshots','.pytest_cache')) or path.suffix=='.pyc':continue
            if folder=='data/reference/bank' and (path.suffix!='.json' or path.name=='manifest.pending.json'):continue
            names.add(rel.as_posix())
    files={n:(root/n).read_bytes() for n in sorted(names) if (root/n).is_file()}
    for name,raw in files.items():
        assert not re.search(rb'sk-or-v1-[a-zA-Z0-9]{20,}|AQ\.[a-zA-Z0-9_-]{30,}',raw),f'Secret pattern in {name}'
    files['source-manifest.sha256.json']=json.dumps({n:hashlib.sha256(raw).hexdigest() for n,raw in files.items()},ensure_ascii=False,indent=2).encode()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.output,'w',zipfile.ZIP_DEFLATED) as out:
        for name,raw in files.items():
            info=zipfile.ZipInfo('alfa-context-source/'+name);info.compress_type=zipfile.ZIP_DEFLATED;info.create_system=3
            info.external_attr=(stat.S_IFREG | (0o755 if (root/name).exists() and (root/name).stat().st_mode & 0o111 else 0o644))<<16;out.writestr(info,raw)
    print(json.dumps({'path':str(args.output),'files':len(files),'bytes':args.output.stat().st_size,'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest()}))
if __name__=='__main__':main()
