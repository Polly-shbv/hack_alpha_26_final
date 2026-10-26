"""Load an audited case corpus, without network requests or implicit trust."""
import hashlib
import json
from pathlib import Path
from .db import ROOT
from .errors import DomainError
from .ingestion import add_document
from .content import normalize_date

CASE_FOLDER='https://drive.google.com/drive/folders/15xZTLF1lZ65p6o5xpNA_fRbxjoV-UlG1'


def load_case_corpus(folder=None,mode='fixture',owner='public'):
    folder=Path(folder or ROOT/'data/reference/bank')
    manifest_file=folder/'manifest.json'
    if not manifest_file.exists():return {'items':[],'note':'Корпус из кейса не установлен'}
    manifest=json.loads(manifest_file.read_text())
    if manifest.get('case_folder_url')!=CASE_FOLDER:raise DomainError('CORPUS_ORIGIN','Не подтверждён источник корпуса из кейса',422)
    items=[]
    for entry in manifest['items']:
        if not entry.get('source_confirmed') or not entry.get('title') or (not normalize_date(entry.get('published_at')) and not entry.get('publication_date_unknown')):
            raise DomainError('CORPUS_REVIEW','Подтвердите происхождение, название и дату каждого документа',422)
        for key in ('file','ocr_file'):
            if Path(entry[key]).name!=entry[key]:raise DomainError('CORPUS_PATH','Недопустимый путь в манифесте',422)
        sidecar=folder/entry['ocr_file']
        raw_ocr=sidecar.read_bytes()
        if hashlib.sha256(raw_ocr).hexdigest()!=entry['ocr_sha256']:raise DomainError('CORPUS_CHECKSUM','Контрольная сумма OCR не совпала',422)
        parsed=json.loads(raw_ocr)
        if parsed['original_sha256']!=entry['sha256']:raise DomainError('CORPUS_CHECKSUM','OCR относится к другой версии PDF',422)
        original=folder/entry['file'];raw=original.read_bytes() if original.exists() else None
        if raw and (len(raw)>40*1024*1024 or hashlib.sha256(raw).hexdigest()!=entry['sha256'] or not raw.startswith(b'%PDF-')):
            raise DomainError('CORPUS_CHECKSUM','Оригинал PDF не совпал с манифестом',422)
        pages=[(page['number'],page['text']) for page in parsed['pages']]
        if not pages or len(pages)>300 or [number for number,_ in pages]!=list(range(1,len(pages)+1)) or any(not text.strip() for _,text in pages):
            raise DomainError('CORPUS_PAGES','Проверьте извлечение всех страниц документа',422)
        provenance={'type':'case_reference','case_folder_url':CASE_FOLDER,'original_sha256':entry['sha256'],'ocr_sha256':entry['ocr_sha256'],'source_confirmed':True,'original_available_locally':bool(raw),'date_precision':entry.get('date_precision','day'),'reporting_period':entry.get('reporting_period'),'publication_date_unknown':entry.get('publication_date_unknown',False)}
        result=add_document(entry['title'],'\n'.join(text for _,text in pages),published_at=entry['published_at'],kind='bank',mode=mode,
                            owner=owner,origin_url=entry['origin_url'],publisher='Альфа-Банк · материалы кейса',pages=pages,
                            file_raw=raw,filename=entry['file'],bank_verified=True,synthetic=False,
                            extraction_method=parsed['ocr']['engine'],ocr_metadata=parsed['ocr'],provenance=provenance)
        items.append({'file':entry['file'],**result})
    return {'items':items}
