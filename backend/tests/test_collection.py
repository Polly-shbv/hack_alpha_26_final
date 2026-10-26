"""Synthetic publisher pages and transport doubles; no network/model calls."""
import json
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import httpx
import pytest
from backend.app import collection as service
from backend.app.db import db,dumps,now
from backend.app.errors import DomainError
from backend.app.publishers import PUBLISHERS,extract_article,article_url
from backend.app.llm import LiveProvider,configuration,reserve_free_request
from backend.app.analysis import create_run
from backend.app.seed import seed

TEXT='Ключевая ставка снижена. Стоимость новых кредитов может уменьшиться при пересмотре условий договора. Это синтетический материал для проверки программы.'

def entry():return {'url':'https://lenta.ru/news/2026/10/26/test/','title':'Ключевая ставка снижена','description':TEXT,'published_at':now(),'category':'Экономика'}
def html(text=TEXT,title='Ключевая ставка снижена'):
    return ('<html><head><meta property="article:published_time" content="'+now()+'"></head><body><nav>НЕ СТАТЬЯ</nav><h1>'+title+'</h1><div class="topic-body__content">'+text+'</div></body></html>').encode()
def fetch_html(url):return html(),'text/html',url

def prepare():seed();service.initialize()

@pytest.mark.parametrize('publisher,selector',[('lenta','topic-body__content'),('ria','article__body'),('tass','news-content')])
def test_publisher_bodies_exclude_navigation(publisher,selector):
    p=next(p for p in PUBLISHERS if p['id']==publisher)
    raw=('<nav>Ненужное меню</nav><h1>Ставка снижена</h1><div class="'+selector+'">'+TEXT+'</div><footer>НЕ ФАКТ</footer>').encode()
    result=extract_article(raw,p['homepage'],entry())
    assert result['text']==TEXT and result['publisher']==p['name']
    assert 'Ненужное меню' not in result['text']

def test_amp_is_one_canonical_article():
    assert article_url('https://lenta.ru/amp/news/2026/10/26/test/?utm_source=test')=='https://lenta.ru/news/2026/10/26/test/'

def test_versioned_article_deduplicates_preserves_history(database_path):
    prepare();source=PUBLISHERS[0];e=entry()
    # Stable publisher timestamp; fetched_at must not create a new revision.
    raw=html();fetch=lambda url:(raw,'text/html',url)
    first=service.store_article(e,source,'fixture',fetch)
    same=service.store_article(e,source,'fixture',fetch)
    second=service.store_article(e,source,'fixture',lambda url:(html(TEXT+' Уточнение источника.'),'text/html',url))
    assert first['status']=='new' and same['status']=='unchanged' and second['version']==2
    with db() as c:
        assert c.execute('SELECT count(*) FROM article_versions').fetchone()[0]==2
        old=c.execute('SELECT active,payload FROM sources WHERE id=?',(first['id'],)).fetchone()
        assert old['active']==0 and json.loads(old['payload'])['superseded_by']==second['id']
        assert c.execute('SELECT active FROM sources WHERE id=?',(second['id'],)).fetchone()[0]==1

def test_blocked_article_keeps_honest_feed_excerpt(database_path):
    prepare()
    def blocked(url):raise DomainError('SOURCE_UNAVAILABLE','Статья недоступна',502)
    e=entry();e['url']='https://tass.ru/ekonomika/12345678'
    result=service.store_article(e,PUBLISHERS[2],'fixture',blocked)
    with db() as c:p=json.loads(c.execute('SELECT payload FROM sources WHERE id=?',(result['id'],)).fetchone()[0])
    assert p['provenance']['content_extent']=='feed_excerpt' and p['extraction_method']=='rss_excerpt'
    assert not p['is_synthetic'] and p['publisher']=='ТАСС'

def test_temporary_block_never_replaces_full_article(database_path):
    prepare();e=entry();raw=html();first=service.store_article(e,PUBLISHERS[0],'fixture',lambda url:(raw,'text/html',url))
    def blocked(url):raise DomainError('SOURCE_TIMEOUT','Недоступно',502)
    result=service.store_article(e,PUBLISHERS[0],'fixture',blocked)
    assert result['status']=='unchanged' and result['id']==first['id']
    with db() as c:assert c.execute('SELECT count(*) FROM article_versions').fetchone()[0]==1

def test_blocked_without_excerpt_is_an_error(database_path):
    prepare();e=entry();e['description']=''
    def blocked(url):raise DomainError('SOURCE_UNAVAILABLE','Недоступно',502)
    with pytest.raises(DomainError):service.store_article(e,PUBLISHERS[0],'fixture',blocked)

def test_unknown_and_future_dates_not_invented(database_path):
    prepare();raw=('<h1>Тест</h1><div class="topic-body__content">'+TEXT+'</div>').encode();e=entry();e['published_at']=None
    with pytest.raises(DomainError,match='') as error:service.store_article(e,PUBLISHERS[0],'fixture',lambda url:(raw,'text/html',url))
    assert error.value.code=='NO_PUBLICATION_DATE'
    e['published_at']=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
    with pytest.raises(DomainError) as error:service.store_article(e,PUBLISHERS[0],'fixture',lambda url:(raw,'text/html',url))
    assert error.value.code=='FUTURE_PUBLICATION'

def test_cross_publisher_redirect_rejected(database_path):
    prepare();e=entry();e['description']=''
    with pytest.raises(DomainError) as error:service.store_article(e,PUBLISHERS[0],'fixture',lambda url:(html(),'text/html','https://other.example/article'))
    assert error.value.code=='PUBLISHER_REDIRECT'

def test_cycle_partial_failure_and_idempotent_queue(database_path,monkeypatch):
    prepare();monkeypatch.setattr(service,'scan_bank',lambda mode:{'status':'completed','changed':0})
    monkeypatch.setattr(service,'collect_source',lambda source,mode,fetcher:{'id':source['id'],'status':'completed','changed':1,'found':1,'processed':1,'items':[]} if source['id']!='ria' else (_ for _ in ()).throw(DomainError('SOURCE_TIMEOUT','Тестовая недоступность',502)))
    first=service.enqueue();assert service.enqueue()['cycle_id']==first['cycle_id']
    result=service.execute_cycle(first['cycle_id'])
    assert result['status']=='partial' and result['payload']['changed']==2
    assert len(result['payload']['sources'])==3 and service.snapshot()['revision']==1

def test_business_priority_and_foreign_feed_links(database_path):
    prepare();date=datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')
    raw=f'<rss><channel><item><title>Спорт</title><link>https://lenta.ru/news/sport/</link><pubDate>{date}</pubDate></item><item><title>Ключевая ставка снижена</title><link>https://lenta.ru/news/rate/</link><pubDate>{date}</pubDate><category>Экономика</category></item><item><title>Ключевая ставка</title><link>https://evil.example/news</link></item></channel></rss>'.encode()
    visited=[]
    def fetch(url):
        if url.endswith('/rss/news'):return raw,'application/rss+xml',url
        visited.append(url);return html(),'text/html',url
    result=service.collect_source(PUBLISHERS[0],'fixture',fetch,max_items=1)
    assert visited==['https://lenta.ru/news/rate/'] and result['found']==2

def test_worker_lease_prevents_duplicate_collectors(database_path):
    prepare();a,b=service.Worker(),service.Worker()
    assert a.lease() and not b.lease() and a.lease();a.stop();assert b.lease();b.stop()

def test_restart_preserves_queue_marks_interrupted(database_path):
    prepare();first=service.enqueue()
    with db() as c:c.execute("UPDATE collection_cycles SET status='running' WHERE id=?",(first['cycle_id'],))
    service.recover()
    assert service.get_cycle(first['cycle_id'])['status']=='interrupted'
    assert service.enqueue()['cycle_id']!=first['cycle_id']

def test_auto_analysis_only_real_fresh_news_and_no_repeat(database_path,monkeypatch):
    prepare();service.store_article(entry(),PUBLISHERS[0],'fixture',fetch_html)
    result=create_run('U_9250','demo',as_of=now(),automatic=True,background=False)
    with db() as c:
        run=c.execute('SELECT status,payload FROM runs WHERE id=?',(result['id'],)).fetchone()
        insights=[json.loads(r[0]) for r in c.execute('SELECT payload FROM insights WHERE run_id=?',(result['id'],))]
    assert run['status'].startswith('completed') and len(insights)==1 and not insights[0]['is_synthetic']
    assert insights[0]['analysis_method']=='Локальные правила'
    calls=[]
    def fake(uid,owner,role,mode,as_of,automatic):
        calls.append(uid)
        return create_run(uid,owner,role,mode,as_of,automatic=automatic,background=False)
    monkeypatch.setattr('backend.app.analysis.create_run',fake)
    service.schedule_analysis();service.schedule_analysis()
    assert len(calls)==0

def batch_result(context):
    from backend.app.analysis import fixture_interpret,get_profile
    result=[]
    with db() as c:
        for task in context['tasks']:
            event=json.loads(c.execute('SELECT payload FROM events WHERE id=?',(task['event_id'],)).fetchone()[0])
            data=fixture_interpret(get_profile(task['company_id']),event,task['role'],[])
            result.append({**task,'interpretation':data})
    return {'results':result}

def configure_free(monkeypatch):
    with db() as c:c.execute("INSERT OR REPLACE INTO settings VALUES('global',?)",(dumps({'mode':'live','auto_refresh':False,'daily_budget':500,'run_budget':25,'allow_external_context':True}),))
    # Only mock transports are used by these tests; this setting never applies to the working DB.
    for k,v in {'ALFA_MODEL_ALIAS':'Gemma 4 26B A4B','ALFA_MODEL_ID':'google/gemma-4-26b-a4b-it:free','ALFA_APPROVED_MODEL_ID':'google/gemma-4-26b-a4b-it:free','ALFA_API_BASE':'https://openrouter.ai/api/v1','ALFA_API_KEY':'synthetic-key','ALFA_INPUT_RUB_PER_MILLION':'0','ALFA_OUTPUT_RUB_PER_MILLION':'0','ALFA_FREE_ONLY':'1'}.items():monkeypatch.setenv(k,v)

def response_data():return {'facts':[],'company_links':[],'bank_view':{'status':'not_found'},'hypotheses':[],'actions':[],'missing_data':[]}
def catalog():return {'data':[{'id':'google/gemma-4-26b-a4b-it:free','pricing':{'prompt':'0','completion':'0'}}]}

def test_free_exact_model_price_guard_and_usage(database_path,monkeypatch):
    configure_free(monkeypatch);assert configuration()['ready']
    def handler(request):
        if request.method=='GET':return httpx.Response(200,json=catalog())
        body=json.loads(request.content)
        assert body['model']=='google/gemma-4-26b-a4b-it:free'
        assert body['provider']['max_price']=={'prompt':0,'completion':0,'request':0} and body['provider']['allow_fallbacks'] is False
        return httpx.Response(200,json={'usage':{'prompt_tokens':100,'completion_tokens':50,'cost':0},'choices':[{'message':{'content':json.dumps(response_data())}}]})
    provider=LiveProvider(25,httpx.MockTransport(handler));provider.interpret({})
    assert provider.spent==Decimal(0) and provider.calls==1
    monkeypatch.setenv('ALFA_MODEL_ID','google/some-paid-model');assert not configuration()['ready']
    monkeypatch.setenv('ALFA_MODEL_ID','google/gemma-4-26b-a4b-it:free');monkeypatch.setenv('ALFA_API_BASE','https://evil.example');assert not configuration()['ready']

@pytest.mark.parametrize('cost',[None,0.01])
def test_free_unconfirmed_cost_rejected(database_path,monkeypatch,cost):
    configure_free(monkeypatch)
    def handler(request):
        if request.method=='GET':return httpx.Response(200,json=catalog())
        usage={'prompt_tokens':100,'completion_tokens':50}
        if cost is not None:usage['cost']=cost
        return httpx.Response(200,json={'usage':usage,'choices':[{'message':{'content':json.dumps(response_data())}}]})
    with pytest.raises(DomainError) as error:LiveProvider(25,httpx.MockTransport(handler)).interpret({})
    assert error.value.code=='NONZERO_FREE_COST'

def test_free_catalog_price_change_blocks_request(database_path,monkeypatch):
    configure_free(monkeypatch);calls=[]
    def handler(request):
        calls.append(request.method);data=catalog();data['data'][0]['pricing']['prompt']='0.001';return httpx.Response(200,json=data)
    with pytest.raises(DomainError) as error:LiveProvider(25,httpx.MockTransport(handler)).interpret({})
    assert error.value.code=='FREE_MODEL_UNAVAILABLE' and calls==['GET']

def test_free_request_quota_is_durable_and_bounded(database_path,monkeypatch):
    monkeypatch.setenv('ALFA_FREE_DAILY_REQUESTS','2');monkeypatch.setenv('ALFA_FREE_MINUTE_REQUESTS','5')
    reserve_free_request();reserve_free_request()
    with pytest.raises(DomainError) as error:reserve_free_request()
    assert error.value.code=='FREE_DAILY_QUOTA'

def test_free_model_failure_publishes_explicit_rules_fallback(database_path,monkeypatch):
    prepare();configure_free(monkeypatch);service.store_article(entry(),PUBLISHERS[0],'live',fetch_html)
    def unavailable(self,context):raise DomainError('FREE_RATE_LIMIT','Тестовый лимит бесплатной модели',429)
    monkeypatch.setattr(LiveProvider,'interpret_batch',unavailable)
    result=create_run('U_9250','demo',mode='live',as_of=now(),automatic=True,background=False)
    with db() as c:
        run=json.loads(c.execute('SELECT payload FROM runs WHERE id=?',(result['id'],)).fetchone()[0])
        insight=json.loads(c.execute('SELECT payload FROM insights WHERE run_id=?',(result['id'],)).fetchone()[0])
        assert c.execute('SELECT count(*) FROM ledger').fetchone()[0]==0
    assert run['cost_rub']=='0' and run['model_errors'][0]['code']=='FREE_RATE_LIMIT'
    assert insight['analysis_method']=='Локальные правила' and 'llm_unavailable_local_rules' in insight['warnings']

def test_collection_api_auth_and_source_controls(client):
    data=client.get('/api/v1/collection').json();assert len(data['sources'])==3
    first=client.post('/api/v1/collection/refresh').json();assert first['cycle_id']==client.post('/api/v1/collection/refresh').json()['cycle_id']
    assert client.patch('/api/v1/collection/sources/ria',json={'enabled':False,'poll_seconds':300}).status_code==200
    assert client.patch('/api/v1/collection/sources/ria',json={'enabled':True,'poll_seconds':1}).status_code==422
    assert client.post('/api/v1/companies/U_9250/watch',json={'role':'cfo'}).status_code==200
    assert client.post('/api/v1/companies/foreign/watch',json={'role':'ceo'}).status_code==403
    assert client.patch('/api/v1/settings',json={'daily_budget':500,'run_budget':25,'mode':'fixture','auto_refresh':True}).status_code==409
    assert client.post('/api/v1/collection/refresh',headers={'X-CSRF-Token':'invalid'}).status_code==403

def test_verified_model_output_cached_across_refreshes(database_path,monkeypatch):
    from backend.app.analysis import fixture_interpret,get_profile
    prepare();configure_free(monkeypatch)
    service.store_article(entry(),PUBLISHERS[0],'live',fetch_html);calls=[]
    def model(self,context):
        calls.append(1);self.calls+=1
        return batch_result(context)
    monkeypatch.setattr(LiveProvider,'interpret_batch',model)
    first=create_run('U_9250','demo',mode='live',as_of=now(),automatic=True,background=False)
    second=create_run('U_9250','demo',mode='live',as_of=now(),automatic=True,background=False)
    with db() as c:
        rows=[json.loads(r[0]) for r in c.execute('SELECT payload FROM runs ORDER BY created_at')]
    assert len(calls)==1 and rows[-1]['model_cache_hits']==1
    assert rows[-1]['model_calls']==0 and rows[-1]['cost_rub']=='0'

def test_invalid_batch_facts_fall_back_without_repair_request(database_path,monkeypatch):
    from backend.app.analysis import fixture_interpret,get_profile
    prepare();configure_free(monkeypatch);service.store_article(entry(),PUBLISHERS[0],'live',fetch_html);calls=[]
    def model(self,context):
        calls.append(1)
        data=batch_result(context)
        data['results'][0]['interpretation']['facts'][0]['text']='Компания гарантированно заработает миллиард рублей.'
        return data
    monkeypatch.setattr(LiveProvider,'interpret_batch',model)
    result=create_run('U_9250','demo',mode='live',as_of=now(),automatic=True,background=False)
    with db() as c:
        row=c.execute('SELECT status,payload FROM runs WHERE id=?',(result['id'],)).fetchone()
        insight=json.loads(c.execute('SELECT payload FROM insights WHERE run_id=?',(result['id'],)).fetchone()[0])
        assert c.execute('SELECT count(*) FROM interpretation_cache').fetchone()[0]==0
    assert len(calls)==1 and row['status']=='completed_with_limits'
    assert insight['facts'][0]['text']!='Компания гарантированно заработает миллиард рублей.'
    assert insight['analysis_method']=='Локальные правила'

def test_only_manual_tick_collects_one_batch_and_does_not_repeat(database_path,monkeypatch):
    from backend.app import analysis
    prepare();monkeypatch.setattr(service,'scan_bank',lambda mode:{'status':'completed','changed':0})
    with db() as c:c.execute("INSERT OR REPLACE INTO settings VALUES('global',?)",(dumps({'mode':'fixture','auto_refresh':True,'daily_budget':500,'run_budget':25}),))
    date=datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')
    def fetch(url):
        if any(url==p['feed_url'] for p in PUBLISHERS):
            publisher=next(p for p in PUBLISHERS if p['feed_url']==url)
            body=f'<rss><channel><item><title>Ключевая ставка снижена</title><link>{publisher["homepage"]}test-article</link><pubDate>{date}</pubDate><description>{TEXT}</description></item></channel></rss>'
            return body.encode(),'application/rss+xml',url
        return html(),'text/html',url
    original_cycle=service.execute_cycle
    monkeypatch.setattr(service,'execute_cycle',lambda uid:original_cycle(uid,fetch))
    from backend.app import batches
    original_create=batches.create_batch
    monkeypatch.setattr(batches,'create_batch',lambda *a,**kw:original_create(*a,**kw,background=False))
    worker=service.Worker()
    try:
        worker.tick();worker.tick()
        with db() as c:
            assert c.execute('SELECT count(*) FROM collection_cycles').fetchone()[0]==0
        service.enqueue('manual','demo',['U_9250','U_1941','U_106783'],analyze=True)
        worker.tick();worker.tick()
        with db() as c:
            assert c.execute('SELECT count(*) FROM collected_articles').fetchone()[0]==3
            assert c.execute('SELECT count(*) FROM runs').fetchone()[0]==3
            assert c.execute('SELECT count(*) FROM collection_cycles').fetchone()[0]==1
            assert all(r[0].startswith('completed') for r in c.execute('SELECT status FROM runs'))
    finally:worker.stop()

def test_bank_watch_official_origin_and_unknown_date(client,monkeypatch):
    from backend.app import bank_watch
    assert client.post('/api/v1/collection/bank-watches',json={'url':'https://fake-bank.example/report.pdf','title':'Чужой PDF'}).status_code==422
    watch=client.post('/api/v1/collection/bank-watches',json={'url':'https://alfabank.ru/report.pdf','title':'Проверка'}).json()
    from backend.app.db import ROOT
    raw=(ROOT/'data/fixtures/test-documents/synthetic-bank-01.pdf').read_bytes()
    # Synthetic PDF is returned only by this local transport double, never by a live bank watch.
    result=bank_watch.scan('fixture',lambda url:(raw,'application/pdf',url))
    assert result['changed']==1 and result['items'][0]['date_unknown']
    with db() as c:
        source=json.loads(c.execute('SELECT payload FROM sources WHERE id=?',(result['items'][0]['id'],)).fetchone()[0])
    assert source['published_at'] is None and not source['analysis_eligible']
    assert bank_watch.scan('fixture',lambda url:(raw,'application/pdf',url))['changed']==0
    assert client.delete('/api/v1/collection/bank-watches/'+watch['id']).status_code==200

def test_synthetic_publisher_files_are_extractable():
    from backend.app.db import ROOT
    for publisher in PUBLISHERS:
        raw=(ROOT/'data/fixtures/news-sites'/(publisher['id']+'.html')).read_bytes()
        parsed=extract_article(raw,publisher['homepage'])
        assert 'Учебный материал' in parsed['text'] and 'Тестовое меню' not in parsed['text']

def test_company_context_never_sent_without_explicit_permission(database_path,monkeypatch):
    prepare();configure_free(monkeypatch)
    with db() as c:c.execute("UPDATE settings SET payload=? WHERE id='global'",(dumps({'mode':'live','auto_refresh':False,'daily_budget':500,'run_budget':25,'allow_external_context':False}),))
    service.store_article(entry(),PUBLISHERS[0],'live',fetch_html)
    def forbidden(*a,**kw):raise AssertionError('No external model call is permitted')
    monkeypatch.setattr(LiveProvider,'interpret_batch',forbidden)
    with pytest.raises(DomainError) as error:create_run('U_9250','demo',mode='live',as_of=now(),background=False)
    assert error.value.code=='EXTERNAL_CONTEXT_CONSENT'
    with db() as c:assert c.execute('SELECT count(*) FROM model_requests').fetchone()[0]==0

def test_external_context_omits_financial_amounts_payments_and_comments(database_path):
    from backend.app.analysis import interpretation_context,get_profile
    prepare();document=service.store_article(entry(),PUBLISHERS[0],'fixture',fetch_html)
    with db() as c:event=json.loads(c.execute('SELECT payload FROM events WHERE id=?',('event-'+document['id'],)).fetchone()[0])
    profile=get_profile('U_9250');profile['clarification']={'comment':'PRIVATE COMMENT','routes':['PRIVATE ROUTE']}
    profile['field_evidence']['revenue']={'id':'private-revenue','value':'PRIVATE AMOUNT'}
    text=dumps(interpretation_context(profile,event,[],now(),'ceo'))
    assert all(value not in text for value in ('PRIVATE COMMENT','PRIVATE ROUTE','PRIVATE AMOUNT','revenue_decimal','payment_edges'))
