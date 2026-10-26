import sys
from .db import migrate,ROOT
from .seed import seed

def main():
    if len(sys.argv)!=2 or sys.argv[1]!='seed':raise SystemExit('Usage: python -m backend.app.cli seed')
    migrate();seed();print('Demo seed готов. Учебные события; профили из CSV.')
if __name__=='__main__':main()
