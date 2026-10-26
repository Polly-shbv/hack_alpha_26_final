import json
import httpx
import pytest
from backend.app import batches,collection
from backend.app.db import db,now,dumps
from backend.app.llm import LiveProvider
from backend.app.errors import DomainError
from backend.tests.test_collection import prepare,configure_free,entry,html,batch_result,catalog
from backend.app.publishers import PUBLISHERS

COMPANIES=['U_9250','U_1941','U_106783']

def setup_news(monkeypatch):
    prepare();configure_free(monkeypatch)
    for index,name in enumerate(['первого комитета','второго регулятора','третьего кредитора']):
        item=entry();item['url']=f'https://lenta.ru/news/test-{index}/'
        raw=html(title='Ключевая ставка снижена: решение '+name)
        collection.store_article(item,PUBLISHERS[0],'live',lambda url:(raw,'text/html',url))

def mock_provider(monkeypatch,change=None,status=200):
    posts=[]
    def handler(request):
        if request.method=='GET':return httpx.Response(200,json=catalog())
        body=json.loads(request.content);context=json.loads(body['messages'][1]['content']);posts.append((body,context))
        if status!=200:return httpx.Response(status)
        result=batch_result(context)
        if change:change(result)
        return httpx.Response(200,json={'usage':{'prompt_tokens':500,'completion_tokens':1000,'cost':0},'choices':[{'message':{'content':json.dumps(result)}}]})
    monkeypatch.setattr(batches,'LiveProvider',lambda cap:LiveProvider(cap,httpx.MockTransport(handler)))
    return posts

def test_three_companies_nine_pairs_use_one_actual_post(database_path,monkeypatch):
    setup_news(monkeypatch);posts=mock_provider(monkeypatch)
    result=batches.create_batch(COMPANIES,'demo',mode='live',background=False)
    batch=batches.get_batch(result['batch_id'])
    assert len(posts)==1 and len(posts[0][1]['companies'])==3 and len(posts[0][1]['events'])==3
    assert len(posts[0][1]['tasks'])==9 and batch['payload']['model_calls']==1
    assert len({event['id'] for event in posts[0][1]['events']})==3
    assert batch['status']=='completed' and batch['payload']['cost_rub']=='0'
    with db() as c:
        assert c.execute('SELECT count(*) FROM insights').fetchone()[0]==9
        assert c.execute('SELECT count(*) FROM model_requests').fetchone()[0]==1
        payloads=[json.loads(r[0]) for r in c.execute('SELECT payload FROM runs')]
    assert sum(p['model_calls'] for p in payloads)==1
    assert sum(p['tokens_in'] for p in payloads)==500
    assert all(len(p['steps'])==7 for p in payloads)
    text=json.dumps(posts[0][1],ensure_ascii=False)
    assert 'revenue_decimal' not in text and 'payment_edges' not in text and 'clarification' not in text

def test_repeated_unchanged_packet_uses_cache_without_model(database_path,monkeypatch):
    setup_news(monkeypatch);posts=mock_provider(monkeypatch)
    batches.create_batch(COMPANIES,'demo',mode='live',background=False)
    second=batches.create_batch(COMPANIES,'demo',mode='live',background=False)
    batch=batches.get_batch(second['batch_id'])
    assert len(posts)==1 and batch['payload']['model_calls']==0 and batch['payload']['cache_hits']==9

@pytest.mark.parametrize('bad',['missing','fact','foreign','duplicate'])
def test_bad_packet_parts_never_trigger_repair_calls(database_path,monkeypatch,bad):
    setup_news(monkeypatch)
    def change(result):
        if bad=='missing':result['results'].pop()
        if bad=='fact':result['results'][0]['interpretation']['facts'][0]['text']='Выручка компании гарантированно вырастет на 42%.'
        if bad=='foreign':result['results'][0]['company_id']='OTHER_COMPANY'
        if bad=='duplicate':result['results'][1]=result['results'][0]
    posts=mock_provider(monkeypatch,change)
    result=batches.create_batch(COMPANIES,'demo',mode='live',background=False);batch=batches.get_batch(result['batch_id'])
    assert len(posts)==1 and batch['status']=='completed_with_limits' and batch['payload']['errors']
    with db() as c:items=[json.loads(r[0]) for r in c.execute('SELECT payload FROM insights')]
    assert len(items)==9
    assert all('42%' not in item['facts'][0]['text'] for item in items)
    rules=sum(i['analysis_method']=='Локальные правила' for i in items)
    assert rules==(9 if bad in ('foreign','duplicate') else 1)

def test_rate_limit_one_post_no_retry_no_scheduled_analysis(database_path,monkeypatch):
    setup_news(monkeypatch);posts=mock_provider(monkeypatch,status=429)
    result=batches.create_batch(COMPANIES,'demo',mode='live',background=False)
    assert len(posts)==1 and batches.get_batch(result['batch_id'])['payload']['errors'][0]['code']=='FREE_RATE_LIMIT'
    worker=collection.Worker()
    try:worker.tick();worker.tick()
    finally:worker.stop()
    assert len(posts)==1
    with db() as c:assert c.execute('SELECT count(*) FROM analysis_batches').fetchone()[0]==1

def test_duplicate_click_returns_same_inflight_packet(database_path,monkeypatch):
    setup_news(monkeypatch);submitted=[]
    monkeypatch.setattr(batches.EXECUTOR,'submit',lambda *a:submitted.append(a))
    first=batches.create_batch(COMPANIES,'demo',mode='live',idem='first-click')
    second=batches.create_batch(COMPANIES,'demo',mode='live',idem='second-click')
    assert first['batch_id']==second['batch_id'] and len(submitted)==1
    batches.cancel_batch(first['batch_id'],'demo')
    assert batches.get_batch(first['batch_id'])['status']=='cancelled'
    with db() as c:assert all(r[0]=='cancelled' for r in c.execute('SELECT status FROM runs'))

def test_no_fresh_news_means_no_model_call(database_path,monkeypatch):
    prepare();configure_free(monkeypatch);posts=mock_provider(monkeypatch)
    result=batches.create_batch(COMPANIES,'demo',mode='live',background=False)
    assert not posts and batches.get_batch(result['batch_id'])['payload']['task_count']==0

def test_batch_scope_and_manual_mode_api(client,monkeypatch):
    first=client.post('/api/v1/analysis/batches',json={'mode':'fixture','role':'cfo'}).json()
    assert set(first['run_ids'])==set(COMPANIES)
    from time import monotonic
    deadline=monotonic()+3
    while monotonic()<deadline:
        batch=client.get('/api/v1/analysis/batches/'+first['batch_id']).json()
        if batch['status'] not in ('queued','running'):break
    assert batch['status']=='completed'
    with pytest.raises(DomainError) as error:batches.get_batch(first['batch_id'],'other-user')
    assert error.value.status==403
    assert client.patch('/api/v1/settings',json={'daily_budget':500,'run_budget':25,'mode':'fixture','auto_refresh':True}).status_code==409
    assert client.get('/api/v1/collection').json()['manual_only'] is True

def test_original_article_url_is_preserved_in_insight_and_export(client,monkeypatch):
    item=entry();raw=html();collection.store_article(item,PUBLISHERS[0],'fixture',lambda url:(raw,'text/html',url))
    result=batches.create_batch(COMPANIES,'demo',mode='fixture',background=False)
    with db() as c:insight=json.loads(c.execute('SELECT payload FROM insights WHERE run_id=?',(result['run_ids']['U_9250'],)).fetchone()[0])
    assert insight['source_metadata'][0]['origin_url']==item['url']
    assert item['url'] in client.get('/api/v1/insights/'+insight['id']+'/export').text

def test_old_live_run_api_also_uses_one_cohort_packet(client,monkeypatch):
    setup_news(monkeypatch);posts=mock_provider(monkeypatch)
    response=client.post('/api/v1/companies/U_1941/runs',json={'mode':'live','role':'cfo'})
    assert response.status_code==202
    from time import sleep
    for _ in range(100):
        result=client.get('/api/v1/analysis/batches/'+response.json()['batch_id']).json()
        if result['status'] not in ('queued','running'):break
        sleep(.02)
    assert len(posts)==1 and result['payload']['company_count']==3
    assert response.json()['run_id']==result['payload']['run_ids']['U_1941']

def test_connection_check_never_generates_completion(client,monkeypatch):
    from backend.app import llm
    configure_free(monkeypatch)
    monkeypatch.setattr(llm,'verify_free_catalog',lambda *a,**kw:None)
    calls=[];original=httpx.Client
    def handler(request):calls.append(request.url.path);return httpx.Response(200,json={'data':{}})
    monkeypatch.setattr(llm.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    result=client.post('/api/v1/settings/test-connection')
    assert result.status_code==200 and result.json()['generation_requests']==0
    assert calls==['/api/v1/key']
    with db() as c:assert c.execute('SELECT count(*) FROM model_requests').fetchone()[0]==0

def test_existing_real_news_migrates_with_revision_tracking(database_path,monkeypatch):
    prepare();configure_free(monkeypatch);item=entry();raw=html()
    first=collection.store_article(item,PUBLISHERS[0],'fixture',lambda url:(raw,'text/html',url))
    batches.prepare_corpus('live','demo')
    same=collection.store_article(item,PUBLISHERS[0],'live',lambda url:(raw,'text/html',url))
    assert same['status']=='unchanged'
    updated=collection.store_article(item,PUBLISHERS[0],'live',lambda url:(html(text='Ключевая ставка снижена. Это исправленная синтетическая публикация с новыми условиями кредитования. Договоры нужно проверить отдельно.'),'text/html',url))
    assert updated['version']==2
    with db() as c:
        assert c.execute('SELECT active FROM sources WHERE id=?',(same['id'],)).fetchone()[0]==0
        assert c.execute('SELECT active FROM sources WHERE id=?',(first['id'],)).fetchone()[0]==1
