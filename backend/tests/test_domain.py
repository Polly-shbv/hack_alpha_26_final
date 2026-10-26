import copy,io,json,zipfile,concurrent.futures
from decimal import Decimal
import pytest
from backend.app import db as database
from backend.app.db import db,ROOT,dumps
from backend.app.datasets import csv_rows,flag,import_datasets,safe_archive
from backend.app.analysis import rank,fixture_interpret,validate_claims,create_run,get_profile,AS_OF
from backend.app.errors import DomainError
from backend.app.seed import seed
from backend.app import budget
from backend.app.security import public_target,canonical_url
from backend.app.ingestion import add_document,import_pdf,search_bank
from backend.app.llm import LiveProvider,configuration
import httpx

@pytest.fixture
def context(database_path):
    seed();p=get_profile('U_9250')
    with db() as c:e=json.loads(c.execute("SELECT payload FROM events WHERE id='fixture-rate-001'").fetchone()[0])
    return p,e

@pytest.mark.parametrize('n,value',[('1.0',True),('0.0',False),('',None),('null',None),('unknown',None)])
def test_nullable_flags(n,value):assert flag(n) is value
@pytest.mark.parametrize('encoding,delimiter',[('utf-8-sig',';'),('utf-8',','),('cp1251','\t')])
def test_csv_encodings(encoding,delimiter):
    raw=(delimiter.join(['SYNTHETIC_U','REVENUE_COMPANY','MAINOKVED_NAME'])+'\n'+delimiter.join(['U_0001','0','Торговля'])+'\n').encode(encoding)
    reader,enc,sep=csv_rows(raw);r=list(reader);assert r[0]['SYNTHETIC_U']=='U_0001';assert r[0]['REVENUE_COMPANY']=='0';assert sep==delimiter

def test_csv_validation_and_counterparties(database_path):
    companies=b'SYNTHETIC_U;REVENUE_COMPANY\nU_9250;10\nU_9250;11\nSYNTHETIC_U;REVENUE_COMPANY\n'
    payments=b'PAYER;BENEFICIARY;PAY_AMT;PAY_CNT\nU_1;U_9250;10;1\nU_1;U_9250;5;1\nU_9250;U_2;2;1\nU_9250;U_9250;7;1\nU_3;U_9250;-1;1\n'
    r=import_datasets([('c.csv',companies),('p.csv',payments)])
    p=get_profile('U_9250');assert p['payment_links']['payers']==1;assert p['payment_links']['beneficiaries']==1;assert r['rejected']==3
    assert any(x['message']=='Самоперевод исключён' for x in r['warnings'])

def test_stage1_matches_actual_csv(context):
    for uid,rev,counts in [('U_9250','62340483339.0',(19,218)),('U_1941','24107304489.0',(504,110)),('U_106783','20828575458.0',(633,190))]:
        p=get_profile(uid);assert Decimal(p['revenue_decimal'])==Decimal(rev);assert (p['payment_links']['payers'],p['payment_links']['beneficiaries'])==counts
    assert get_profile('U_106783')['products']['credit'] is False
    assert get_profile('U_9250')['region']=='Иркутская область'

@pytest.mark.parametrize('uid,role,expected',[('U_9250','ceo',70),('U_9250','cfo',75),('U_1941','cfo',60),('U_106783','cfo',50)])
def test_rank_exact(context,uid,role,expected):
    _,e=context;r=rank(get_profile(uid),e,role,AS_OF);assert r['score']==expected;assert r['components']['network']==0

def test_missing_fields_monotonicity_and_future(context):
    p,e=context;baseline=rank(p,e,'cfo',AS_OF)['score'];p=copy.deepcopy(p);p['products']['credit']=None;p['products']['leasing']=None
    assert rank(p,e,'cfo',AS_OF)['score']<baseline
    e['published_at']='2026-10-24T00:00:00+00:00';assert rank(p,e,'ceo',AS_OF) is None

def test_fts_country_not_full_geography(context):
    p,_=context;e={'topic':'china','published_at':'2026-10-22T00:00:00+00:00'};r=rank(p,e,'ceo',AS_OF);assert r['components']['geography']==5;assert r['score']==35

def test_evidence_rejects_fabricated_fact(context):
    p,e=context;i=fixture_interpret(p,e);i['facts'][0]['text']='Выручка выросла на 40%'
    with pytest.raises(DomainError,match='Фактическое'):validate_claims(i,p,e,AS_OF,'fixture')
@pytest.mark.parametrize('fault',['missing','foreign','bank','hypothesis'])
def test_evidence_gates(context,fault):
    p,e=context;i=fixture_interpret(p,e)
    if fault=='missing':i['facts'][0]['evidence_ids']=['doc-invented#c1']
    if fault=='foreign':i['company_links'][0]['field_evidence_ids']=['U_1941:credit']
    if fault=='hypothesis':i['hypotheses'][0]['premise_claim_ids']=['missing']
    if fault=='bank':i['bank_view']={'status':'supported','claims':[i['facts'][0]],'document_ids':e['source_document_ids']}
    with pytest.raises(DomainError):validate_claims(i,p,e,AS_OF,'fixture')

def test_eighteen_golden_cases(context):
    cases=json.loads((ROOT/'data/fixtures/golden-cases.json').read_text());assert len(cases)==18
    for case in cases:
        p=get_profile(case['company_id'])
        with db() as c:e=json.loads(c.execute('SELECT payload FROM events WHERE id=?',(case['event_id'],)).fetchone()[0])
        i=validate_claims(fixture_interpret(p,e),p,e,case['cutoff'],'fixture')
        assert i['bank_view']['status']=='not_found';assert all(x in i['missing_data'] for x in case['expected_unknowns'])
        for key in case['expected_links']:assert p['id']+':'+key in i['company_links'][0]['field_evidence_ids']
        for bad in case['prohibited_claims']:assert bad not in dumps(i).lower()
        r=rank(p,e,'ceo',AS_OF)
        if e['topic']=='gold':assert r['score']<30
        if e['topic']=='conflict':assert any('Официальный' in s for s in i['missing_data'])

def test_budget_atomic_parallel(database_path):
    with db() as c:c.execute("INSERT INTO settings VALUES('global',?)",(dumps({'daily_budget':25,'run_budget':25}),))
    def attempt(n):
        try:return budget.reserve(str(n),25)
        except DomainError:return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:ids=list(ex.map(attempt,range(8)))
    assert len([x for x in ids if x])==1;assert Decimal(budget.snapshot()['reserved'])==25
    budget.settle(next(x for x in ids if x),actual=2);assert Decimal(budget.snapshot()['charged'])==2;assert Decimal(budget.snapshot()['reserved'])==0

def test_budget_unknown_outcome_and_midnight(database_path,monkeypatch):
    monkeypatch.setattr(budget,'today',lambda:'2026-10-23');uid=budget.reserve('r',25);budget.settle(uid,uncertain=True);assert budget.snapshot()['charged']=='25'
    monkeypatch.setattr(budget,'today',lambda:'2026-10-24');assert budget.snapshot()['charged']=='0'
@pytest.mark.parametrize('url',['file:///etc/passwd','javascript:alert(1)','http://localhost','http://127.0.0.1','http://169.254.169.254','https://[::1]','https://user:password@example.com'])
def test_ssrf(url):
    def resolver(*a,**kw):return [(2,1,6,'',('127.0.0.1',80))]
    with pytest.raises(DomainError):public_target(url,resolver)
def test_ssrf_mixed_dns_rebinding():
    def resolver(*a,**kw):return [(2,1,6,'',('8.8.8.8',80)),(2,1,6,'',('10.0.0.1',80))]
    with pytest.raises(DomainError):public_target('https://example.com',resolver)
    assert canonical_url('https://EXAMPLE.com/a?utm_source=x&id=1#test')=='https://example.com/a?id=1'
@pytest.mark.parametrize('name',['../hack.csv','/hack.csv','evil.py'])
def test_zip_traversal_and_type(name):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:z.writestr(name,'x')
    with pytest.raises(DomainError):safe_archive(stream.getvalue())

def test_ingestion_sanitization_dedup_and_fts(context):
    d=add_document('Учебный документ банка','<script>attack()</script><p>медь и расходы</p>',kind='bank',published_at='2026-10-22T00:00:00+00:00')
    assert add_document('Повтор','медь и расходы',kind='bank')['status']=='duplicate'
    result=search_bank('медь',AS_OF,'fixture');assert result and result[0]['document_id']==d['id'];assert search_bank('медь','2026-10-21T00:00:00+00:00','fixture')==[]

def test_real_pdf_pages_and_no_fake_bank(context):
    raw=(ROOT/'data/fixtures/test-documents/synthetic-bank-01.pdf').read_bytes();d=import_pdf(raw,'../../bad.pdf',kind='bank',published_at='2026-10-22T00:00:00+00:00')
    with db() as c:p=database.unpack(c.execute('SELECT payload FROM sources WHERE id=?',(d['id'],)).fetchone())
    assert p['filename']=='bad.pdf';assert p['chunks'][0]['page_number']==1;assert p['bank_verified'] is False;assert 'Учебный документ' in p['text']

def configure_live(monkeypatch):
    for k,v in {'ALFA_MODEL_ALIAS':'GLM-5.3-Flash','ALFA_MODEL_ID':'organizer-approved-test-id','ALFA_APPROVED_MODEL_ID':'organizer-approved-test-id','ALFA_API_BASE':'https://provider.example/v1','ALFA_API_KEY':'test-secret-not-real','ALFA_INPUT_RUB_PER_MILLION':'1','ALFA_OUTPUT_RUB_PER_MILLION':'2'}.items():monkeypatch.setenv(k,v)
def test_live_blocks_unknown_id_or_tariff(monkeypatch):
    configure_live(monkeypatch);assert configuration()['ready'];monkeypatch.setenv('ALFA_MODEL_ID','forbidden');assert not configuration()['ready']
    with pytest.raises(DomainError):LiveProvider(25)
    configure_live(monkeypatch);monkeypatch.delenv('ALFA_OUTPUT_RUB_PER_MILLION');assert not configuration()['ready']
def test_live_adapter_mocked_usage(monkeypatch):
    configure_live(monkeypatch)
    def handler(request):
        assert request.headers['Authorization']=='Bearer test-secret-not-real';assert json.loads(request.content)['model']=='organizer-approved-test-id'
        return httpx.Response(200,json={'usage':{'prompt_tokens':100,'completion_tokens':50},'choices':[{'message':{'content':json.dumps({'facts':[],'company_links':[],'bank_view':{'status':'not_found'},'hypotheses':[],'actions':[],'missing_data':[]})}}]})
    provider=LiveProvider(25,httpx.MockTransport(handler));assert provider.interpret({})['bank_view']['status']=='not_found';assert provider.spent==Decimal('0.0002')
@pytest.mark.parametrize('status',[429,500])
def test_provider_bounded_retries(monkeypatch,status):
    configure_live(monkeypatch);calls=[]
    def handler(req):calls.append(1);return httpx.Response(status)
    p=LiveProvider(25,httpx.MockTransport(handler))
    with pytest.raises(DomainError):p.interpret({})
    assert len(calls)==3;assert p.spent>0

def test_positive_case_has_source_premise_and_explicit_condition(context):
    p,_=context
    text='Учебный сценарий: цена входного ресурса снижена по опубликованному прайс-листу. Совпадение ресурса с закупками компании требует проверки.'
    doc=add_document('Учебное снижение цены',text,published_at='2026-10-22T00:00:00+00:00',owner='public')
    event={'id':'positive-unit','topic':'copper','fact':text,'source_document_ids':[doc['id']]}
    result=fixture_interpret(p,event);result['hypotheses'][0]['kind']='opportunity';result['hypotheses'][0]['text']='Если ресурс используется и договорная цена пересматривается, возможно снижение затрат.';result['hypotheses'][0]['conditions']=['Ресурс фактически используется','Цена договора пересматривается']
    assert validate_claims(result,p,event,AS_OF,'fixture')['hypotheses'][0]['kind']=='opportunity'
    result['facts'][0]['text']='Цена ресурса снижена на 20%'
    with pytest.raises(DomainError):validate_claims(result,p,event,AS_OF,'fixture')
