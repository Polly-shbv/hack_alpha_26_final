import csv, io, hashlib, json, zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from .db import db, dumps, now
from .errors import DomainError
SELECTED=['U_9250','U_1941','U_106783']
LABELS=dict(zip(SELECTED,['Строительство','Оптовая торговля','Электрооборудование']))
ALIASES={'КИТАЙ':'Китай','ЕГИПЕТ':'Египет','ТУРЦИЯ':'Турция','ОАЭ':'ОАЭ','ЛИТВА':'Литва'}
FLAGS={'credit':'CL_PRODUCT_LOA_MARK','leasing':'CL_PRODUCT_LEA_MARK','rko':'CL_PRODUCT_RKO_MARK','guarantee':'CL_PRODUCT_GAR_MARK','deposit':'CL_PRODUCT_DEP_MARK','salary':'CL_PRODUCT_ZPP_MARK','factoring':'FACTORING_MARK'}
def flag(value):
    if value in ('1','1.0'): return True
    if value in ('0','0.0'): return False
    return None

def csv_rows(raw):
    try: text=raw.decode('utf-8-sig'); encoding='utf-8-sig'
    except UnicodeDecodeError: text=raw.decode('cp1251'); encoding='cp1251'
    try: dialect=csv.Sniffer().sniff(text[:12000],delimiters=';,\t')
    except csv.Error: raise DomainError('CSV_STRUCTURE','Не удалось определить разделитель CSV')
    return csv.DictReader(io.StringIO(text),dialect=dialect),encoding,dialect.delimiter

def normalize(row,source,stamp):
    uid=row['SYNTHETIC_U']
    value=row.get('REVENUE_COMPANY','')
    try: revenue=format(Decimal(value),'f') if value else None
    except InvalidOperation: raise DomainError('INVALID_MONEY','Некорректная выручка')
    if revenue is not None and Decimal(revenue)<0: raise DomainError('INVALID_MONEY','Отрицательная выручка')
    countries=[ALIASES.get(x.strip(),x.strip().capitalize()) for x in row.get('FTS_COUNTRY_NAME','').split(',') if x.strip()]
    p={'id':uid,'industry':LABELS.get(uid,row.get('MAINOKVED_NAME') or 'Нет данных'),'okved':row.get('MAINOKVED_CODE') or None,'okved_name':row.get('MAINOKVED_NAME') or None,'revenue_decimal':revenue,'region':row.get('REGION') or None,'city':row.get('CITY') or None,'group_id':row.get('GROUP_ID') or None,'countries':countries,'products':{k:flag(row.get(v)) for k,v in FLAGS.items()},'provenance':'source_csv','imported_at':stamp,'source':source,'raw':row,'field_evidence':{},'unknowns':['Товарный состав поставок','Условия договоров','Доля затрат на сырьё','Маршруты'],'stage1_report':{'provenance':'stage1_report','label':'По отчёту первого этапа'}}
    fields={'industry':'MAINOKVED_NAME','okved':'MAINOKVED_CODE','revenue_decimal':'REVENUE_COMPANY','region':'REGION','city':'CITY','group_id':'GROUP_ID','countries':'FTS_COUNTRY_NAME',**FLAGS}
    p['field_evidence']={k:{'id':uid+':'+k,'field':v,'raw_value':row.get(v),'file':source,'row_key':uid,'imported_at':stamp} for k,v in fields.items()}
    return p

def safe_archive(raw):
    if len(raw)>50*1024*1024: raise DomainError('FILE_TOO_LARGE','Архив превышает 50 МБ',413)
    try: z=zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile: raise DomainError('BAD_ARCHIVE','Некорректный ZIP')
    entries=z.infolist()
    if len(entries)>100 or sum(i.file_size for i in entries)>200*1024*1024: raise DomainError('ARCHIVE_LIMIT','Превышен лимит распакованного архива')
    result=[]
    for i in entries:
        path=Path(i.filename)
        if path.is_absolute() or '..' in path.parts or (i.external_attr>>16)&0o170000==0o120000: raise DomainError('UNSAFE_ARCHIVE','Небезопасный путь в архиве')
        if i.is_dir() or i.filename.startswith('__MACOSX') or path.name.startswith('.'): continue
        if path.suffix.lower()!='.csv': raise DomainError('UNSUPPORTED_FILE','В архиве допускаются только CSV')
        if i.file_size>max(1,i.compress_size)*1000: raise DomainError('ARCHIVE_LIMIT','Недопустимая степень сжатия')
        result.append((path.name,z.read(i)))
    return result

def import_datasets(files,preview=False):
    flattened=[]
    for name,raw in files:
        flattened.extend(safe_archive(raw) if name.lower().endswith('.zip') else [(Path(name).name,raw)])
    report={'accepted':0,'rejected':0,'warnings':[],'files':[],'profiles':[],'preview':[]}
    profiles={}; universe=set(); payment_rows=[]; incoming={i:set() for i in SELECTED}; outgoing={i:set() for i in SELECTED}; links=set(); stamp=now()
    # A full raw row is never sent to an LLM. Files are parsed one row at a time.
    for name,raw in flattened:
        if not name.lower().endswith('.csv'): raise DomainError('UNSUPPORTED_FILE','Загрузите CSV или ZIP с CSV')
        sha=hashlib.sha256(raw).hexdigest(); reader,enc,sep=csv_rows(raw); columns=reader.fieldnames or []
        kind='companies' if 'REVENUE_COMPANY' in columns else 'payments' if {'PAYER','BENEFICIARY','PAY_AMT','PAY_CNT'}<=set(columns) else 'links' if {'SYNTHETIC_F','SYNTHETIC_U'}<=set(columns) else None
        if not kind: raise DomainError('CSV_STRUCTURE','Неизвестная структура таблицы')
        item={'file':name,'kind':kind,'encoding':enc,'delimiter':sep,'sha256':sha,'accepted':0,'rejected':0,'columns':columns}
        seen={}
        for n,row in enumerate(reader,2):
            if len(report['preview'])<20: report['preview'].append({'file':name,'row':n,'columns':columns,'values':row if kind=='companies' else {'structure':'Подтверждена; связи скрыты'}})
            if preview and n>22: break
            try:
                if any(v is None for v in row.values()) or None in row: raise ValueError('Неверное число колонок')
                if row.get(columns[0])==columns[0]: raise ValueError('Повторный заголовок')
                if kind=='companies':
                    uid=row['SYNTHETIC_U']; normalized=normalize(row,name,stamp)
                    if not uid.startswith('U_'): raise ValueError('Некорректный ID')
                    fingerprint=hashlib.sha256(dumps(row).encode()).hexdigest()
                    if uid in seen:
                        if seen[uid]!=fingerprint: raise ValueError('Несовпадающие дубликаты компании')
                        report['warnings'].append({'file':name,'row':n,'message':'Повтор компании'}); continue
                    seen[uid]=fingerprint; universe.add(uid)
                    if uid in SELECTED: profiles[uid]=normalized
                elif kind=='payments':
                    payer,ben=row['PAYER'],row['BENEFICIARY']; amt=Decimal(row['PAY_AMT']); count=Decimal(row['PAY_CNT'])
                    if not amt.is_finite() or amt<0 or count<0 or count!=int(count): raise ValueError('Некорректный платёж')
                    if payer==ben: report['warnings'].append({'file':name,'row':n,'message':'Самоперевод исключён'}); continue
                    if ben in incoming: incoming[ben].add(payer)
                    if payer in outgoing: outgoing[payer].add(ben)
                    if payer in SELECTED or ben in SELECTED: payment_rows.append((payer,ben,str(amt),int(count),sha))
                else: links.add(row['SYNTHETIC_U'])
                item['accepted']+=1
            except (ValueError,InvalidOperation,DomainError,KeyError) as e:
                item['rejected']+=1
                if len(report['warnings'])<200: report['warnings'].append({'file':name,'row':n,'message':str(e) if not isinstance(e,DomainError) else e.message})
        report['accepted']+=item['accepted'];report['rejected']+=item['rejected'];report['files'].append(item)
    if preview: return report
    if universe and links-universe: report['warnings'].append({'message':'Связи с отсутствующими компаниями','count':len(links-universe)})
    for uid,p in profiles.items():
        p['payment_links']={'payers':len(incoming[uid]),'beneficiaries':len(outgoing[uid]),'source':'source_csv'} if any(f['kind']=='payments' for f in report['files']) else None
        p['version']=hashlib.sha256(dumps(p['raw']).encode()).hexdigest()[:16]
    with db() as c:
        for uid,p in profiles.items(): c.execute('INSERT INTO companies VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,version=excluded.version',(uid,dumps(p),p['version']))
        c.executemany('INSERT OR IGNORE INTO payment_edges VALUES(?,?,?,?,?)',payment_rows)
        for item in report['files']: c.execute('INSERT OR IGNORE INTO dataset_imports VALUES(?,?)',(item['sha256'],dumps(item)))
    report['profiles']=list(profiles);report['unique_companies']=len(universe)
    return report
