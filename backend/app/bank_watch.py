"""Optional official bank PDF watches. Unknown publication dates stay unknown."""
import hashlib
import json
import uuid
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup
from .db import db,dumps,now
from .errors import DomainError
from .security import fetch_public,canonical_url
from .ingestion import import_pdf
from .content import normalize_date

def official_url(url):
    host=(urlsplit(url).hostname or '').lower()
    if urlsplit(url).scheme!='https' or not (host=='alfabank.ru' or host.endswith('.alfabank.ru')):
        raise DomainError('BANK_ORIGIN','Для автоматического подтверждения используйте HTTPS-адрес на домене alfabank.ru',422)
    return canonical_url(url)

def watches():
    with db() as c:return [json.loads(r[0]) for r in c.execute('SELECT payload FROM bank_watches ORDER BY id')]

def add_watch(url,title,published_at=None):
    url=official_url(url)
    if published_at and not normalize_date(published_at):raise DomainError('INVALID_DATE','Укажите дату публикации с часовым поясом',422)
    with db() as c:
        if c.execute('SELECT count(*) FROM bank_watches').fetchone()[0]>=5:raise DomainError('BANK_WATCH_LIMIT','Для локального демо допускается до пяти банковских адресов',422)
        uid='bank-watch-'+uuid.uuid4().hex[:12]
        p={'id':uid,'url':url,'title':title,'published_at':normalize_date(published_at),'enabled':True,'documents':{},'last_checked':None,'error':None}
        c.execute('INSERT INTO bank_watches VALUES(?,?)',(uid,dumps(p)))
    return p

def remove_watch(uid):
    with db() as c:c.execute('DELETE FROM bank_watches WHERE id=?',(uid,))

def scan(mode,fetcher=fetch_public):
    result={'changed':0,'items':[],'checked_at':now()}
    for watch in watches():
        try:
            raw,_,final=fetcher(watch['url']);official_url(final)
            direct=raw.startswith(b'%PDF-')
            if direct:documents=[(final,raw)]
            else:
                soup=BeautifulSoup(raw,'html.parser');urls=[]
                for anchor in soup.select('a[href]'):
                    url=urljoin(final,anchor['href'])
                    if urlsplit(url).path.lower().endswith('.pdf'):
                        try:urls.append(official_url(url))
                        except DomainError:continue
                documents=[]
                candidates=list(dict.fromkeys(urls));candidates.sort(key=lambda url:watch['documents'].get(mode+' '+url,{}).get('checked_at',''))
                for url in candidates[:2]:
                    data,_,target=fetcher(url);official_url(target);documents.append((target,data))
            for url,data in documents:
                digest=hashlib.sha256(data).hexdigest();key=mode+' '+url;previous=watch['documents'].get(key)
                if previous and previous['sha256']==digest:
                    previous['checked_at']=now();continue
                imported=import_pdf(data,urlsplit(url).path.rsplit('/',1)[-1] or 'bank.pdf',ocr=True,title=watch['title'],
                                    published_at=watch['published_at'] if direct else None,kind='bank',mode=mode,owner='public',
                                    origin_url=url,publisher='Альфа-Банк · официальный сайт',bank_verified=True,synthetic=False,
                                    revision_key=digest,provenance={'type':'official_bank_watch','watch_id':watch['id'],'date_unknown':not(direct and watch['published_at'])})
                if previous and previous['document_id']!=imported['id']:
                    with db() as c:c.execute('UPDATE sources SET active=0 WHERE id=?',(previous['document_id'],))
                watch['documents'][key]={'sha256':digest,'document_id':imported['id'],'checked_at':now()}
                result['changed']+=int(imported['status']!='duplicate')
                result['items'].append({'url':url,**imported,'date_unknown':not(direct and watch['published_at'])})
            watch['error']=None
        except Exception as error:
            watch['error']=getattr(error,'message','Не удалось проверить банковский адрес')
            result['items'].append({'watch_id':watch['id'],'status':'failed','error':watch['error']})
        watch['last_checked']=now()
        with db() as c:c.execute('UPDATE bank_watches SET payload=? WHERE id=?',(dumps(watch),watch['id']))
    return result
