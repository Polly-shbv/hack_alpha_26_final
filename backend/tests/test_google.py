"""Google native API transport doubles. No real keys, requests or business data."""
import json
import httpx
import pytest
from backend.app import batches,collection,llm
from backend.app.db import db
from backend.app.errors import DomainError
from backend.tests.test_batches import setup_news,COMPANIES
from backend.tests.test_collection import configure_free,response_data,batch_result


def configure_google(monkeypatch):
    configure_free(monkeypatch)
    for k,v in {'ALFA_PROVIDER':'google','ALFA_MODEL_ID':'gemma-4-26b-a4b-it','ALFA_APPROVED_MODEL_ID':'gemma-4-26b-a4b-it','ALFA_API_BASE':'https://generativelanguage.googleapis.com/v1beta'}.items():monkeypatch.setenv(k,v)


def metadata():return {'name':'models/gemma-4-26b-a4b-it','supportedGenerationMethods':['generateContent']}

def response(data,**changes):
    result={'modelVersion':'gemma-4-26b-a4b-it','usageMetadata':{'promptTokenCount':500,'candidatesTokenCount':700},'candidates':[{'finishReason':'STOP','content':{'parts':[{'thought':True,'text':'private reasoning, not JSON'},{'text':json.dumps(data)}]}}]}
    result.update(changes);return result


def test_google_exact_configuration_guards(database_path,monkeypatch):
    configure_google(monkeypatch);assert llm.configuration()['ready']
    for key,value in [('ALFA_MODEL_ID','gemini-3.8-flash'),('ALFA_API_BASE','https://evil.example/v1beta'),('ALFA_FREE_ONLY','0'),('ALFA_OUTPUT_RUB_PER_MILLION','1')]:
        with monkeypatch.context() as m:
            m.setenv(key,value);assert not llm.configuration()['ready']


def test_native_request_auth_schema_usage_and_no_router_parameters(database_path,monkeypatch):
    configure_google(monkeypatch);calls=[]
    def handler(req):
        calls.append(req)
        assert req.headers['x-goog-api-key']=='synthetic-key' and 'authorization' not in req.headers
        assert 'synthetic-key' not in str(req.url)
        if req.method=='GET':return httpx.Response(200,json=metadata())
        assert req.url.path.endswith('/models/gemma-4-26b-a4b-it:generateContent')
        body=json.loads(req.content)
        assert set(body)=={'systemInstruction','contents','generationConfig'}
        assert body['generationConfig']['thinkingConfig']=={'thinkingLevel':'minimal'}
        return httpx.Response(200,json=response(response_data(),usageMetadata={'promptTokenCount':500,'candidatesTokenCount':700,'thoughtsTokenCount':5}))
    p=llm.LiveProvider(25,httpx.MockTransport(handler));assert p.interpret({})['bank_view']['status']=='not_found'
    assert p.calls==1 and p.tokens_in==500 and p.tokens_out==705 and p.spent==0
    assert [r.method for r in calls]==['GET','POST']
    assert collection.state('model_status')['cost_basis']=='documented_free_tariff'


@pytest.mark.parametrize('status,code',[(401,'LLM_AUTH'),(403,'LLM_AUTH'),(429,'FREE_RATE_LIMIT'),(503,'LLM_UNAVAILABLE')])
def test_google_errors_single_post_no_retry_and_sanitized(database_path,monkeypatch,status,code):
    configure_google(monkeypatch);calls=[]
    def handler(req):
        if req.method=='GET':return httpx.Response(200,json=metadata())
        calls.append(req);return httpx.Response(status,json={'error':{'message':'secret provider details synthetic-key'}})
    p=llm.LiveProvider(25,httpx.MockTransport(handler))
    with pytest.raises(DomainError) as error:p.interpret_batch({})
    assert error.value.code==code and len(calls)==1
    assert 'synthetic-key' not in collection.state('model_status')['message']


@pytest.mark.parametrize('change,code',[
    ({'usageMetadata':{}},'UNKNOWN_USAGE'),
    ({'candidates':[{'finishReason':'MAX_TOKENS'}]},'LLM_INCOMPLETE'),
    ({'candidates':[]},'LLM_INCOMPLETE'),
    ({'modelVersion':'other-model'},'MODEL_MISMATCH'),
    ({'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'invalid JSON'}]}}]},'LLM_RESPONSE'),
])
def test_google_rejects_missing_usage_truncation_wrong_model_and_invalid_json(database_path,monkeypatch,change,code):
    configure_google(monkeypatch)
    def handler(req):return httpx.Response(200,json=metadata() if req.method=='GET' else response(response_data(),**change))
    with pytest.raises(DomainError) as error:llm.LiveProvider(25,httpx.MockTransport(handler)).interpret_batch({})
    assert error.value.code==code


def test_google_one_cohort_post_and_second_batch_cache(database_path,monkeypatch):
    setup_news(monkeypatch);configure_google(monkeypatch);posts=[]
    def handler(req):
        if req.method=='GET':return httpx.Response(200,json=metadata())
        context=json.loads(json.loads(req.content)['contents'][0]['parts'][0]['text']);posts.append(context)
        return httpx.Response(200,json=response(batch_result(context)))
    monkeypatch.setattr(batches,'LiveProvider',lambda cap:llm.LiveProvider(cap,httpx.MockTransport(handler)))
    first=batches.create_batch(COMPANIES,'demo',mode='live',background=False)
    second=batches.create_batch(COMPANIES,'demo',mode='live',background=False)
    assert batches.get_batch(first['batch_id'])['status']=='completed'
    assert len(posts)==1 and len(posts[0]['companies'])==3 and len(posts[0]['tasks'])==9
    assert batches.get_batch(second['batch_id'])['payload']['cache_hits']==9
    with db() as c:assert c.execute('SELECT count(*) FROM model_requests').fetchone()[0]==1


def test_google_connection_metadata_get_only(database_path,monkeypatch):
    configure_google(monkeypatch);calls=[];original=httpx.Client
    def handler(req):calls.append(req.method);return httpx.Response(200,json=metadata())
    monkeypatch.setattr(llm.httpx,'Client',lambda **kw:original(**{**kw,'transport':httpx.MockTransport(handler)}))
    assert llm.check_connection()['generation_requests']==0 and calls==['GET']


def test_router_cooldown_does_not_block_new_google_provider(database_path,monkeypatch):
    configure_google(monkeypatch)
    collection.put_state('model_status',{'blocked_until':'2099-01-01T00:00:00+00:00','code':'OLD_ROUTER_ERROR'})
    def handler(req):return httpx.Response(200,json=metadata() if req.method=='GET' else response(response_data()))
    assert llm.LiveProvider(25,httpx.MockTransport(handler)).interpret({})
