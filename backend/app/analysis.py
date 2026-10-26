import hashlib,json,time,uuid
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
from .db import ROOT,db,dumps,unpack,now,setting
from .errors import DomainError
from .ingestion import search_bank
from .llm import LiveProvider,Interpretation
from . import budget
from .relevance import rank,VERSION as RANKING_VERSION
from .content import event_content
from .stories import select_events
from .interpretation import interpret as interpret_unseen

RULES={'version':RANKING_VERSION}
TEXTS=json.loads((ROOT/'data/fixtures/texts.json').read_text()) if (ROOT/'data/fixtures/texts.json').exists() else {}
CANONICAL_EVENT_IDS={e['id'] for e in json.loads((ROOT/'data/fixtures/events.json').read_text())}
CANONICAL_PROFILES={p['id']:p for p in json.loads((ROOT/'data/fixtures/profiles.json').read_text())}
STEPS=['План анализа','Профиль компании','Поиск событий','Материалы банка','Ранжирование','Интерпретация','Проверка оснований']
EXECUTOR=ThreadPoolExecutor(max_workers=1,thread_name_prefix='alfa-worker')
AS_OF='2026-10-23T12:00:00+00:00'
BANK_EMPTY='В загруженных материалах не найдена подтверждённая позиция банка по этому событию'

def get_profile(uid):
    with db() as c:
        p=unpack(c.execute('SELECT payload FROM companies WHERE id=?',(uid,)).fetchone())
        extra=c.execute('SELECT * FROM profile_updates WHERE company_id=?',(uid,)).fetchone()
    if not p:raise DomainError('COMPANY_NOT_FOUND','Компания не найдена',404)
    if extra:
        p['clarification']=json.loads(extra['payload']);p['clarification_version']=extra['version']
        if p['clarification'].get('routes'):p['unknowns']=[x for x in p['unknowns'] if x!='Маршруты']
    return p

def fixture_interpret(profile,event,role='ceo',bank_chunks=()):
    baseline=CANONICAL_PROFILES.get(profile['id'],{})
    unchanged=all(profile.get(key)==baseline.get(key) for key in ('version','industry','okved','products','countries')) and not profile.get('clarification')
    if not unchanged or event.get('id') not in CANONICAL_EVENT_IDS or any(c.get('bank_verified') and not c.get('is_synthetic') for c in bank_chunks):
        return interpret_unseen(profile,event,role,bank_chunks)
    uid=profile['id'];topic=event['topic'];provided=TEXTS.get(topic+':'+uid)
    item=provided or {'why':'Прямая связь события с профилем компании не подтверждена.','risk':'Недостаточно данных, чтобы подтвердить влияние на компанию.','action':'Уточните источник и механизм связи с деятельностью компании.'}
    # Imported fixture documents use their own factual text; canonical wording applies to the six named fixtures only.
    source_ids=event.get('source_document_ids',[])
    with db() as c:
        chunks=[dict(r) for d in source_ids for r in c.execute('SELECT * FROM chunks WHERE document_id=?',(d,))]
    if not chunks:raise DomainError('NO_EVIDENCE','У события нет доступных источников',422)
    fid=uid+':'+('credit' if topic=='rate' and profile['products'].get('credit') else 'leasing' if topic=='rate' else 'countries' if topic=='china' else 'industry')
    return {'facts':[{'claim_id':'f1','text':event['fact'],'evidence_ids':[chunks[0]['id']]}], 'company_links':[{'text':item['why'],'field_evidence_ids':[fid]}], 'bank_view':{'status':'not_found','claims':[],'document_ids':[]},'hypotheses':[{'claim_id':'h1','text':item['risk'],'kind':'risk','premise_claim_ids':['f1'],'field_evidence_ids':[fid],'conditions':['Требуется подтверждение условий и зависимости компании'],'horizon':None}], 'actions':[{'text':item['action'],'rationale_claim_ids':['h1'],'requires_data':profile['unknowns']}], 'missing_data':profile['unknowns']+(['Официальный документ со сроком вступления ограничения'] if topic=='conflict' else []),'assumptions':[]}

def validate_claims(payload,profile,event,as_of,mode,owner='demo',allowed_bank_ids=None):
    data=Interpretation.model_validate(payload).model_dump(); known=set(x['id'] for x in profile['field_evidence'].values())
    with db() as c:
        docs=[unpack(c.execute('SELECT payload FROM sources WHERE id=? AND active=1 AND mode=? AND owner IN (?,?)',(sid,mode,'public',owner)).fetchone()) for sid in event.get('source_document_ids',[])]
    docs=[d for d in docs if d and d.get('published_at') and datetime.fromisoformat(d['published_at'])<=datetime.fromisoformat(as_of)]
    chunks={ch['id']:(ch,d) for d in docs for ch in d['chunks']}
    if not data['company_links']:raise DomainError('NO_COMPANY_LINK','Не объяснена связь с компанией',422)
    if not data['facts']:raise DomainError('NO_FACTS','Нет факта, подтверждённого источником',422)
    identifiers=[item['claim_id'] for key in ('facts','hypotheses') for item in data[key]]+[item['claim_id'] for item in data['bank_view']['claims']]
    if len(identifiers)!=len(set(identifiers)):raise DomainError('DUPLICATE_CLAIM','Идентификаторы утверждений должны быть уникальны',422)
    for fact in data['facts']:
        if any(eid not in chunks for eid in fact['evidence_ids']):raise DomainError('INVALID_EVIDENCE','Основание утверждения недоступно',422)
        # Facts are extractive in MVP. A URL or token overlap is not sufficient evidence of support.
        support=' '.join(chunks[eid][0]['text'] for eid in fact['evidence_ids'])
        if ' '.join(fact['text'].split()) not in ' '.join(support.split()):raise DomainError('UNSUPPORTED_CLAIM','Фактическое утверждение не подтверждено фрагментом',422)
    fact_ids={f['claim_id'] for f in data['facts']};hyp_ids={h['claim_id'] for h in data['hypotheses']}
    for link in data['company_links']:
        if not link.get('field_evidence_ids') or any(e not in known for e in link['field_evidence_ids']):raise DomainError('FOREIGN_EVIDENCE','Признак компании не подтверждён',422)
    bank_ids={claim['claim_id'] for claim in data['bank_view']['claims']}
    for h in data['hypotheses']:
        if not h['conditions'] or not h['premise_claim_ids'] or not h['field_evidence_ids'] or any(e not in known for e in h['field_evidence_ids']) or not set(h['premise_claim_ids'])<=fact_ids|bank_ids:raise DomainError('FOREIGN_EVIDENCE','Гипотеза содержит недоступные основания',422)
    for action in data['actions']:
        if not set(action['rationale_claim_ids'])<=hyp_ids:raise DomainError('INVALID_RATIONALE','Действие не связано с гипотезой',422)
    if data['bank_view']['claims']:
        bank_documents=set()
        if data['bank_view']['status']!='supported':raise DomainError('UNVERIFIED_BANK','Статус позиции банка не соответствует основаниям',422)
        for claim in data['bank_view']['claims']:
            for eid in claim['evidence_ids']:
                if allowed_bank_ids is not None and eid not in allowed_bank_ids:raise DomainError('UNVERIFIED_BANK','Фрагмент банка не передавался в контекст',422)
                with db() as c:row=c.execute('SELECT s.payload FROM chunks ch JOIN sources s ON s.id=ch.document_id WHERE ch.id=? AND s.kind=? AND s.active=1 AND s.mode=? AND s.owner IN (?,?)',(eid,'bank',mode,'public',owner)).fetchone()
                doc=unpack(row)
                if not doc or not doc['bank_verified'] or doc['is_synthetic'] or not doc.get('published_at') or datetime.fromisoformat(doc['published_at'])>datetime.fromisoformat(as_of):raise DomainError('UNVERIFIED_BANK','Позиция банка не подтверждена',422)
                chunk=next(ch for ch in doc['chunks'] if ch['id']==eid)
                if ' '.join(claim['text'].split()) not in ' '.join(chunk['text'].split()):raise DomainError('UNVERIFIED_BANK','Цитата банка не подтверждена',422)
                bank_documents.add(doc['id'])
        if bank_documents!=set(data['bank_view']['document_ids']):raise DomainError('UNVERIFIED_BANK','Список материалов банка не соответствует цитатам',422)
    elif data['bank_view']['status']!='not_found' or data['bank_view']['document_ids']:raise DomainError('UNVERIFIED_BANK','Пустая позиция банка',422)
    if (event['topic']=='conflict' or event.get('conflicts') or event_content(event)['conflict']) and not any('противореч' in s.lower() or 'официальн' in s.lower() for s in data['missing_data']):raise DomainError('CONFLICT_HIDDEN','Противоречие источников не отражено',422)
    return data

def log_step(rid,step,result,tool,elapsed):
    with db() as c:
        p=unpack(c.execute('SELECT payload FROM runs WHERE id=?',(rid,)).fetchone())
        p['steps'].append({'number':step,'name':STEPS[step-1],'status':'completed','result':result,'tool':tool,'elapsed_ms':round(elapsed*1000)})
        p['step']=step;c.execute('UPDATE runs SET payload=? WHERE id=?',(dumps(p),rid))

def cancelled(rid):
    with db() as c:return c.execute('SELECT status FROM runs WHERE id=?',(rid,)).fetchone()['status']=='cancelled'

def bank_query(event,expanded=False):
    base={'rate':'ставк финансирован','copper':'медь медн','china':'Китай экспорт ограничен','gold':'золот','fx':'валют курс рубл','energy':'нефт электроэнерг тариф','inflation':'инфляц цен','labor':'труд зарплат','demand':'потреблен спрос','steel':'сталь металл','logistics':'логист поставк','construction':'строитель инвестиц','electrical':'электрооборуд кабел','regulation':'пошлин налог регулирован'}
    query=base.get(event['topic'],event['title'])
    return query+(' ключев процентн кредит денежн' if event['topic']=='rate' else ' '+' '.join(event_content(event)['countries']+event_content(event)['sectors'])) if expanded else query

def interpretation_context(profile,event,bank_chunks,as_of,role):
    with db() as c:
        chunks=[dict(r) for sid in event['source_document_ids'] for r in c.execute('SELECT id,document_id,text,page_number FROM chunks WHERE document_id=?',(sid,))]
    company={k:profile.get(k) for k in ('id','industry','okved','products','countries','unknowns')}
    company['products']={k:v for k,v in company['products'].items() if k in ('credit','leasing')}
    company['field_evidence']={k:{key:v[key] for key in ('id','raw_value') if key in v} for k,v in profile.get('field_evidence',{}).items() if k in ('industry','okved','credit','leasing','countries')}
    return {'company':company,'role':role,'event':event,'event_chunks':[{**ch,'text':ch['text'][:900]} for ch in chunks[:1]],'bank_chunks':[{**ch,'text':ch['text'][:700]} for ch in bank_chunks[:2]],'as_of':as_of,'constraints':['Факты и цитаты — точные выдержки из переданных фрагментов','Страны ФТС не доказывают импорт или товар','Гипотезы условные; при нехватке оснований воздержись от вывода']}

def execute_run(rid):
    start=time.monotonic(); provider=None;reservation=None
    with db() as c:
        row=dict(c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone());p=json.loads(row['payload']);reservation=p.get('reservation')
        if row['status']=='cancelled':return
        if row['mode']=='live':
            p['error']={'code':'BATCH_REQUIRED','message':'Реальная модель вызывается только пакетным обработчиком'}
            c.execute("UPDATE runs SET status='failed',payload=?,ended_at=? WHERE id=?",(dumps(p),now(),rid));return
        c.execute("UPDATE runs SET status='running' WHERE id=?",(rid,))
    try:
        uid,role,mode,as_of=row['company_id'],row['role'],row['mode'],row['as_of'];profile=p.get('profile_snapshot') or get_profile(uid)
        if mode=='live':provider=LiveProvider(setting()['run_budget'])
        engines={};model_errors=[];model_cache_hits=0;provider_blocked=False
        external_allowed=setting().get('allow_external_context',False)
        if provider and not external_allowed:
            provider_blocked=True
            model_errors.append({'code':'EXTERNAL_CONTEXT_CONSENT','message':'Передача профиля компании внешней модели не разрешена. Использованы локальные правила.'})
        def context_key(event,chunks):
            ctx=interpretation_context(profile,event,chunks,as_of,role);ctx.pop('as_of',None)
            return hashlib.sha256(dumps([row['user_id'],provider.cfg['model_id'] if provider else 'rules',ctx,'prompt-v3']).encode()).hexdigest()
        def generate(event,chunks,repair=None):
            nonlocal model_cache_hits,provider_blocked
            eid=event['id']
            if provider and not provider_blocked:
                key=context_key(event,chunks)
                if not repair:
                    with db() as c:cached=c.execute('SELECT payload FROM interpretation_cache WHERE cache_key=?',(key,)).fetchone()
                    if cached:
                        data=json.loads(cached[0])
                        try:
                            data=validate_claims(data,profile,event,as_of,mode,row['user_id'],{ch['id'] for ch in chunks})
                            engines[eid]='cache';model_cache_hits+=1;return data
                        except DomainError:pass
                context=interpretation_context(profile,event,chunks,as_of,role)
                if repair:context['repair_error_code']=repair
                try:
                    data=provider.interpret(context);engines[eid]='model';return data
                except DomainError as error:
                    if not provider.cfg['free_only']:raise
                    model_errors.append({'event_id':eid,'code':error.code,'message':error.message})
                    provider_blocked=True
            engines[eid]='rules'
            return fixture_interpret(profile,event,role,chunks)

        for step in range(1,8):
            if cancelled(rid):return
            if time.monotonic()-start>(300 if mode=='live' else 120):raise DomainError('RUN_TIMEOUT','Анализ превысил отведённое время',504)
            tick=time.monotonic()
            if step==1:result={'required':['Профиль','Событие','Основания'],'optional':['Аналитика банка'],'role':role,'as_of':as_of};tool='plan_analysis'
            elif step==2:result={'profile_version':profile['version'],'known_fields':list(profile['field_evidence']),'unknowns':profile['unknowns']};tool='get_company_profile'
            elif step==3:
                with db() as c:events=select_events(c,mode,row['user_id'],as_of)
                if p.get('automatic'):
                    events=[e for e in events if not e['is_synthetic'] and (datetime.fromisoformat(as_of)-datetime.fromisoformat(e['published_at'])).days<=7]
                result={'events':len(events),'event_ids':[e['id'] for e in events]};tool='search_news'
            elif step==4:
                bank_by_event={e['id']:search_bank(bank_query(e),as_of,mode,row['user_id']) for e in events}
                result={'matches':sum(len(x) for x in bank_by_event.values()),'bank_verified':any(c.get('bank_verified') and not c.get('is_synthetic') for chunks in bank_by_event.values() for c in chunks),'note':'Учебные PDF не являются позицией банка','queries':{e['id']:bank_query(e) for e in events}};tool='search_bank_documents'
            elif step==5:
                ranked=[(e,rank(profile,e,role,as_of)) for e in events];ranked=[(e,r) for e,r in ranked if r]
                ranked.sort(key=lambda x:x[0]['id']);ranked.sort(key=lambda x:x[0]['published_at'],reverse=True);ranked.sort(key=lambda x:x[1]['score'],reverse=True)
                # Six baseline examples are kept for explicit comparison; low scores are excluded from normal feed.
                candidates=([(e,r) for e,r in ranked if r['score']>=30][:3] if mode=='live' else [(e,r) for e,r in ranked if r['score']>=30][:10] if p.get('automatic') else ranked[:10]);result={'shortlist':[{'event_id':e['id'],**r} for e,r in candidates]};tool='rank_events'
            elif step==6:
                generated=[]
                for e,r in candidates:
                    if cancelled(rid):return
                    data=generate(e,bank_by_event[e['id']])
                    generated.append((e,r,data))
                result={'generated':len(generated),'provider':'approved_api' if provider else 'offline_rules','canonical_fixtures':[e['id'] for e,_,data in generated if not provider and e['id'] in CANONICAL_EVENT_IDS and not data.get('assumptions')]};tool='interpret_event'
            else:
                validated=[];adaptations=[]
                for e,r,data in generated:
                    if cancelled(rid):return
                    if time.monotonic()-start>(300 if mode=='live' else 120):raise DomainError('RUN_TIMEOUT','Анализ превысил отведённое время',504)
                    bank_chunks=bank_by_event[e['id']]
                    try:data=validate_claims(data,profile,e,as_of,mode,row['user_id'],{ch['id'] for ch in bank_chunks})
                    except DomainError as error:
                        context=interpretation_context(profile,e,bank_chunks,as_of,role);context['repair_error_code']=error.code
                        data=generate(e,bank_chunks,error.code)
                        try:data=validate_claims(data,profile,e,as_of,mode,row['user_id'],{ch['id'] for ch in bank_chunks})
                        except DomainError as repair_error:
                            if not provider or not provider.cfg['free_only']:raise
                            model_errors.append({'event_id':e['id'],'code':repair_error.code,'message':'Ответ модели не прошёл проверку оснований; использованы локальные правила'})
                            engines[e['id']]='rules'
                            data=validate_claims(fixture_interpret(profile,e,role,bank_chunks),profile,e,as_of,mode,row['user_id'],{ch['id'] for ch in bank_chunks})
                        adaptations.append({'event_id':e['id'],'attempt':1,'tool':'repair_interpretation','reason':error.code,'decision':'Исправлена интерпретация; основания проверены повторно'})
                    if data['missing_data']:
                        query=bank_query(e,expanded=True);matches=search_bank(query,as_of,mode,row['user_id'])
                        additional=[ch for ch in matches if ch['id'] not in {old['id'] for old in bank_chunks} and ch.get('bank_verified') and not ch.get('is_synthetic')]
                        if additional and (not provider or not data['bank_view']['claims']):
                            bank_chunks=bank_chunks+additional
                            data=generate(e,bank_chunks)
                            data=validate_claims(data,profile,e,as_of,mode,row['user_id'],{ch['id'] for ch in bank_chunks})
                        adaptations.append({'event_id':e['id'],'attempt':2,'tool':'search_bank_documents','query':query,'matches':len(matches),'new_verified_chunks':len(additional),'decision':'Добавлены новые основания; интерпретация пересчитана' if additional and (not provider or not data['bank_view']['claims']) else 'Текущие основания сохранены; недостающие условия компании остаются неизвестными'})
                    insight={'id':'ins-'+uuid.uuid4().hex[:18],'schema_version':'1.0','profile_version':profile['version'],'run_id':rid,'company_id':uid,'event_id':e['id'],'role':role,'mode':mode,'as_of':as_of,'created_at':now(),'title':e['title'],'published_at':e['published_at'],'topic':e['topic'],'is_synthetic':e['is_synthetic'],'relevance':r,'impact':('mixed' if len({h['kind'] for h in data['hypotheses'] if not h['text'].startswith('Недостаточно')})>1 else next((h['kind'] for h in data['hypotheses'] if not h['text'].startswith('Недостаточно')),'neutral')),'evidence_status':'partial' if data['missing_data'] else 'supported','source_document_ids':e['source_document_ids'],'warnings':(['external_context_not_authorized'] if provider and not external_allowed else [])+(['synthetic_event'] if e['is_synthetic'] else [])+(['llm_unavailable_local_rules'] if provider and engines.get(e['id'])=='rules' else [])+(['rss_excerpt'] if any(doc.get('content_extent')=='feed_excerpt' for doc in e.get('source_metadata',[])) else []),**data}
                    used={eid for claim in data['bank_view']['claims'] for eid in claim['evidence_ids']}
                    insight['bank_evidence']=[{key:ch.get(key) for key in ('id','document_id','title','published_at','page_number','origin_url','publisher','extraction_method','ocr_warning')} for ch in bank_chunks if ch['id'] in used]
                    insight['source_metadata']=e.get('source_metadata',[])
                    model_used=provider and engines.get(e['id']) in ('model','cache')
                    insight['interpretation_version']='approved-api-v3' if model_used else 'offline-rules-v3'
                    insight['analysis_method']='Gemma 4 · кэш проверенного ответа' if model_used and engines[e['id']]=='cache' else provider.cfg['alias'] if model_used else 'Локальные правила'
                    if model_used:
                        with db() as c:c.execute('INSERT OR REPLACE INTO interpretation_cache VALUES(?,?,?)',(context_key(e,bank_chunks),dumps(data),now()))
                    insight['summary']=data['company_links'][0]['text']+' '+data['hypotheses'][0]['text'] if data['company_links'] and data['hypotheses'] else 'Недостаточно данных'
                    validated.append(insight)
                result={'verified':len(validated),'adaptive_searches':adaptations,'bank_claims':sum(len(i['bank_view']['claims']) for i in validated),'model_errors':model_errors,'cache_hits':model_cache_hits};tool='validate_claims'
            log_step(rid,step,result,tool,time.monotonic()-tick)
        if cancelled(rid):return
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT status FROM runs WHERE id=?',(rid,)).fetchone()[0]=='cancelled':return
            for i in validated:c.execute('INSERT INTO insights VALUES(?,?,?,?,?)',(i['id'],rid,uid,i['event_id'],dumps(i)))
            p=unpack(c.execute('SELECT payload FROM runs WHERE id=?',(rid,)).fetchone());p.update({'event_count':len([i for i in validated if i['relevance']['score']>=30]),'elapsed_ms':round((time.monotonic()-start)*1000),'tokens_in':provider.tokens_in if provider else 0,'tokens_out':provider.tokens_out if provider else 0,'cost_rub':str(provider.spent) if provider else '0','provider':provider.cfg['alias'] if provider else 'Локальные правила','model_errors':model_errors,'model_cache_hits':model_cache_hits,'model_calls':provider.calls if provider else 0,'fallback_events':sum(v=='rules' for v in engines.values()) if provider else 0})
            c.execute('UPDATE runs SET status=?,payload=?,ended_at=? WHERE id=?',('completed_with_limits' if model_errors or any(i['missing_data'] for i in validated) else 'completed',dumps(p),now(),rid))
    except Exception as error:
        with db() as c:
            p=unpack(c.execute('SELECT payload FROM runs WHERE id=?',(rid,)).fetchone());p['error']={'code':getattr(error,'code','ANALYSIS_ERROR'),'message':getattr(error,'message','Не удалось обновить анализ. Предыдущая версия сохранена')}
            c.execute('UPDATE runs SET status=?,payload=?,ended_at=? WHERE id=?',('budget_exceeded' if getattr(error,'status',None)==429 else 'failed',dumps(p),now(),rid))
    finally:
        if reservation:budget.settle(reservation,provider.spent if provider else 0,uncertain=provider.uncertain if provider else False)

def create_run(uid,user,role='ceo',mode='fixture',as_of=None,idem=None,background=True,automatic=False):
    if role not in ('ceo','cfo','treasury') or mode not in ('fixture','live'):raise DomainError('INVALID_RUN','Недопустимая роль или режим',422)
    if mode=='live':
        from .batches import create_batch
        result=create_batch([uid],user,role,mode,as_of,idem,background)
        return {'id':result['run_ids'][uid],'status':result['status'],'batch_id':result['batch_id']}
    profile=get_profile(uid);as_of=as_of or (AS_OF if mode=='fixture' else now())
    try:
        dt=datetime.fromisoformat(as_of)
        if dt.tzinfo is None:raise ValueError()
        as_of=dt.astimezone(timezone.utc).isoformat()
    except ValueError:raise DomainError('INVALID_DATE','Дата анализа должна содержать часовой пояс',422)
    if mode=='live':LiveProvider(setting()['run_budget'])
    rid='run-'+uuid.uuid4().hex[:16];reservation=None
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if idem:
            old=c.execute('SELECT id,status FROM runs WHERE user_id=? AND company_id=? AND idem=?',(user,uid,idem)).fetchone()
            if old:return dict(old)
        running=c.execute("SELECT id FROM runs WHERE user_id=? AND company_id=? AND status IN ('queued','running')",(user,uid)).fetchone()
        if running:return {'id':running['id'],'status':'running'}
        with_corpus=[dict(r) for r in c.execute('SELECT id,sha256,active FROM sources WHERE mode=? ORDER BY id',(mode,))]
        cache_key=hashlib.sha256(dumps([profile['version'],profile.get('clarification_version'),with_corpus,role,as_of,mode,RULES['version'],'prompt-v2',__import__('os').getenv('ALFA_MODEL_ID','fixture')]).encode()).hexdigest()
        p={'profile_snapshot':profile,'steps':[],'step':0,'automatic':automatic,'provider':'Детерминированный учебный провайдер' if mode=='fixture' else 'Разрешённый API','event_count':None,'tokens_in':0,'tokens_out':0,'cost_rub':'0'}
        c.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(rid,uid,user,role,mode,as_of,'queued',dumps(p),now(),None,idem,cache_key))
    if mode=='live' and not __import__('backend.app.llm',fromlist=['configuration']).configuration()['free_only']:
        try:reservation=budget.reserve(rid,setting()['run_budget'])
        except DomainError:
            with db() as c:c.execute("UPDATE runs SET status='budget_exceeded',ended_at=? WHERE id=?",(now(),rid))
            raise
        with db() as c:p['reservation']=reservation;c.execute('UPDATE runs SET payload=? WHERE id=?',(dumps(p),rid))
    if background:EXECUTOR.submit(execute_run,rid)
    else:execute_run(rid)
    return {'id':rid,'status':'queued'}
