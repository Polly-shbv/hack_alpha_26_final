import json
from .db import ROOT,db,dumps,unpack
from .ingestion import add_document

def seed():
    with db() as c:
        if not c.execute('SELECT 1 FROM companies LIMIT 1').fetchone():
            for p in json.loads((ROOT/'data/fixtures/profiles.json').read_text()):c.execute('INSERT INTO companies VALUES(?,?,?)',(p['id'],dumps(p),p['version']))
    for e in json.loads((ROOT/'data/fixtures/events.json').read_text()):
        with db() as c:
            if c.execute('SELECT 1 FROM events WHERE id=?',(e['id'],)).fetchone():continue
        d=add_document('Учебный сценарий: '+e['title'],e['fact'],published_at=e['published_at'],owner='public')
        e['source_document_ids']=[d['id']]
        with db() as c:
            c.execute('DELETE FROM events WHERE id=?',('event-'+d['id'],))
            c.execute('INSERT INTO events VALUES(?,?,?)',(e['id'],dumps(e),'fixture'))
    import os
    if os.getenv('ALFA_SKIP_REFERENCE_CORPUS')!='1':
        from .corpus import load_case_corpus
        load_case_corpus()
