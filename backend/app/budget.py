from decimal import Decimal
from datetime import datetime
from zoneinfo import ZoneInfo
import uuid
from .db import db,setting
from .errors import DomainError

def today(): return datetime.now(ZoneInfo('Europe/Moscow')).date().isoformat()
def totals(c,day):
    rows=c.execute('SELECT reserved,charged,state FROM ledger WHERE date_msk=?',(day,)).fetchall()
    charged=sum((Decimal(r['charged']) for r in rows),Decimal(0))
    reserved=sum((Decimal(r['reserved']) for r in rows if r['state']=='reserved'),Decimal(0))
    return charged,reserved

def snapshot():
    s=setting()
    with db() as c: charged,reserved=totals(c,today())
    return {'date_msk':today(),'charged':str(charged),'reserved':str(reserved),'remaining':str(max(Decimal(0),Decimal(str(s['daily_budget']))-charged-reserved))}
def reserve(run_id,amount):
    amount=Decimal(str(amount));s=setting()
    if amount<=0 or amount>Decimal(str(s['run_budget'])): raise DomainError('RUN_BUDGET','Превышен лимит одного запуска',429)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        used,held=totals(c,today())
        if used+held+amount>Decimal(str(s['daily_budget'])): raise DomainError('BUDGET_EXCEEDED','Достигнут лимит анализа',429)
        uid=str(uuid.uuid4());c.execute('INSERT INTO ledger VALUES(?,?,?,?,?,?)',(uid,today(),run_id,str(amount),'0','reserved'))
    return uid

def settle(uid,actual=None,uncertain=False):
    with db() as c:
        c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT * FROM ledger WHERE id=?',(uid,)).fetchone()
        if not row or row['state']!='reserved': return
        charged=Decimal(row['reserved']) if uncertain else Decimal(str(actual or 0))
        if charged>Decimal(row['reserved']): raise DomainError('ACCOUNTING_ERROR','Стоимость превысила резерв',500)
        c.execute('UPDATE ledger SET reserved=?,charged=?,state=? WHERE id=?',('0',str(charged),'uncertain' if uncertain else 'settled',uid))
