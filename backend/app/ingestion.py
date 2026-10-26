import hashlib,io,re,uuid
from pathlib import Path
from datetime import datetime, timezone
from pypdf import PdfReader
from bs4 import BeautifulSoup
from .db import db,dumps,now,ROOT,uploads_dir
from .security import fetch_public,canonical_url
from .errors import DomainError
from .content import extract_content,normalize_date
from .stories import store_event

def plain_text(value):
    soup=BeautifulSoup(value,'html.parser')
    for el in soup(['script','style','iframe','object']):el.decompose()
    return soup.get_text(' ',strip=True)
def add_document(title,text,published_at=None,kind='news',mode='fixture',origin_url=None,publisher=None,owner='demo',pages=None,file_raw=None,filename=None,bank_verified=False,synthetic=True,extraction_method=None,ocr_metadata=None,provenance=None,revision_key=None):
    title=plain_text(title)[:300]; text=plain_text(text)
    if published_at:
        try:
            parsed=datetime.fromisoformat(published_at.replace('Z','+00:00'))
            if parsed.tzinfo is None:raise ValueError()
            published_at=parsed.astimezone(timezone.utc).isoformat()
        except (ValueError,AttributeError):raise DomainError('INVALID_DATE','Укажите дату публикации с часовым поясом',422)
    sha=hashlib.sha256((mode+'\n'+kind+'\n'+owner+'\n'+(canonical_url(origin_url) if origin_url else '')+'\n'+text+('\n'+revision_key if revision_key else '')).encode()).hexdigest()
    with db() as c:
        existing=c.execute('SELECT id,payload FROM sources WHERE sha256=?',(sha,)).fetchone()
        if existing:
            previous=__import__('json').loads(existing['payload'])
            if published_at and not previous.get('published_at'):
                previous.update(published_at=published_at,analysis_eligible=bool(previous['chunks']))
                previous.setdefault('metadata_history',[]).append({'at':now(),'field':'published_at','before':None,'after':published_at,'reason':'Дата предоставлена при повторном импорте'})
                c.execute('UPDATE sources SET published_at=?,payload=? WHERE id=?',(published_at,dumps(previous),existing['id']))
            return {'id':existing['id'],'status':'duplicate'}
        uid='doc-'+uuid.uuid4().hex[:16]; chunks=[]
        for pn,content in (pages or [(None,text)]):
            for offset in range(0,len(content),3600):
                piece=content[offset:offset+4200]
                if piece.strip(): chunks.append({'id':f'{uid}#chunk-{len(chunks)+1}','document_id':uid,'page_number':pn,'offset_start':offset,'offset_end':offset+len(piece),'text':piece})
        status='ready' if chunks else 'needs_ocr'
        p={'id':uid,'title':title,'text':text,'kind':kind,'mode':mode,'origin_url':canonical_url(origin_url) if origin_url else None,'publisher':publisher or ('Учебный источник' if synthetic else 'Введено вручную'),'published_at':published_at,'fetched_at':now(),'sha256':sha,'text_status':status,'bank_verified':bank_verified and not synthetic,'is_synthetic':synthetic,'active':True,'pages':len(pages or []),'filename':Path(filename).name if filename else None,'extraction_method':'pypdf' if pages else 'text','chunks':chunks}
        p['extraction']=extract_content(title,text)
        p['analysis_eligible']=bool(chunks and published_at)
        if extraction_method:p['extraction_method']=extraction_method
        if ocr_metadata:p['ocr']=ocr_metadata
        if provenance:p['provenance']=provenance
        if file_raw:
            folder=uploads_dir();folder.mkdir(parents=True,exist_ok=True);(folder/(uid+'.pdf')).write_bytes(file_raw);p['file_id']=uid
        c.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(uid,kind,title,published_at,sha,dumps(p),1,mode,owner))
        for chunk in chunks:
            c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',(chunk['id'],uid,chunk['page_number'],chunk['offset_start'],chunk['offset_end'],chunk['text']))
            c.execute('INSERT INTO chunks_fts VALUES(?,?,?)',(chunk['id'],uid,chunk['text']))
        if kind=='news' and chunks:
            store_event(c,p,mode,owner)
    return {'id':uid,'status':status}

def classify(text):
    return extract_content('',text)['topic']
def import_pdf(raw,filename,ocr=False,**metadata):
    if len(raw)>20*1024*1024: raise DomainError('FILE_TOO_LARGE','Загрузите PDF до 20 МБ',413)
    if not raw.startswith(b'%PDF-'): raise DomainError('UNSUPPORTED_FILE','Формат файла не поддерживается. Загрузите PDF до 20 МБ')
    try:
        reader=PdfReader(io.BytesIO(raw))
        if reader.is_encrypted: raise DomainError('ENCRYPTED_PDF','Защищённый PDF не поддерживается')
        if len(reader.pages)>300: raise DomainError('PAGE_LIMIT','PDF содержит больше 300 страниц')
        pages=[(n+1,p.extract_text() or '') for n,p in enumerate(reader.pages)]
    except DomainError: raise
    except Exception: raise DomainError('INVALID_PDF','Не удалось прочитать PDF')
    if ocr and any(not text.strip() for _,text in pages):
        from .ocr import recognize_pdf
        recognized,audit=recognize_pdf(raw,[number for number,text in pages if not text.strip()])
        pages=[(number,text if text.strip() else recognized[number]) for number,text in pages]
        metadata.update(extraction_method=audit['engine'],ocr_metadata=audit)
    return add_document(metadata.pop('title',None) or Path(filename).name,'\n'.join(t for _,t in pages),pages=pages,file_raw=raw,filename=filename,**metadata)
def import_url(url,**metadata):
    metadata.setdefault('synthetic',False)
    raw,mime,final=fetch_public(url)
    if raw.startswith(b'%PDF-'):
        from urllib.parse import urlsplit
        metadata.setdefault('publisher',urlsplit(final).hostname)
        return import_pdf(raw,'source.pdf',origin_url=final,**metadata)
    from .publishers import extract_article
    extracted=extract_article(raw,final,{'published_at':metadata.pop('published_at',None)})
    for key in ('publisher','extraction_method','provenance'):
        metadata.setdefault(key,extracted.pop(key))
    return add_document(**extracted,origin_url=final,**metadata)
def search_bank(query,as_of,mode,owner='demo'):
    tokens=re.findall(r'[\w]+',query.lower())[:15]
    if not tokens:return []
    tokens=list(dict.fromkeys(t for t in tokens if len(t)>3 and not t.isdigit() and t not in ('материал','источник','учебный','новость')))
    if not tokens:return []
    match=' OR '.join('"'+t+'"*' for t in tokens)
    with db() as c:
        rows=c.execute('SELECT chunks_fts.id,bm25(chunks_fts) AS rank,s.payload FROM chunks_fts JOIN sources s ON s.id=chunks_fts.document_id WHERE chunks_fts MATCH ? AND s.kind=? AND s.active=1 AND s.mode=? AND s.published_at IS NOT NULL AND s.published_at<=? AND s.owner IN (?,?) ORDER BY rank LIMIT 30',(match,'bank',mode,as_of,'public',owner)).fetchall()
    result=[]
    import json
    for row in rows:
        p=json.loads(row['payload'])
        if mode=='live' and not p['bank_verified']:continue
        chunk=next(ch for ch in p['chunks'] if ch['id']==row['id'])
        # FTS matches must include meaningful topical overlap, not merely date or boilerplate.
        if not any(t in chunk['text'].lower() for t in tokens if len(t)>3):continue
        terms=[t for t in tokens if t in chunk['text'].lower()]
        age=max(0,(datetime.fromisoformat(as_of)-datetime.fromisoformat(p['published_at'])).days)
        score=6*sum(t in p['title'].lower() for t in tokens)+2*len(terms)+2/(1+age/90)+3*bool(p['bank_verified'] and not p['is_synthetic'])+min(1,-row['rank'])
        result.append({**chunk,'title':p['title'],'published_at':p['published_at'],'origin_url':p.get('origin_url'),'publisher':p.get('publisher'),'extraction_method':p.get('extraction_method'),'ocr_warning':p.get('ocr',{}).get('warning'),'bank_verified':p['bank_verified'],'is_synthetic':p['is_synthetic'],'match_terms':terms,'score':score,'reason':'Заголовок, тематический фрагмент, давность и подтверждённое происхождение'})
    result.sort(key=lambda item:(-item['score'],item['id']))
    selected=[];per_document={}
    for item in result:
        count=per_document.get(item['document_id'],0)
        if count>=2:continue
        selected.append(item);per_document[item['document_id']]=count+1
        if len(selected)==5:break
    return selected
