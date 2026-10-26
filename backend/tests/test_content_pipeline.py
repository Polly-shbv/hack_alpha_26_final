import copy
import json
import pytest
from backend.app import analysis, ingestion, feeds
from backend.app.content import extract_content, normalize_date
from backend.app.analysis import AS_OF, create_run, get_profile, rank, fixture_interpret, validate_claims
from backend.app.db import db, dumps
from backend.app.errors import DomainError
from backend.app.ingestion import add_document, search_bank
from backend.app.seed import seed
from backend.app.stories import select_events

DATE='2026-10-22T09:00:00+00:00'
RATE='Ключевая ставка снижена по итогам нового заседания. В публикации описано снижение стоимости новых кредитов; условия действующих договоров зависят от их формулы пересмотра.'
COPPER='Цена меди снизилась по данным нового обзора. Изменение относится к биржевой цене; состав закупок компании и условия договоров в публикации не раскрыты.'


def event_for(document):
    with db() as connection:
        return next(json.loads(row[0]) for row in connection.execute('SELECT payload FROM events') if document['id'] in json.loads(row[0])['source_document_ids'])


@pytest.mark.parametrize('title,text,topic',[
    ('Медь',COPPER,'copper'),('Финансы',RATE,'rate'),
    ('Перевозки','Контейнерные перевозки подорожали; изменены сроки доставки.','logistics'),
    ('Спрос','Розничные продажи и потребительский спрос снизились.','demand'),
    ('Кадры','Зарплаты и рынок труда: опубликованы новые данные.','labor'),
    ('Клиника','Медицинский центр открыл новый кабинет.','no-bank'),
    ('Поставки','Поставки оборудования завершены по старому договору.','no-bank'),
])
def test_classification_unseen_and_false_positive_boundaries(title,text,topic):
    result=extract_content(title,text)
    assert result['topic']==topic
    joined=title+'\n'+text
    for evidence in result['topic_evidence']:
        assert joined[evidence['start']:evidence['end']]==evidence['quote']
    assert result['summary'] in text


def test_dates_require_timezone_and_accept_rss():
    assert normalize_date('Thu, 22 Oct 2026 12:00:00 +0300')==DATE
    assert normalize_date('2026-10-22') is None
    assert normalize_date('2026-10-22T12:00:00') is None
    assert normalize_date('invalid') is None


def test_html_jsonld_metadata_and_exact_facts(database_path,monkeypatch):
    raw=('<html><title>Шапка сайта</title><nav>реклама</nav><script type="application/ld+json">'+json.dumps({'@context':'https://schema.org','@type':'NewsArticle','headline':'Новое решение о ставке','datePublished':DATE,'articleBody':RATE,'publisher':{'name':'Тестовая редакция'}})+'</script><article>неполная версия</article></html>').encode()
    monkeypatch.setattr(ingestion,'fetch_public',lambda url:(raw,'text/html','https://example.org/article'))
    document=ingestion.import_url('https://example.org/article')
    with db() as connection:
        source=json.loads(connection.execute('SELECT payload FROM sources WHERE id=?',(document['id'],)).fetchone()[0])
    assert source['title']=='Новое решение о ставке' and source['text']==RATE
    assert source['publisher']=='Тестовая редакция' and source['published_at']==DATE
    assert source['analysis_eligible'] and not source['bank_verified']
    assert not source['is_synthetic']
    assert event_for(document)['topic']=='rate'


def test_rank_depends_on_features_not_known_company_ids(database_path):
    seed();p=get_profile('U_106783')
    e={'title':'Новый обзор меди','fact':COPPER,'topic':'copper','published_at':DATE}
    first=rank(p,e,'cfo',AS_OF)
    clone=copy.deepcopy(p);clone['id']='NEVER_SEEN_COMPANY'
    for feature in clone['field_evidence'].values():feature['id']=feature['id'].replace(p['id'],clone['id'])
    second=rank(clone,e,'cfo',AS_OF)
    assert first['score']==second['score'] and first['components']==second['components']
    assert first['score']>rank(get_profile('U_1941'),e,'cfo',AS_OF)['score']
    unrelated={'title':'Новый медицинский кабинет','fact':'Медицинский центр открыл кабинет.','topic':'no-bank','published_at':DATE}
    assert rank(p,unrelated,'ceo',AS_OF)['score']<30


def test_unseen_interpretation_has_new_direction_and_role_actions(database_path):
    seed();document=add_document('Новое снижение ставки',RATE,published_at=DATE,owner='public');e=event_for(document)
    p=get_profile('U_9250');cfo=fixture_interpret(p,e,'cfo');treasury=fixture_interpret(p,e,'treasury')
    assert cfo['hypotheses'][0]['kind']=='opportunity'
    assert 'снижение ставки' in cfo['hypotheses'][0]['text']
    assert cfo['actions'][0]['text']!=treasury['actions'][0]['text']
    assert validate_claims(cfo,p,e,AS_OF,'fixture')['facts'][0]['text'] in RATE
    assert 'Тип ставки и формула пересмотра' in cfo['missing_data']


def test_unseen_copper_opportunity_is_conditional(database_path):
    assert extract_content('Медь','Цена меди не подешевела.')['direction']=='unchanged'
    seed();document=add_document('Новая цена меди',COPPER,published_at=DATE,owner='public');e=event_for(document)
    result=fixture_interpret(get_profile('U_106783'),e)
    assert result['hypotheses'][0]['kind']=='opportunity'
    assert 'Если медь входит' in result['hypotheses'][0]['text']
    assert result['hypotheses'][0]['conditions'] and result['hypotheses'][0]['field_evidence_ids']
    wholesale=fixture_interpret(get_profile('U_1941'),e)
    assert wholesale['hypotheses']==[]


def test_duplicate_is_scoped_to_owner_and_kind(database_path):
    a=add_document('Обзор',RATE,owner='one',published_at=DATE)
    assert add_document('Повтор',RATE,owner='one',published_at=DATE)['status']=='duplicate'
    b=add_document('Обзор',RATE,owner='two',published_at=DATE)
    bank=add_document('Обзор',RATE,owner='one',kind='bank',published_at=DATE)
    assert len({a['id'],b['id'],bank['id']})==3
    with db() as connection:
        visible=select_events(connection,'fixture','one',AS_OF)
    assert len(visible)==1 and visible[0]['source_document_ids']==[a['id']]


def test_grouped_sources_survive_deactivation_without_future_leak(database_path):
    a=add_document('Новое решение о ключевой ставке',RATE+' Подробности будут доступны в полном отчёте.',owner='demo',published_at='2026-10-22T18:00:00+00:00')
    b=add_document('Новое решение о ключевой ставке',RATE+' Подробности опубликованы в полном отчёте.',owner='demo',published_at=DATE)
    e=event_for(a);assert len(e['source_document_ids'])==2
    with db() as connection:
        events=select_events(connection,'fixture','demo','2026-10-22T12:00:00+00:00')
        assert events[0]['source_document_ids']==[b['id']]
        assert events[0]['published_at']==DATE
        connection.execute('UPDATE sources SET active=0 WHERE id=?',(a['id'],))
    with db() as connection:
        assert select_events(connection,'fixture','demo',AS_OF)[0]['source_document_ids']==[b['id']]


def test_conflicting_numbers_kept_with_both_evidence_sources(database_path):
    seed();body='Ключевая ставка сохранена на уровне {}% годовых. В сообщении указано, что условия действующих кредитных договоров требуют отдельной проверки.'
    a=add_document('Учебное решение о ставке',body.format(14),published_at=DATE)
    b=add_document('Учебное решение о ставке',body.format(15),published_at=DATE)
    with db() as connection:
        e=next(e for e in select_events(connection,'fixture','demo',AS_OF) if a['id'] in e['source_document_ids'])
    assert e['conflicts'] and len(e['source_document_ids'])==2
    payload=fixture_interpret(get_profile('U_9250'),e)
    assert len(payload['facts'])==2 and any('противореч' in value for value in payload['missing_data'])
    validate_claims(payload,get_profile('U_9250'),e,AS_OF,'fixture')


def test_rss_and_atom_dates_and_relative_links():
    rss=f'<rss><channel><item><title>Новость</title><link>/news/one</link><pubDate>Thu, 22 Oct 2026 12:00:00 +0300</pubDate></item></channel></rss>'.encode()
    item=feeds.parse_feed(rss,'https://example.org/feed')[0]
    assert item['url']=='https://example.org/news/one' and item['published_at']==DATE
    atom=f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Новость</title><link href="/two"/><published>{DATE}</published><content>{RATE}</content></entry></feed>'.encode()
    assert feeds.parse_feed(atom,'https://example.org/feed')[0]['content']==RATE


@pytest.mark.parametrize('raw',[b'<!DOCTYPE rss [<!ENTITY x "evil">]><rss/>',b'<html/>',b'<rss>',b'\xff\xfe<\x00!\x00D\x00O\x00C\x00T\x00Y\x00P\x00E\x00'])
def test_bad_or_entity_feeds_rejected(raw):
    with pytest.raises(DomainError):feeds.parse_feed(raw,'https://example.org/feed')


def test_feed_collects_partial_results_and_rechecks_each_link(database_path,monkeypatch):
    rss=b'<rss><channel><item><title>One</title><link>https://example.org/one</link></item><item><title>Two</title><link>http://127.0.0.1/private</link></item></channel></rss>'
    monkeypatch.setattr(feeds,'fetch_public',lambda url:(rss,'application/rss+xml',url))
    checked=[]
    def safe(url):
        checked.append(url)
        if '127.0.0.1' in url:raise DomainError('UNSAFE_URL','blocked')
    monkeypatch.setattr(feeds,'public_target',safe)
    monkeypatch.setattr(feeds,'import_url',lambda url,**kw:{'id':'test','status':'ready'})
    report=feeds.import_feed('https://example.org/feed')
    assert report['processed']==1 and report['items'][1]['error']['code']=='UNSAFE_URL' and len(checked)==2


def test_agent_actually_reinterprets_new_bank_evidence(database_path,monkeypatch):
    seed();quote='Ключевая ставка и условия финансирования остаются важным фактором стоимости новых кредитов.'
    document=add_document('Тестовая подтверждённая аналитика',quote,kind='bank',published_at=DATE,synthetic=False,bank_verified=True,owner='public')
    chunks=search_bank('ключев',AS_OF,'fixture')
    assert chunks and chunks[0]['document_id']==document['id']
    monkeypatch.setattr(analysis,'search_bank',lambda query,*args:chunks if 'ключев процентн' in query else [])
    run=create_run('U_9250','demo',background=False)
    with db() as connection:
        row=connection.execute('SELECT status,payload FROM runs WHERE id=?',(run['id'],)).fetchone()
        insight=json.loads(connection.execute("SELECT payload FROM insights WHERE run_id=? AND event_id='fixture-rate-001'",(run['id'],)).fetchone()[0])
    assert row['status'].startswith('completed') and insight['bank_view']['status']=='supported'
    assert quote in insight['bank_view']['claims'][0]['text']
    trace=json.loads(row['payload'])['steps'][-1]['result']['adaptive_searches']
    assert any(item.get('new_verified_chunks',0)>0 for item in trace)


def test_invalid_generation_is_repaired_before_publication(database_path,monkeypatch):
    seed();original=analysis.fixture_interpret;calls=[]
    def generated(*args):
        payload=original(*args)
        if not calls:payload['facts'][0]['text']='Несуществующий рост выручки на 40%'
        calls.append(1)
        return payload
    monkeypatch.setattr(analysis,'fixture_interpret',generated)
    run=create_run('U_9250','demo',background=False)
    with db() as connection:
        row=connection.execute('SELECT status,payload FROM runs WHERE id=?',(run['id'],)).fetchone()
        published=' '.join(r[0] for r in connection.execute('SELECT payload FROM insights WHERE run_id=?',(run['id'],)))
    assert row['status'].startswith('completed') and 'Несуществующий' not in published
    assert any(step.get('tool')=='repair_interpretation' for step in json.loads(row['payload'])['steps'][-1]['result']['adaptive_searches'])


def test_same_article_keeps_distinct_publishers_and_repairs_missing_date(database_path):
    a=add_document('Новое решение',RATE,origin_url='https://first.example/news',published_at=None)
    b=add_document('Новое решение',RATE,origin_url='https://second.example/news',published_at=DATE)
    assert a['id']!=b['id']
    assert add_document('Новое решение',RATE,origin_url='https://first.example/news',published_at=DATE)['status']=='duplicate'
    with db() as connection:
        source=json.loads(connection.execute('SELECT payload FROM sources WHERE id=?',(a['id'],)).fetchone()[0])
        visible=select_events(connection,'fixture','demo',AS_OF)
    assert source['analysis_eligible'] and source['metadata_history'][0]['before'] is None
    assert any(a['id'] in e['source_document_ids'] for e in visible)


def test_pdf_ocr_reprocessing_preserves_page_and_original(database_path,tmp_path,monkeypatch):
    import fitz
    from backend.app import ocr
    document=fitz.open();document.new_page();raw=document.tobytes();document.close()
    monkeypatch.setattr(ingestion,'ROOT',tmp_path)
    monkeypatch.setattr(ocr,'recognize_pdf',lambda raw,numbers:({1:RATE},{'engine':'test-local-ocr','text_reviewed':False,'warning':'Сверить с оригиналом'}))
    result=ingestion.import_pdf(raw,'scan.pdf',ocr=True,kind='bank',published_at=DATE)
    with db() as connection:source=json.loads(connection.execute('SELECT payload FROM sources WHERE id=?',(result['id'],)).fetchone()[0])
    assert source['text_status']=='ready' and source['extraction_method']=='test-local-ocr'
    assert source['chunks'][0]['page_number']==1 and not source['bank_verified']
    assert (database_path.parent/'uploads'/(result['id']+'.pdf')).read_bytes()==raw


def test_corpus_requires_verified_manifest_and_checksums(database_path,tmp_path):
    import hashlib
    from backend.app.corpus import CASE_FOLDER,load_case_corpus
    parsed={'original_sha256':'sha-of-public-original','pages':[{'number':1,'text':RATE}],'ocr':{'engine':'test-ocr','text_reviewed':False}}
    sidecar=tmp_path/'one.ocr.json';sidecar.write_text(json.dumps(parsed))
    entry={'file':'one.pdf','ocr_file':sidecar.name,'sha256':parsed['original_sha256'],'ocr_sha256':hashlib.sha256(sidecar.read_bytes()).hexdigest(),'source_confirmed':False,'title':'Аналитика из кейса','published_at':DATE,'origin_url':'https://drive.google.com/file/d/test/view'}
    manifest={'case_folder_url':CASE_FOLDER,'items':[entry]};path=tmp_path/'manifest.json';path.write_text(json.dumps(manifest))
    with pytest.raises(DomainError,match='Подтвердите'):load_case_corpus(tmp_path)
    entry['source_confirmed']=True;path.write_text(json.dumps(manifest));report=load_case_corpus(tmp_path)
    with db() as connection:source=json.loads(connection.execute('SELECT payload FROM sources WHERE id=?',(report['items'][0]['id'],)).fetchone()[0])
    assert source['bank_verified'] and not source['is_synthetic'] and source['provenance']['type']=='case_reference'
    sidecar.write_text('{}')
    with pytest.raises(DomainError,match='Контрольная'):load_case_corpus(tmp_path)


def test_rss_endpoint_async_validation_and_report(client,monkeypatch):
    import time
    from backend.app import security
    monkeypatch.setattr(security,'public_target',lambda url:None)
    monkeypatch.setattr(feeds,'import_feed',lambda **kwargs:{'items':[],'processed':0,'found':0})
    assert client.post('/api/v1/imports/feed',json={'url':'https://example.org/feed','max_items':11}).status_code==422
    response=client.post('/api/v1/imports/feed',json={'url':'https://example.org/feed','max_items':3})
    assert response.status_code==202
    for _ in range(100):
        report=client.get('/api/v1/imports/'+response.json()['job_id']).json()
        if report['status'] not in ('queued','running'):break
        time.sleep(.01)
    assert report['status']=='ready' and report['payload']['processed']==0


def test_opportunity_is_exported_without_false_empty_notice(client):
    document=add_document('Другое снижение ставки',RATE,published_at=DATE,owner='public')
    e=event_for(document);run=create_run('U_9250','demo',background=False)
    with db() as connection:
        insight=json.loads(connection.execute('SELECT payload FROM insights WHERE run_id=? AND event_id=?',(run['id'],e['id'])).fetchone()[0])
    assert insight['impact']=='opportunity'
    exported=client.get('/api/v1/insights/'+insight['id']+'/export').text
    assert 'Возможность:' in exported and 'Недостаточно оснований для выделения возможностей' not in exported


def test_bad_tls_certificate_is_reported_without_unsafe_retry(monkeypatch):
    import ssl
    from urllib.parse import urlsplit
    from backend.app import security
    closed=[]
    class Connection:
        def request(self,*args,**kwargs):raise ssl.SSLCertVerificationError('untrusted certificate')
        def close(self):closed.append(True)
    monkeypatch.setattr(security,'public_target',lambda url:(urlsplit(url),'8.8.8.8'))
    monkeypatch.setattr(security,'PinnedHTTPS',lambda *args:Connection())
    with pytest.raises(DomainError) as error:security.fetch_public('https://example.org/feed')
    assert error.value.code=='SOURCE_TLS' and closed==[True]


def test_updated_profile_cannot_reuse_old_canonical_financing_text(database_path):
    seed();profile=get_profile('U_9250');profile['products']['credit']=False;profile['products']['leasing']=False
    with db() as connection:event=json.loads(connection.execute("SELECT payload FROM events WHERE id='fixture-rate-001'").fetchone()[0])
    result=fixture_interpret(profile,event)
    assert not result['hypotheses']
    assert all('В профиле отмечен' not in link['text'] for link in result['company_links'])


def test_bank_retrieval_prefers_specific_title_and_diverse_documents(database_path):
    seed()
    noisy='Финансирование и ключевая ставка обсуждаются в обзоре долгового рынка. '*40
    add_document('Долговой рынок',noisy,kind='bank',published_at=DATE,synthetic=False,bank_verified=True,owner='public')
    targeted=add_document('Ключевая ставка: условия финансирования','Мы ожидаем сохранения ключевой ставки; условия финансирования зависят от инфляции и спроса.',kind='bank',published_at=DATE,synthetic=False,bank_verified=True,owner='public')
    matches=search_bank('ставк финансирован',AS_OF,'fixture')
    assert matches[0]['document_id']==targeted['id']
    doc=add_document('Новое снижение ставки',RATE,published_at=DATE,owner='public');event=event_for(doc)
    payload=fixture_interpret(get_profile('U_9250'),event,bank_chunks=matches)
    assert len(payload['bank_view']['document_ids'])==len(set(payload['bank_view']['document_ids']))==2
    assert payload['bank_view']['claims'][0]['kind']=='expert_view'

@pytest.mark.parametrize('title,text',[
    ('Над Китаем заметили след ракеты','Необычное свечение заметили жители после запуска спутника.'),
    ('Китай поблагодарили за сохранение памятников','Посольство рассказало об уходе за историческими захоронениями.'),
    ('Вратарь получил награду','Матч закончился победой хозяев. Материалы по теме: поставки меди из Китая.'),
    ('Угроза нефтяной платформе','Устройство повредило нефтяную платформу. '+('Подробности расследования. '*90)+'События этой новости вымышлены, это художественный проект.'),
])
def test_nonbusiness_mentions_and_fiction_do_not_enter_company_feed(database_path,title,text):
    from backend.app.content import extract_content
    from backend.app.relevance import rank
    seed();profile=get_profile('U_106783')
    content=extract_content(title,text)
    # Fiction disclosure may be far beyond the model's short excerpt.
    if 'вымышлены' in text:assert content['fictional']
    event={'title':title,'fact':text,'published_at':'2026-10-22T00:00:00+00:00','extraction':content,'topic':content['topic']}
    assert rank(profile,event,'ceo','2026-10-23T12:00:00+00:00')['selection']=='excluded'


def test_trade_mechanism_survives_country_noise_filter(database_path):
    from backend.app.content import extract_content
    from backend.app.relevance import rank
    seed();profile=get_profile('U_106783')
    content=extract_content('Китай вводит экспортные ограничения','Ограничения экспорта затронут поставки промышленного оборудования. Товарные коды требуют проверки.')
    event={'title':'Экспортные ограничения Китая','fact':'Поставки оборудования','published_at':'2026-10-22T00:00:00+00:00','extraction':content,'topic':content['topic']}
    assert content['business_eligible'] and rank(profile,event,'ceo','2026-10-23T12:00:00+00:00')['score']>=30


def test_private_garden_advice_not_commercial_construction_exposure(database_path):
    seed();profile=get_profile('U_9250')
    content=extract_content('Разъяснено оформление земли за пределами участка','Расширить участок можно для садоводства, огородничества или личного подсобного хозяйства. Для жителей действуют муниципальные ограничения площади.')
    event={'title':'Порядок для садоводов','fact':'Оформление земли','published_at':DATE,'extraction':content,'topic':content['topic']}
    assert content['consumer_only'] and rank(profile,event,'ceo',AS_OF)['score']==0
