import asyncio,hashlib,json,os,secrets,uuid
from contextlib import asynccontextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Literal
from fastapi import FastAPI,Request,Response,Depends,UploadFile,File,Form
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse,FileResponse,PlainTextResponse,HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from dotenv import load_dotenv
from .db import ROOT,db,dumps,unpack,now,migrate,setting,uploads_dir
load_dotenv(ROOT/'.env')
from .errors import DomainError
from .datasets import SELECTED,import_datasets
from .analysis import get_profile,create_run,EXECUTOR,BANK_EMPTY
from . import budget
from .llm import configuration
from .presentation import present_insight
from .ingestion import add_document,import_pdf,import_url

@asynccontextmanager
async def lifespan(app):
    migrate()
    with db() as c:
        c.execute("UPDATE analysis_batches SET status='interrupted',ended_at=? WHERE status IN ('queued','running')",(now(),))
        c.execute("UPDATE runs SET status='failed',ended_at=? WHERE status IN ('running','queued')",(now(),))
        c.execute("UPDATE jobs SET status='failed' WHERE status IN ('running','queued')")
        # Unknown outcome after crash is charged conservatively.
        c.execute("UPDATE ledger SET charged=reserved,reserved='0',state='uncertain' WHERE state='reserved'")
    from .seed import seed
    seed()
    from .collection import initialize,recover,Worker
    initialize();recover()
    worker=Worker()
    if os.getenv('ALFA_COLLECTION_WORKER','1')!='0':worker.start()
    try:yield
    finally:worker.stop()

app=FastAPI(title='Альфа Контекст',version='1.4.1',lifespan=lifespan)
@app.exception_handler(DomainError)
async def domain_error(request,e):return JSONResponse({'error':{'code':e.code,'message':e.message,'details':e.details,'request_id':getattr(request.state,'rid','')}},status_code=e.status)
@app.exception_handler(RequestValidationError)
async def validation_error(request,e):return JSONResponse({'error':{'code':'VALIDATION','message':'Проверьте введённые данные','details':[{'field':str(x['loc'][-1]),'message':x['msg']} for x in e.errors()],'request_id':getattr(request.state,'rid','')}},status_code=422)
@app.exception_handler(Exception)
async def internal_error(request,e):return JSONResponse({'error':{'code':'INTERNAL_ERROR','message':'Сервис временно недоступен','request_id':getattr(request.state,'rid','')}},status_code=500)
@app.middleware('http')
async def guard(request,call_next):
    request.state.rid=uuid.uuid4().hex
    origin=request.headers.get('origin')
    if request.method not in ('GET','HEAD','OPTIONS') and origin and origin not in ('http://127.0.0.1:5173','http://localhost:5173','http://127.0.0.1:8000','http://localhost:8000',os.getenv('ALFA_ORIGIN','')):
        return JSONResponse({'error':{'code':'CSRF','message':'Недопустимый источник запроса'}},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='same-origin';response.headers['X-Request-ID']=request.state.rid
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self' ws://127.0.0.1:5173; frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
    return response

def session(request:Request,response:Response):
    sid=request.cookies.get('alfa_session')
    with db() as c:row=c.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone() if sid else None
    if not row:
        # Demo is a local-only session. Never enable it on a remote bind.
        if os.getenv('ALFA_AUTH_MODE','demo')!='demo' or request.client.host not in ('127.0.0.1','::1','testclient') and os.getenv('ALFA_CONTAINER_LOCAL')!='1':
            raise DomainError('NO_SESSION','Требуется серверная сессия',401)
        sid=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        user={'id':'demo','display_name':'Демо-пользователь','system_role':os.getenv('ALFA_DEMO_ROLE','admin'),'allowed_company_ids':SELECTED,'preferences':{'role':'ceo','company_id':None}}
        with db() as c:c.execute('INSERT INTO sessions VALUES(?,?,?)',(sid,dumps(user),csrf))
        response.set_cookie('alfa_session',sid,httponly=True,samesite='strict',secure=False)
    else:user=json.loads(row['payload']);csrf=row['csrf']
    if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('x-csrf-token')!=csrf:raise DomainError('CSRF','Обновите страницу и повторите действие',403)
    return {**user,'csrf_token':csrf,'session_id':sid}
def auth_company(uid,user):
    if uid not in user['allowed_company_ids']:raise DomainError('FORBIDDEN','Нет доступа к аналитике этой компании',403)
    return get_profile(uid)
def analyst(user):
    if user['system_role'] not in ('analyst','admin'):raise DomainError('FORBIDDEN','Источники добавляет аналитик',403)
def admin(user):
    if user['system_role']!='admin':raise DomainError('FORBIDDEN','Недостаточно прав',403)
def access_run(rid,user):
    with db() as c:row=c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
    if not row:raise DomainError('NOT_FOUND','Запуск не найден',404)
    auth_company(row['company_id'],user)
    return dict(row)
def access_insight(iid,user,uid=None):
    with db() as c:row=c.execute('SELECT * FROM insights WHERE id=?',(iid,)).fetchone()
    if not row:raise DomainError('NOT_FOUND','Событие недоступно',404)
    auth_company(row['company_id'],user)
    if uid and row['company_id']!=uid:raise DomainError('FORBIDDEN','Нет доступа',403)
    p=json.loads(row['payload'])
    with db() as c:p['saved']=bool(c.execute('SELECT 1 FROM saved WHERE user_id=? AND company_id=? AND insight_id=?',(user['id'],p['company_id'],iid)).fetchone())
    return present_insight(p)

def source_access(sid,user):
    with db() as c:row=c.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone()
    if not row:raise DomainError('NOT_FOUND','Источник не найден',404)
    if row['owner'] not in ('public',user['id']) and user['system_role']!='admin':raise DomainError('FORBIDDEN','Нет доступа к файлу',403)
    return {**json.loads(row['payload']),'active':bool(row['active'])}
def page(items,p=1,size=10):return {'items':items[(p-1)*size:p*size],'total':len(items),'page':p,'page_size':size}
def latest(uid,role,run_id=None):
    with db() as c:
        if run_id:row=c.execute('SELECT * FROM runs WHERE id=? AND company_id=?',(run_id,uid)).fetchone()
        else:row=c.execute("SELECT * FROM runs WHERE company_id=? AND role=? AND status IN ('completed','completed_with_limits') ORDER BY created_at DESC LIMIT 1",(uid,role)).fetchone()
        if not row:return None,[]
        items=[present_insight(json.loads(r[0])) for r in c.execute('SELECT payload FROM insights WHERE run_id=?',(row['id'],))]
    items.sort(key=lambda i:i['event_id']);items.sort(key=lambda i:i['published_at'],reverse=True);items.sort(key=lambda i:i['relevance']['score'],reverse=True)
    return {**dict(row),'payload':json.loads(row['payload'])},items

def with_saved(items,user):
    with db() as c:saved={r[0] for r in c.execute('SELECT insight_id FROM saved WHERE user_id=?',(user['id'],))}
    return [{**i,'saved':i['id'] in saved} for i in items]
P='/api/v1'
@app.get(P+'/health')
def health():return {'status':'ok'}
@app.get(P+'/ready')
def ready():
    with db() as c:c.execute('SELECT 1')
    return {'status':'ready','worker':'local-durable-queue'}
@app.get(P+'/me')
def me(user=Depends(session)):return {k:v for k,v in user.items() if k!='session_id'}
class Preferences(BaseModel):
    role:Literal['ceo','cfo','treasury']|None=None
    company_id:str|None=None
@app.patch(P+'/me/preferences')
def preferences(body:Preferences,user=Depends(session)):
    if body.company_id:auth_company(body.company_id,user)
    user['preferences'].update(body.model_dump(exclude_none=True))
    with db() as c:c.execute('UPDATE sessions SET payload=? WHERE id=?',(dumps({k:v for k,v in user.items() if k not in ('csrf_token','session_id')}),user['session_id']))
    return user['preferences']
@app.get(P+'/companies')
def companies(user=Depends(session)):return page([get_profile(i) for i in user['allowed_company_ids']],1,100)
@app.get(P+'/companies/{uid}/profile')
def profile(uid:str,run_id:str|None=None,user=Depends(session)):
    current=auth_company(uid,user)
    if run_id:
        r=access_run(run_id,user)
        if r['company_id']!=uid:raise DomainError('FORBIDDEN','Нет доступа',403)
        snapshot=json.loads(r['payload']).get('profile_snapshot')
        if snapshot:return {**snapshot,'is_historical_snapshot':True}
    return current
class Clarification(BaseModel):
    version:int=0
    comment:str=Field(max_length=1000)
    contract_terms:Literal['fixed','floating','unknown']='unknown'
    routes:list[str]=Field(default=[],max_length=10)
@app.patch(P+'/companies/{uid}/profile')
def clarify(uid:str,body:Clarification,user=Depends(session)):
    auth_company(uid,user);analyst(user)
    with db() as c:
        c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT version FROM profile_updates WHERE company_id=?',(uid,)).fetchone();version=old[0] if old else 0
        if body.version!=version:raise DomainError('PROFILE_VERSION_CONFLICT','Контекст компании изменился. Обновите форму',409)
        c.execute('INSERT INTO profile_updates VALUES(?,?,?,?,?) ON CONFLICT(company_id) DO UPDATE SET version=excluded.version,payload=excluded.payload,updated_by=excluded.updated_by,updated_at=excluded.updated_at',(uid,version+1,dumps(body.model_dump()),user['id'],now()))
    return get_profile(uid)
@app.get(P+'/companies/{uid}/overview')
def overview(uid:str,role:str='ceo',run_id:str|None=None,user=Depends(session)):
    profile=auth_company(uid,user)
    if run_id:access_run(run_id,user)
    run,items=latest(uid,role,run_id)
    if run and run['payload'].get('profile_snapshot'):profile=run['payload']['profile_snapshot']
    valid=[i for i in items if i['relevance']['score']>=30];items=with_saved(valid,user)
    return {'company':profile,'run':run,'kpi':{'events':len(items),'risks':sum(i['impact'] in ('risk','mixed') for i in items),'opportunities':sum(i['impact'] in ('opportunity','mixed') for i in items),'missing':sum(bool(i['missing_data']) for i in items)} if run else None,'items':items[:3]}
@app.get(P+'/companies/{uid}/insights')
def insights(uid:str,role:str='ceo',q:str='',topics:str='',effect:str='',sort:str='relevance',start:str|None=None,end:str|None=None,page_num:int=1,page:int=1,user=Depends(session)):
    auth_company(uid,user);run,items=latest(uid,role);items=[i for i in items if i['relevance']['score']>=30]
    if start and end and start>end:raise DomainError('INVALID_DATE_RANGE','Дата начала не может быть позже даты окончания',422)
    if q:items=[i for i in items if q.casefold() in (i['title']+' '+i['summary']+' '+' '.join(str(s.get('publisher') or '') for s in i.get('source_metadata',[]))+(' Учебный источник' if i['is_synthetic'] else '')).casefold()]
    if topics:items=[i for i in items if i['topic'] in topics.split(',')]
    if effect:items=[i for i in items if i['impact']==effect or i['impact']=='mixed' and effect in ('risk','opportunity')]
    if start:items=[i for i in items if datetime.fromisoformat(i['published_at']).astimezone(timezone(timedelta(hours=3))).date().isoformat()>=start]
    if end:items=[i for i in items if datetime.fromisoformat(i['published_at']).astimezone(timezone(timedelta(hours=3))).date().isoformat()<=end]
    if sort in ('newest','oldest'):items.sort(key=lambda i:i['published_at'],reverse=sort=='newest')
    return globals()['page'](with_saved(items,user),max(1,page),10)
@app.get(P+'/companies/{uid}/insights/{iid}')
def insight(uid:str,iid:str,user=Depends(session)):return access_insight(iid,user,uid)
class RunInput(BaseModel):
    role:Literal['ceo','cfo','treasury']='ceo'
    mode:Literal['fixture','live']='fixture'
    as_of:str|None=None
@app.post(P+'/companies/{uid}/runs',status_code=202)
def run_create(uid:str,body:RunInput,request:Request,user=Depends(session)):
    auth_company(uid,user)
    if body.mode=='live':
        from .batches import create_batch
        result=create_batch(user['allowed_company_ids'],user['id'],body.role,body.mode,body.as_of,request.headers.get('Idempotency-Key'))
        return {'run_id':result['run_ids'][uid],'batch_id':result['batch_id'],'status':result['status']}
    result=create_run(uid,user['id'],body.role,body.mode,body.as_of,request.headers.get('Idempotency-Key'))
    return {'run_id':result['id'],'status':result['status']}
@app.get(P+'/runs/{rid}')
def run_get(rid:str,user=Depends(session)):
    r=access_run(rid,user);r['payload']=json.loads(r['payload'])
    if user['system_role']=='viewer':r['payload']={k:v for k,v in r['payload'].items() if k not in ('steps','reservation')}
    return r
@app.post(P+'/runs/{rid}/cancel')
def cancel(rid:str,user=Depends(session)):
    r=access_run(rid,user)
    bid=json.loads(r['payload']).get('batch_id')
    if bid:
        from .batches import cancel_batch
        cancel_batch(bid,user['id']);return {'status':'cancelled'}
    with db() as c:c.execute("UPDATE runs SET status='cancelled',ended_at=? WHERE id=? AND status IN ('queued','running')",(now(),rid))
    return {'status':'cancelled'}
@app.get(P+'/companies/{uid}/runs')
def history(uid:str,page_num:int=1,user=Depends(session)):
    auth_company(uid,user)
    with db() as c:rows=[{**dict(r),'payload':json.loads(r['payload'])} for r in c.execute('SELECT * FROM runs WHERE company_id=? ORDER BY created_at DESC',(uid,))]
    return page(rows,page_num,20)
@app.get(P+'/events/{eid}/comparison')
def comparison(eid:str,user=Depends(session)):
    with db() as c:event=unpack(c.execute('SELECT payload FROM events WHERE id=?',(eid,)).fetchone())
    if not event:raise DomainError('NOT_FOUND','Событие недоступно',404)
    result=[]
    for uid in user['allowed_company_ids']:
        run,items=latest(uid,user['preferences']['role']);i=next((i for i in items if i['event_id']==eid),None)
        result.append({'company':get_profile(uid),'insight':i})
    return {'event':event,'items':result}
@app.get(P+'/companies/{uid}/graph')
def graph(uid:str,role:str='ceo',user=Depends(session)):
    p=auth_company(uid,user);_,items=latest(uid,role);items=[i for i in items if i['relevance']['score']>=30]
    nodes=[{'id':uid,'type':'company','label':uid,'detail':p['industry']},{'id':'countries','type':'feature','label':'Страны ФТС','detail':', '.join(p['countries']),'evidence_ids':[uid+':countries']},{'id':'financing','type':'feature','label':'Финансирование','detail':'Кредитный признак: '+('отмечен' if p['products']['credit'] else 'не отмечен'),'evidence_ids':[uid+':credit',uid+':leasing']},{'id':'industry','type':'feature','label':'Отрасль','detail':p['industry'],'evidence_ids':[uid+':industry']}]
    edges=[]
    for i in items:
        feature={'rate':'financing','china':'countries','copper':'industry'}.get(i['topic'],'industry')
        nodes.append({'id':i['event_id'],'type':'event','label':i['title'],'insight_id':i['id'],'detail':i['summary']})
        edges.extend([{'from':i['event_id'],'to':feature,'type':'potential_influence','label':'Возможное влияние','evidence_ids':i['facts'][0]['evidence_ids']},{'from':feature,'to':uid,'type':'context','label':'Признак профиля','evidence_ids':[uid+':'+('leasing' if feature=='financing' else feature)]}])
    return {'company_id':uid,'nodes':nodes,'edges':edges}
@app.put(P+'/companies/{uid}/saved/{iid}')
def save(uid:str,iid:str,user=Depends(session)):
    access_insight(iid,user,uid)
    with db() as c:c.execute('INSERT OR IGNORE INTO saved VALUES(?,?,?,?)',(user['id'],uid,iid,now()))
    return {'saved':True}
@app.delete(P+'/companies/{uid}/saved/{iid}')
def unsave(uid:str,iid:str,user=Depends(session)):
    access_insight(iid,user,uid)
    with db() as c:c.execute('DELETE FROM saved WHERE user_id=? AND company_id=? AND insight_id=?',(user['id'],uid,iid))
    return {'saved':False}
@app.get(P+'/companies/{uid}/saved')
def saved(uid:str,q:str='',sort:str='newest',page_num:int=1,user=Depends(session)):
    auth_company(uid,user)
    with db() as c:items=[{**present_insight(json.loads(r['payload'])),'saved':True,'saved_at':r['created_at']} for r in c.execute('SELECT i.payload,s.created_at FROM saved s JOIN insights i ON i.id=s.insight_id WHERE s.user_id=? AND s.company_id=? ORDER BY s.created_at DESC',(user['id'],uid))]
    items=[i for i in items if q.casefold() in (i['title']+' '+i['summary']).casefold()]
    if sort=='oldest':items.reverse()
    return page(items,page_num,10)
class Feedback(BaseModel):
    useful:bool
    reason:str=Field(default='',max_length=100)
    comment:str=Field(default='',max_length=1000)
@app.put(P+'/insights/{iid}/feedback')
def feedback(iid:str,body:Feedback,user=Depends(session)):
    access_insight(iid,user)
    with db() as c:c.execute('INSERT INTO feedback VALUES(?,?,?,?) ON CONFLICT(user_id,insight_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at',(user['id'],iid,dumps(body.model_dump()),now()))
    return {'saved':True}
@app.get(P+'/insights/{iid}/export')
def export(iid:str,format:str='md',user=Depends(session)):
    i=access_insight(iid,user)
    bank_text='\n'.join('Цитата: '+claim['text']+' ['+', '.join(claim['evidence_ids'])+']' for claim in i['bank_view']['claims']) or BANK_EMPTY
    consequences='\n'.join(('Возможность: ' if h['kind']=='opportunity' else 'Риск: ')+h['text']+'\nУсловия: '+'; '.join(h['conditions']) for h in i['hypotheses'])
    if not any(h['kind']=='opportunity' for h in i['hypotheses']):consequences+='\nНедостаточно оснований для выделения возможностей'
    sections=[i['title'],f"Компания: {i['company_id']} · Роль: {i['role']} · Анализ на {i['as_of']} · Создано: {i['created_at']}",'Учебное событие · Демонстрационные данные' if i['is_synthetic'] else 'Реальные источники','Что произошло\n'+'\n'.join(f['text'] for f in i['facts']),'Почему это важно\n'+'\n'.join(f['text'] for f in i['company_links']),'Возможные последствия\n'+consequences,'Что проверить и сделать\n'+'\n'.join(a['text'] for a in i['actions']),'Позиция Альфа-Банка\n'+bank_text,'Ограничения анализа\n'+'\n'.join(i['missing_data']),'Источники\n'+'\n'.join(i['source_document_ids']+i['bank_view']['document_ids'])]
    sections.append('Оригиналы новостей\n'+'\n'.join((s.get('publisher') or 'Источник')+' — '+s['origin_url'] for s in i.get('source_metadata',[]) if s.get('origin_url')))
    if format=='html':
        import html
        return HTMLResponse('<!DOCTYPE html><html lang="ru"><meta charset="utf-8"><title>Печать инсайта</title><style>body{font:16px/1.6 system-ui;max-width:900px;margin:40px auto;padding:20px}section{white-space:pre-wrap;margin:24px 0}@media print{button{display:none}}</style>'+''.join('<section>'+html.escape(s)+'</section>' for s in sections)+'</html>')
    return PlainTextResponse('\n\n'.join('# '+s if n==0 else s for n,s in enumerate(sections)),headers={'Content-Disposition':f'attachment; filename="{iid}.md"'})
@app.get(P+'/sources')
def sources(tab:str='news',q:str='',status:str='',page_num:int=1,user=Depends(session)):
    with db() as c:
        if tab=='datasets':return page([json.loads(r[0]) for r in c.execute('SELECT payload FROM dataset_imports')],page_num,10)
        rows=[{**json.loads(r['payload']),'active':bool(r['active'])} for r in c.execute('SELECT * FROM sources WHERE kind=? AND (owner=? OR owner=?) ORDER BY published_at DESC',('bank' if tab=='bank' else 'news',user['id'],'public'))]
    items=[r for r in rows if q.casefold() in r['title'].casefold() and (not status or r['text_status']==status)]
    return page(items,page_num,10)
@app.get(P+'/sources/{sid}')
def source(sid:str,user=Depends(session)):return source_access(sid,user)
@app.get(P+'/sources/{sid}/file')
def source_file(sid:str,user=Depends(session)):
    p=source_access(sid,user)
    if not p.get('file_id'):raise DomainError('NO_FILE','У источника нет PDF-файла',404)
    return FileResponse(uploads_dir()/(sid+'.pdf'),filename=sid+'.pdf',media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="{sid}.pdf"','Content-Security-Policy':"sandbox; default-src 'none'"})
class SourcePatch(BaseModel):
    active:bool|None=None
    bank_verified:bool|None=None
@app.patch(P+'/sources/{sid}')
def source_patch(sid:str,body:SourcePatch,user=Depends(session)):
    analyst(user);p=source_access(sid,user)
    if body.active is not None:p['active']=body.active
    if body.bank_verified:
        if p['is_synthetic'] or not p.get('origin_url') or 'alfabank.ru'!=__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(p['origin_url']).hostname:raise DomainError('UNVERIFIED_BANK','Необходимо подтверждённое происхождение банка',422)
        p['bank_verified']=True;p['verified_by']=user['id'];p['verified_at']=now()
    with db() as c:c.execute('UPDATE sources SET active=?,payload=? WHERE id=?',(int(p['active']),dumps(p),sid))
    return p

def submit_job(user,kind,fn):
    jid='job-'+uuid.uuid4().hex[:16]
    with db() as c:c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',(jid,user['id'],'queued',kind,dumps({}),now()))
    def work():
        with db() as c:c.execute("UPDATE jobs SET status='running' WHERE id=?",(jid,))
        try:
            result=fn()
            with db() as c:c.execute("UPDATE jobs SET status='ready',payload=? WHERE id=?",(dumps(result),jid))
        except Exception as e:
            with db() as c:c.execute("UPDATE jobs SET status='failed',payload=? WHERE id=?",(dumps({'error':{'code':getattr(e,'code','IMPORT_ERROR'),'message':getattr(e,'message','Не удалось обработать материал')}}),jid))
    EXECUTOR.submit(work)
    return {'job_id':jid,'status':'queued'}
class TextImport(BaseModel):
    title:str=Field(min_length=1,max_length=300)
    text:str=Field(min_length=200,max_length=100000)
    published_at:str|None=None
    kind:Literal['news','bank']='news'
    mode:Literal['fixture','live']='fixture'
    origin_url:str|None=None
@app.post(P+'/imports/text',status_code=202)
def text_import(body:TextImport,user=Depends(session)):
    analyst(user)
    if body.origin_url:
        from .security import public_target
        public_target(body.origin_url)
    return submit_job(user,'text',lambda:add_document(**body.model_dump(),owner=user['id'],synthetic=body.mode=='fixture'))
class URLImport(BaseModel):
    url:str=Field(max_length=2000)
    kind:Literal['news','bank']='news'
    mode:Literal['fixture','live']='fixture'
    published_at:str|None=None
@app.post(P+'/imports/url',status_code=202)
def url_import(body:URLImport,user=Depends(session)):
    analyst(user)
    from .security import public_target
    public_target(body.url)
    return submit_job(user,'url',lambda:import_url(**body.model_dump(),owner=user['id'],synthetic=False))
@app.post(P+'/imports/files',status_code=202)
async def file_import(files:list[UploadFile]=File(...),kind:str=Form('news'),published_at:str|None=Form(None),user=Depends(session)):
    analyst(user)
    if len(files)>10 or kind not in ('news','bank'):raise DomainError('UPLOAD_LIMIT','Допускается до 10 PDF за импорт')
    values=[]
    for f in files:
        raw=await f.read(20*1024*1024+1)
        if len(raw)>20*1024*1024:raise DomainError('FILE_TOO_LARGE','Загрузите PDF до 20 МБ',413)
        if not raw.startswith(b'%PDF-'):raise DomainError('UNSUPPORTED_FILE','Формат файла не поддерживается. Загрузите PDF до 20 МБ')
        values.append((Path(f.filename).name,raw))
    return submit_job(user,'pdf',lambda:{'items':[import_pdf(raw,name,kind=kind,published_at=published_at,owner=user['id']) for name,raw in values]})
class FeedImport(BaseModel):
    url:str=Field(min_length=8,max_length=2000)
    mode:Literal['fixture','live']='fixture'
    max_items:int=Field(default=5,ge=1,le=10)
@app.post(P+'/imports/feed',status_code=202)
def feed_import(body:FeedImport,user=Depends(session)):
    analyst(user)
    from .security import public_target
    from .feeds import import_feed
    public_target(body.url)
    return submit_job(user,'feed',lambda:import_feed(**body.model_dump(),owner=user['id']))
@app.post(P+'/imports/datasets',status_code=202)
async def dataset_import(files:list[UploadFile]=File(...),preview:bool=Form(False),user=Depends(session)):
    analyst(user);values=[]
    for f in files:
        raw=await f.read(50*1024*1024+1)
        if len(raw)>50*1024*1024:raise DomainError('FILE_TOO_LARGE','Файл превышает 50 МБ',413)
        values.append((Path(f.filename).name,raw))
    if preview:return import_datasets(values,preview=True)
    return submit_job(user,'datasets',lambda:import_datasets(values))
@app.get(P+'/imports/{jid}')
def import_get(jid:str,user=Depends(session)):
    with db() as c:row=c.execute('SELECT * FROM jobs WHERE id=? AND owner=?',(jid,user['id'])).fetchone()
    if not row:raise DomainError('NOT_FOUND','Задание не найдено',404)
    return {**dict(row),'payload':json.loads(row['payload'])}
@app.get(P+'/imports/{jid}/report')
def import_report(jid:str,user=Depends(session)):
    report=import_get(jid,user)
    return PlainTextResponse(json.dumps(report,ensure_ascii=False,indent=2),headers={'Content-Disposition':f'attachment; filename="{jid}.json"'})
@app.post(P+'/sources/{sid}/reprocess',status_code=202)
def reprocess(sid:str,user=Depends(session)):
    analyst(user);p=source_access(sid,user)
    if not p.get('file_id'):raise DomainError('NO_FILE','Для повторной обработки добавьте исходный файл',409)
    def process():
        # Existing source stays immutable in history. OCR support is checked locally first.
        from .ocr import engine
        if p['text_status']=='needs_ocr' and not engine():raise DomainError('OCR_UNAVAILABLE','Документу требуется OCR. Локальный OCR не установлен',409)
        raw=(uploads_dir()/(sid+'.pdf')).read_bytes()
        return import_pdf(raw,p['filename'],ocr=True,kind=p['kind'],published_at=p['published_at'],owner=user['id'])
    return submit_job(user,'reprocess',process)
@app.get(P+'/settings')
def settings_get(user=Depends(session)):
    config=configuration()
    return {**setting(),'budget':budget.snapshot(),'system_role':user['system_role'],'live':{k:str(v) if k.endswith('price') else v for k,v in config.items()}}
class Settings(BaseModel):
    daily_budget:int=Field(ge=1,le=500)
    run_budget:int=Field(ge=1,le=500)
    mode:Literal['fixture','live']='fixture'
    auto_refresh:bool=False
    allow_external_context:bool=False
@app.patch(P+'/settings')
def settings_patch(body:Settings,user=Depends(session)):
    admin(user)
    if body.run_budget>body.daily_budget:raise DomainError('INVALID_BUDGET','Лимит одного запуска не должен превышать дневной',422)
    if body.mode=='live' and not configuration()['ready']:raise DomainError('LIVE_NOT_CONFIGURED','Реальный анализ не настроен',409)
    if body.auto_refresh:raise DomainError('MANUAL_ONLY','Обновление выполняется только по кнопке, чтобы экономить лимит запросов',409)
    with db() as c:c.execute("INSERT INTO settings VALUES('global',?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",(dumps(body.model_dump()),))
    return settings_get(user)
@app.post(P+'/settings/test-connection')
def test_connection(user=Depends(session)):
    admin(user)
    from .llm import check_connection
    return check_connection()

# Collection routes are registered before the production SPA fallback.
@app.get(P+'/collection')
def collection_status(user=Depends(session)):
    from .collection import snapshot
    result=snapshot(user['id'])
    result['targets']=[t for t in result['targets'] if t['owner']==user['id'] and t['company_id'] in user['allowed_company_ids']]
    return result

@app.post(P+'/collection/refresh',status_code=202)
def collection_refresh(user=Depends(session)):
    analyst(user)
    from .collection import enqueue
    return enqueue('manual',user['id'],user['allowed_company_ids'],user['preferences']['role'],analyze=True)

@app.get(P+'/collection/cycles/{cid}')
def collection_cycle(cid:str,user=Depends(session)):
    from .collection import get_cycle
    return get_cycle(cid)

class SubscriptionInput(BaseModel):
    enabled:bool
    poll_seconds:int=Field(default=300,ge=180,le=3600)
@app.patch(P+'/collection/sources/{sid}')
def subscription_update(sid:str,body:SubscriptionInput,user=Depends(session)):
    admin(user)
    from .collection import patch_subscription
    return patch_subscription(sid,**body.model_dump())

class WatchInput(BaseModel):
    role:Literal['ceo','cfo','treasury']='ceo'
@app.post(P+'/companies/{uid}/watch')
def watch(uid:str,body:WatchInput,user=Depends(session)):
    auth_company(uid,user)
    from .collection import track_target
    track_target(uid,body.role,user['id'])
    return {'status':'tracked','company_id':uid,'role':body.role}

@app.get(P+'/collection/articles')
def collection_articles(user=Depends(session)):
    with db() as c:
        items=[{**dict(r),'payload':json.loads(r['payload'])} for r in c.execute('SELECT * FROM collected_articles WHERE mode=? ORDER BY last_checked DESC LIMIT 100',(setting()['mode'],))]
    return {'items':items}

class BankWatchInput(BaseModel):
    url:str=Field(min_length=8,max_length=2000)
    title:str=Field(min_length=1,max_length=300)
    published_at:str|None=None
@app.post(P+'/collection/bank-watches',status_code=201)
def bank_watch_create(body:BankWatchInput,user=Depends(session)):
    admin(user)
    from .bank_watch import add_watch
    return add_watch(**body.model_dump())
@app.delete(P+'/collection/bank-watches/{wid}')
def bank_watch_delete(wid:str,user=Depends(session)):
    admin(user)
    from .bank_watch import remove_watch
    remove_watch(wid)
    return {'status':'removed'}

@app.get(P+'/sources/{sid}/versions')
def source_versions(sid:str,user=Depends(session)):
    source_access(sid,user)
    with db() as c:
        row=c.execute('SELECT url,mode FROM article_versions WHERE document_id=?',(sid,)).fetchone()
        items=[dict(r) for r in c.execute('SELECT version,document_id,created_at FROM article_versions WHERE url=? AND mode=? ORDER BY version DESC',(row['url'],row['mode']))] if row else []
    return {'items':items}

class BatchInput(BaseModel):
    role:Literal['ceo','cfo','treasury']='ceo'
    mode:Literal['fixture','live']|None=None
    as_of:str|None=None
@app.post(P+'/analysis/batches',status_code=202)
def batch_create(body:BatchInput,request:Request,user=Depends(session)):
    from .batches import create_batch
    return create_batch(user['allowed_company_ids'],user['id'],body.role,body.mode,body.as_of,request.headers.get('Idempotency-Key'))
@app.get(P+'/analysis/batches/{bid}')
def batch_get(bid:str,user=Depends(session)):
    from .batches import get_batch
    batch=get_batch(bid,user['id'])
    for uid in batch['payload']['company_ids']:auth_company(uid,user)
    return batch
@app.post(P+'/analysis/batches/{bid}/cancel')
def batch_cancel(bid:str,user=Depends(session)):
    from .batches import cancel_batch
    cancel_batch(bid,user['id']);return {'status':'cancelled'}

# Single-origin production and deep-link fallback.
if (ROOT/'dist').exists():
    app.mount('/assets',StaticFiles(directory=ROOT/'dist/assets'),name='assets')
    @app.get('/{path:path}',include_in_schema=False)
    def spa(path:str):
        if path.startswith('api/'):raise DomainError('NOT_FOUND','Ресурс не найден',404)
        target=ROOT/'dist'/path
        if target.is_file() and target.resolve().is_relative_to((ROOT/'dist').resolve()):return FileResponse(target)
        return FileResponse(ROOT/'dist/index.html')
