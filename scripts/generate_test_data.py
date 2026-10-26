"""Explicitly synthetic engineering corpus. Never promoted into live."""
from pathlib import Path
import json,sys
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from backend.app.db import migrate,db,dumps
from backend.app.ingestion import add_document,import_pdf
import fitz
folder=root/'data/fixtures/test-documents';folder.mkdir(parents=True,exist_ok=True)
terms=['ставка финансирование','медь комплектующие','Китай ограничения','инфляция цены','потребление спрос','рынок труда','долговой рынок','макростатистика','золото рынок']
for n,term in enumerate(terms,1):
 p=folder/f'synthetic-bank-{n:02}.pdf'
 doc=fitz.open();page=doc.new_page()
 page.insert_htmlbox(fitz.Rect(50,50,545,790),f'<h1>Учебный документ банка {n:02}</h1><p>СИНТЕТИЧЕСКИЕ ТЕСТОВЫЕ ДАННЫЕ. Не публикация Альфа-Банка.</p><h2>{term}</h2><p>Дата: 22.10.2026. Этот документ используется только для проверки извлечения текста, номеров страниц и поиска. Условия договоров, структура затрат и товарная зависимость компании не известны. Изменения могут влиять на расходы только при подтверждении предпосылок.</p>')
 doc.save(p);doc.close()
 migrate();import_pdf(p.read_bytes(),p.name,title=f'Учебный документ банка {n:02}: {term}',kind='bank',published_at='2026-10-22T08:00:00+00:00',owner='public')
# Additional corpus for pagination, date filters, and performance. Distinct event IDs and source text.
extra=[]
for n in range(24):
 text=f'Учебный сценарий проверки {n+1:02}: ключевая ставка и стоимость финансирования требуют проверки условий обязательств. Это синтетический материал, не сообщение о реальном решении регулятора. Уровень ставки и финансовый эффект не заданы.'
 d=add_document(f'Учебный сценарий проверки финансирования {n+1:02}',text,published_at=f'2026-10-{1+n%20:02}T09:00:00+00:00',owner='public')
 # Extra corpus is opt-in, so normal demonstration keeps the six canonical scenarios.
 extra.append({'id':'perf-event-'+str(n),'title':f'Учебный сценарий {n+1:02}: финансирование','fact':text,'published_at':f'2026-10-{1+n%20:02}T09:00:00+00:00','topic':'rate','is_synthetic':True,'source_document_ids':[d['id']]})
 with db() as c:c.execute('DELETE FROM events WHERE id=?',('event-'+d['id'],))
(root/'data/fixtures/performance-events.json').write_text(json.dumps(extra,ensure_ascii=False,indent=2))
golden=[]
for e in json.loads((root/'data/fixtures/events.json').read_text()):
 for uid in ['U_9250','U_1941','U_106783']:
  golden.append({'event_id':e['id'],'company_id':uid,'expected_links':['leasing' if e['topic']=='rate' and uid=='U_106783' else 'credit' if e['topic']=='rate' else 'countries' if e['topic']=='china' else 'industry'],'prohibited_claims':['нет долга','доля рынка','https://invented','позиция банка подтверждена'],'expected_unknowns':['Условия договоров'],'cutoff':'2026-10-23T12:00:00+00:00','bank_view':'not_found','is_synthetic':True})
(root/'data/fixtures/golden-cases.json').write_text(json.dumps(golden,ensure_ascii=False,indent=2))
print('Generated 9 synthetic PDFs, 24 optional events, 18 golden cases')
