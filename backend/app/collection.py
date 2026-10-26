"""Durable local collection loop. SQLite is the queue; no Redis or cron required."""
import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime,timedelta,timezone
from urllib.parse import urlsplit
from .db import db,dumps,now,setting,ROOT
from .errors import DomainError
from .feeds import parse_feed
from .security import fetch_public
from .publishers import PUBLISHERS,extract_article,article_url
from .ingestion import add_document
from .content import extract_content

POLL_SECONDS=300

def state(key,default=None):
    with db() as c:row=c.execute('SELECT payload FROM collection_state WHERE id=?',(key,)).fetchone()
    return json.loads(row[0]) if row else default

def put_state(key,value):
    with db() as c:c.execute('INSERT INTO collection_state VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(key,dumps(value)))

def initialize():
    config=setting();config['auto_refresh']=False
    with db() as c:
        c.execute("INSERT INTO settings VALUES('global',?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",(dumps(config),))
        for row in c.execute("SELECT id,payload FROM collection_cycles WHERE status='queued'").fetchall():
            if json.loads(row['payload']).get('trigger') in ('schedule','settings'):
                c.execute("UPDATE collection_cycles SET status='cancelled',ended_at=? WHERE id=?",(now(),row['id']))
        for publisher in PUBLISHERS:
            value={k:v for k,v in publisher.items() if k not in ('selectors','hosts')}
            value.update(enabled=True,poll_seconds=POLL_SECONDS,last_checked=None,last_success=None,next_check=None,last_error=None,kind='news',found=0,processed=0)
            c.execute('INSERT OR IGNORE INTO subscriptions VALUES(?,?)',(value['id'],dumps(value)))
        for uid in ('U_9250','U_1941','U_106783'):
            c.execute('INSERT OR IGNORE INTO analysis_targets VALUES(?,?,?,?)',(uid,'ceo','demo',dumps({'enabled':True,'fingerprint':None,'run_id':None})))

def subscriptions():
    with db() as c:return [json.loads(r[0]) for r in c.execute('SELECT payload FROM subscriptions ORDER BY id')]

def patch_subscription(sid,enabled,poll_seconds):
    with db() as c:
        row=c.execute('SELECT payload FROM subscriptions WHERE id=?',(sid,)).fetchone()
        if not row:raise DomainError('NOT_FOUND','Источник не найден',404)
        p=json.loads(row[0]);p.update(enabled=enabled,poll_seconds=poll_seconds,next_check=None)
        c.execute('UPDATE subscriptions SET payload=? WHERE id=?',(dumps(p),sid))
    return p

def enqueue(trigger='manual',owner='demo',company_ids=None,role='ceo',analyze=False):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        old=c.execute("SELECT id,status FROM collection_cycles WHERE status IN ('queued','running') ORDER BY started_at LIMIT 1").fetchone()
        if old:return {'cycle_id':old['id'],'status':old['status']}
        uid='collect-'+uuid.uuid4().hex[:16]
        c.execute('INSERT INTO collection_cycles VALUES(?,?,?,?,?)',(uid,'queued',now(),None,dumps({'trigger':trigger,'owner':owner,'sources':[],'changed':0,'company_ids':company_ids or [],'role':role,'analyze':analyze,'mode':setting()['mode']})))
    return {'cycle_id':uid,'status':'queued'}

def get_cycle(uid):
    with db() as c:row=c.execute('SELECT * FROM collection_cycles WHERE id=?',(uid,)).fetchone()
    if not row:raise DomainError('NOT_FOUND','Обновление не найдено',404)
    return {**dict(row),'payload':json.loads(row['payload'])}

def snapshot(owner='demo'):
    with db() as c:
        row=c.execute('SELECT * FROM collection_cycles ORDER BY started_at DESC LIMIT 1').fetchone()
        count=c.execute("SELECT count(*) FROM sources WHERE mode=? AND kind='news' AND active=1 AND json_extract(payload,'$.is_synthetic')=0 AND owner IN (?,?)",(setting()['mode'],'public',owner)).fetchone()[0]
        targets=[{**dict(r),'payload':json.loads(r['payload'])} for r in c.execute('SELECT * FROM analysis_targets')]
        latest=c.execute("SELECT max(ended_at) FROM runs WHERE status IN ('completed','completed_with_limits')").fetchone()[0]
        batch=c.execute('SELECT * FROM analysis_batches WHERE owner=? ORDER BY created_at DESC LIMIT 1',(owner,)).fetchone()
    return {'enabled':False,'manual_only':True,'batch':{**dict(batch),'payload':json.loads(batch['payload'])} if batch else None,'worker_enabled':os.getenv('ALFA_COLLECTION_WORKER','1')!='0',
            'worker':state('heartbeat'),'sources':subscriptions(),'articles':count,'latest_analysis_at':latest,
            'cycle':{**dict(row),'payload':json.loads(row['payload'])} if row else None,'targets':targets,
            'bank':state('bank_scan'),'bank_watches':__import__('backend.app.bank_watch',fromlist=['watches']).watches(),
            'model':state('model_status'),'mode':setting()['mode'],'revision':state('revision',0)}

def track_target(uid,role,owner):
    with db() as c:c.execute('INSERT OR IGNORE INTO analysis_targets VALUES(?,?,?,?)',(uid,role,owner,dumps({'enabled':True,'fingerprint':None,'run_id':None})))

def business_priority(item):
    topic=extract_content(item['title'],item.get('description',''))['topic']
    business=any(word in (item.get('category') or '').lower() for word in ('эконом','бизнес','финанс','строител','промыш','энерг','недвиж'))
    return 0 if business else 1 if topic not in ('general','other','no-bank','conflict') else 2

def store_article(entry,source,mode,fetcher=fetch_public):
    url=article_url(entry['url']);timestamp=now()
    with db() as c:old=c.execute('SELECT * FROM collected_articles WHERE url=? AND mode=?',(url,mode)).fetchone()
    try:
        raw,_,final=fetcher(url)
        # Each fetch/redirect is DNS-pinned. The final publisher must still match the subscription.
        if (urlsplit(final).hostname or '').removeprefix('www.') != (urlsplit(source['homepage']).hostname or '').removeprefix('www.'):
            raise DomainError('PUBLISHER_REDIRECT','Статья перенаправлена на другой сайт',422)
        extracted=extract_article(raw,final,entry)
    except DomainError as error:
        description=entry.get('description','').strip()
        if len(description)<60:raise
        extracted={'title':entry['title'],'text':entry['title']+'. '+description,'published_at':entry.get('published_at'),
                   'publisher':source['name'],'extraction_method':'rss_excerpt',
                   'provenance':{'type':'external_feed','content_extent':'feed_excerpt','author':entry.get('author'),
                                 'feed_url':source['feed_url'],'warning':'Полный текст недоступен; доступен только анонс RSS',
                                 'article_error':error.code}}
        # A temporary failure must never replace an already collected full article with an excerpt.
        if old and json.loads(old['payload']).get('content_extent')=='full_article':
            with db() as c:c.execute('UPDATE collected_articles SET last_checked=? WHERE url=? AND mode=?',(timestamp,url,mode))
            return {'url':url,'id':old['document_id'],'status':'unchanged','warning':'Полный текст временно недоступен; сохранена предыдущая версия'}
    date=extracted.get('published_at')
    if not date:raise DomainError('NO_PUBLICATION_DATE','Дата публикации не установлена; материал не включён в актуальную ленту',422)
    published=datetime.fromisoformat(date)
    if published>datetime.now(timezone.utc)+timedelta(minutes=5):raise DomainError('FUTURE_PUBLICATION','Дата публикации находится в будущем',422)
    if published<datetime.now(timezone.utc)-timedelta(days=7):return {'url':url,'status':'old'}
    digest=hashlib.sha256(dumps([extracted['title'],extracted['text'],date,extracted.get('provenance',{}).get('modified_at')]).encode()).hexdigest()
    if old and old['content_sha']==digest:
        with db() as c:c.execute('UPDATE collected_articles SET last_checked=? WHERE url=? AND mode=?',(timestamp,url,mode))
        return {'url':url,'id':old['document_id'],'status':'unchanged','version':old['version']}
    version=old['version']+1 if old else 1
    provenance=extracted.setdefault('provenance',{});provenance.update(subscription_id=source['id'],version=version,feed_url=source['feed_url'])
    result=add_document(**extracted,origin_url=url,owner='public',mode=mode,synthetic=False,revision_key=digest)
    with db() as c:
        if old:
            previous=json.loads(c.execute('SELECT payload FROM sources WHERE id=?',(old['document_id'],)).fetchone()[0])
            previous.update(active=False,superseded_by=result['id'])
            c.execute('UPDATE sources SET active=0,payload=? WHERE id=?',(dumps(previous),old['document_id']))
        record={'subscription_id':source['id'],'content_extent':provenance['content_extent'],'published_at':date,'title':extracted['title'],'previous_document_id':old['document_id'] if old else None}
        c.execute('INSERT INTO collected_articles VALUES(?,?,?,?,?,?,?) ON CONFLICT(url,mode) DO UPDATE SET document_id=excluded.document_id,version=excluded.version,content_sha=excluded.content_sha,last_checked=excluded.last_checked,payload=excluded.payload',
                  (url,mode,result['id'],version,digest,timestamp,dumps(record)))
        c.execute('INSERT INTO article_versions VALUES(?,?,?,?,?)',(url,mode,version,result['id'],timestamp))
    return {'url':url,**result,'status':'updated' if old else 'new','version':version,'content_extent':provenance['content_extent']}

def collect_source(source,mode,fetcher=fetch_public,max_items=5):
    start=time.monotonic();raw,_,final=fetcher(source['feed_url']);entries=parse_feed(raw,final)
    host=(urlsplit(source['homepage']).hostname or '').removeprefix('www.')
    entries=[e for e in entries if (urlsplit(e['url']).hostname or '').removeprefix('www.')==host]
    with db() as c:known={r['url']:r['last_checked'] for r in c.execute('SELECT url,last_checked FROM collected_articles WHERE mode=?',(mode,))}
    # Business stories first; rotate checked pages so corrections and older entries are eventually revisited.
    entries.sort(key=lambda e:(business_priority(e),known.get(article_url(e['url']),'')))
    results=[]
    for entry in entries[:max_items]:
        if time.monotonic()-start>50:break
        try:results.append(store_article(entry,source,mode,fetcher))
        except DomainError as error:results.append({'url':entry['url'],'status':'failed','error':{'code':error.code,'message':error.message}})
    failed=sum(r['status']=='failed' for r in results)
    return {'id':source['id'],'name':source['name'],'found':len(entries),'processed':len(results)-failed,'changed':sum(r['status'] in ('new','updated') for r in results),
            'status':'partial' if failed or any(r.get('content_extent')=='feed_excerpt' for r in results) else 'completed','items':results,'elapsed_ms':round((time.monotonic()-start)*1000)}

def scan_bank(mode):
    """Watch the verified local corpus manifest; never silently trust an unknown PDF."""
    from .corpus import load_case_corpus
    folder=ROOT/'data/reference/bank';manifest=folder/'manifest.json'
    if not manifest.exists():return {'status':'unavailable','message':'Корпус банка не установлен','changed':0,'checked_at':now()}
    digest=hashlib.sha256(manifest.read_bytes()).hexdigest()
    previous=state('bank_scan',{})
    if previous.get('sha256')==digest and previous.get('mode')==mode:
        return {**previous,'checked_at':now(),'changed':0}
    result=load_case_corpus(mode=mode)
    return {'status':'completed','checked_at':now(),'mode':mode,'sha256':digest,'documents':len(result['items']),
            'changed':sum(i['status']!='duplicate' for i in result['items']),
            'message':'Проверяется локальный подтверждённый корпус. Адрес для поиска новых банковских публикаций пока не задан.'}

def execute_cycle(uid,fetcher=fetch_public):
    cycle=get_cycle(uid);p=cycle['payload'];mode=p.get('mode') or setting()['mode'];p['mode']=mode
    with db() as c:c.execute("UPDATE collection_cycles SET status='running' WHERE id=?",(uid,))
    for source in subscriptions():
        if not source['enabled']:continue
        if p['trigger']=='schedule' and source.get('next_check') and source['next_check']>now():continue
        try:
            result=collect_source(source,mode,fetcher)
            source.update(last_checked=now(),last_success=now(),found=result['found'],processed=result['processed'],last_error=None,
                          content_warning='Часть материалов доступна только как RSS-анонс' if any(i.get('content_extent')=='feed_excerpt' for i in result['items']) else None)
        except Exception as error:
            message=getattr(error,'message','Ошибка обработки источника')
            result={'id':source['id'],'name':source['name'],'status':'failed','changed':0,'error':{'code':getattr(error,'code','COLLECTION_ERROR'),'message':message}}
            source.update(last_checked=now(),last_error=message)
        source['next_check']=(datetime.now(timezone.utc)+timedelta(seconds=source['poll_seconds'])).isoformat()
        with db() as c:c.execute('UPDATE subscriptions SET payload=? WHERE id=?',(dumps(source),source['id']))
        p['sources'].append(result);p['changed']+=result['changed']
        with db() as c:c.execute('UPDATE collection_cycles SET payload=? WHERE id=?',(dumps(p),uid))
    try:
        from .bank_watch import scan
        remote=scan(mode,fetcher)
        bank=scan_bank(mode);bank['remote']=remote;put_state('bank_scan',bank);p['changed']+=bank.get('changed',0)+remote['changed']
    except Exception as error:put_state('bank_scan',{'status':'failed','checked_at':now(),'message':getattr(error,'message','Ошибка проверки корпуса банка')})
    status='partial' if any(s['status'] in ('failed','partial') for s in p['sources']) or any(i.get('status')=='failed' for i in state('bank_scan',{}).get('remote',{}).get('items',[])) else 'completed'
    with db() as c:c.execute('UPDATE collection_cycles SET status=?,ended_at=?,payload=? WHERE id=?',(status,now(),dumps(p),uid))
    if p['changed']:put_state('revision',state('revision',0)+1)
    if p.get('analyze') and p.get('company_ids'):
        try:
            from .batches import create_batch
            batch=create_batch(p['company_ids'],p['owner'],p.get('role','ceo'),mode,idem='cycle:'+uid)
            p['batch_id']=batch['batch_id']
        except DomainError as error:p['analysis_error']={'code':error.code,'message':error.message}
        with db() as c:c.execute('UPDATE collection_cycles SET payload=? WHERE id=?',(dumps(p),uid))
    return get_cycle(uid)

def fingerprint(mode,uid):
    from .analysis import get_profile
    with db() as c:rows=[tuple(r) for r in c.execute('SELECT id,sha256,active,published_at FROM sources WHERE mode=? AND published_at IS NOT NULL ORDER BY id',(mode,))]
    # Refresh freshness at least once per three hours, even when no articles changed.
    bucket=int(datetime.now(timezone.utc).timestamp()//10800)
    return hashlib.sha256(dumps([rows,get_profile(uid),mode,bucket]).encode()).hexdigest()

def schedule_analysis():
    return {'status':'disabled','reason':'manual_only'}

def recover():
    with db() as c:
        for row in c.execute("SELECT id,payload FROM collection_cycles WHERE status='running'").fetchall():
            p=json.loads(row['payload']);p['restart_note']='Предыдущий сбор прерван; повторите его по кнопке'
            c.execute("UPDATE collection_cycles SET status='interrupted',ended_at=?,payload=? WHERE id=?",(now(),dumps(p),row['id']))
    lease=state('lease',{})
    if lease:
        try:
            pid=int(lease.get('pid',0))
            if pid<=0:raise ProcessLookupError()
            os.kill(pid,0)
        except (ProcessLookupError,ValueError):put_state('lease',{})
        except PermissionError:pass

class Worker:
    def __init__(self):self.stop_event=threading.Event();self.thread=None;self.token=uuid.uuid4().hex
    def lease(self):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute("SELECT payload FROM collection_state WHERE id='lease'").fetchone();lease=json.loads(row[0]) if row else {}
            if lease.get('until','')>now() and lease.get('owner')!=self.token:return False
            value={'owner':self.token,'pid':os.getpid(),'until':(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()}
            c.execute("INSERT INTO collection_state VALUES('lease',?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",(dumps(value),))
        return True
    def tick(self):
        if not self.lease():return
        put_state('heartbeat',{'at':now(),'status':'running'})
        with db() as c:row=c.execute("SELECT id FROM collection_cycles WHERE status='queued' ORDER BY started_at LIMIT 1").fetchone()
        if row:execute_cycle(row['id'])
    def loop(self):
        while not self.stop_event.is_set():
            try:self.tick()
            except Exception:put_state('heartbeat',{'at':now(),'status':'error','message':'Ошибка фонового цикла; следующая попытка через 5 секунд'})
            self.stop_event.wait(5)
    def start(self):
        self.thread=threading.Thread(target=self.loop,name='alfa-collection',daemon=True);self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=2)
        lease=state('lease',{})
        if lease.get('owner')==self.token:put_state('lease',{})
