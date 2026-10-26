"""Presentation changes cannot rewrite evidence or the persisted historical answer."""
import json
from backend.app.presentation import present_insight
from backend.app.analysis import create_run
from backend.app.db import db,dumps

OLD='Если компания U_9250 участвует в строительных проектах в указанных регионах, это может соответствовать её профилю деятельности.'


def test_presentation_preserves_history_and_evidence():
    raw={'analysis_method':'Gemma 4 · пакетный запрос','topic':'construction','summary':OLD+' Условный риск.',
         'company_links':[{'text':OLD,'field_evidence_ids':['U_9250:industry']}],
         'facts':[{'text':'Дословная цитата','evidence_ids':['chunk-1']}]}
    shown=present_insight(raw)
    assert shown['analysis_method']=='AI-анализ' and OLD not in shown['summary']
    assert 'текущих объектах' in shown['company_links'][0]['text']
    assert shown['facts']==raw['facts'] and shown['company_links'][0]['field_evidence_ids']==['U_9250:industry']
    assert raw['company_links'][0]['text']==OLD and raw['analysis_method']=='Gemma 4 · пакетный запрос'


def test_all_read_surfaces_use_public_copy_without_modifying_database(client):
    run=create_run('U_9250','demo',mode='fixture',background=False)
    with db() as c:
        row=c.execute('SELECT id,payload FROM insights WHERE run_id=? LIMIT 1',(run['id'],)).fetchone()
        iid=row['id'];item=json.loads(row['payload']);item.update(analysis_method='Gemma 4 · пакетный запрос',topic='construction',summary=OLD)
        item['company_links'][0]['text']=OLD;c.execute('UPDATE insights SET payload=? WHERE id=?',(dumps(item),iid))
    assert client.put('/api/v1/companies/U_9250/saved/'+iid).status_code==200
    for path in ['/companies/U_9250/overview','/companies/U_9250/insights','/companies/U_9250/insights/'+iid,
                 '/companies/U_9250/saved','/companies/U_9250/graph','/events/'+item['event_id']+'/comparison','/insights/'+iid+'/export']:
        response=client.get('/api/v1'+path);assert response.status_code==200,path
        assert OLD not in response.text and 'Gemma 4 · пакетный запрос' not in response.text,path
    with db() as c:assert json.loads(c.execute('SELECT payload FROM insights WHERE id=?',(iid,)).fetchone()[0])['company_links'][0]['text']==OLD
