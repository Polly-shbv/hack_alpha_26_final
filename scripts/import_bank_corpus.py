import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.db import migrate
from backend.app.corpus import load_case_corpus

parser=argparse.ArgumentParser()
parser.add_argument('--folder',type=Path)
parser.add_argument('--mode',choices=['fixture','live'],default='fixture')
args=parser.parse_args()
migrate()
print(json.dumps(load_case_corpus(args.folder,args.mode),ensure_ascii=False,indent=2))
