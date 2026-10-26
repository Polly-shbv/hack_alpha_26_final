"""Conservative offline interpretation for previously unseen documents.

This is a rule engine, never presented as a real LLM response. Every premise
and company feature has an evidence ID; unmatched events cause abstention.
"""
from datetime import datetime
from .content import event_content, company_sector
from .db import db
from .errors import DomainError

VERSION = 'interpretation-2.0'


def interpret(profile, event, role='ceo', bank_chunks=()):
    content = event_content(event)
    topic = event.get('topic', content['topic'])
    topics = set(content['topics']) | {topic}
    features = profile.get('field_evidence', {})
    products = profile.get('products', {})
    sector = company_sector(profile)
    with db() as connection:
        chunks = [dict(row) for sid in event.get('source_document_ids', []) for row in connection.execute('SELECT * FROM chunks WHERE document_id=?', (sid,))]
    fact = event.get('fact') or content['summary']
    support = next((chunk for chunk in chunks if ' '.join(fact.split()) in ' '.join(chunk['text'].split())), None)
    if not support:
        raise DomainError('NO_EVIDENCE', 'У события нет доступного фрагмента, подтверждающего факт', 422)
    facts = [{'claim_id': 'f1', 'text': fact, 'evidence_ids': [support['id']]}]
    if event.get('conflicts'):
        from .content import sentences
        other=next((ch for ch in chunks if ch['document_id']!=support['document_id']),None)
        if other:
            quote=next((s for s in sentences(other['text']) if 20<=len(s)<=1200),None)
            if quote:facts.append({'claim_id':'f2','text':quote,'evidence_ids':[other['id']]})
    links, hypotheses, actions = [], [], []
    missing = list(profile.get('unknowns', []))

    def mechanism(keys, why, consequence, action, conditions, opportunity=False):
        ids = [features[key]['id'] for key in keys if key in features]
        if not ids:
            return
        links.append({'text': why, 'field_evidence_ids': ids})
        hid = 'h' + str(len(hypotheses) + 1)
        hypotheses.append({'claim_id': hid, 'text': consequence, 'kind': 'opportunity' if opportunity else 'risk',
                           'premise_claim_ids': ['f1'], 'field_evidence_ids': ids, 'conditions': conditions, 'horizon': None})
        actions.append({'text': action, 'rationale_claim_ids': [hid], 'requires_data': conditions})
        missing.extend(conditions)

    direction = content['direction']
    if 'rate' in topics:
        key = 'credit' if products.get('credit') is True else 'leasing' if products.get('leasing') is True else None
        if key:
            opportunity = direction == 'down'
            instrument = 'кредитный признак' if key == 'credit' else 'лизинг'
            conditions = ['Тип ставки и формула пересмотра', 'График платежей и срок обязательств']
            action = {'ceo': 'Сопоставьте инвестиционный план с условиями финансирования и сроками окупаемости.',
                      'cfo': 'Сравните график обязательств и предложения рефинансирования с учётом комиссий и ковенантов.',
                      'treasury': 'Проверьте ближайшие платежи и запас ликвидности при действующих условиях финансирования.'}[role]
            consequence = ('Если ставка по договору пересматривается вслед за рыночной или доступно рефинансирование, снижение ставки может уменьшить будущие процентные расходы. Эффект на действующий договор не подтверждён.' if opportunity else
                           'Если компании потребуется новое финансирование или пересмотр плавающей ставки, стоимость денег может повлиять на денежный поток и сроки инвестиций. Изменение платежей по действующим договорам не установлено.')
            credit_note='Кредитный признак не отмечен. Это не доказывает отсутствие обязательств. ' if key=='leasing' and products.get('credit') is False else ''
            mechanism([key, 'industry'], credit_note+f'В профиле отмечен {instrument}; отрасль — {profile.get("industry", "не указана")}. Поэтому условия финансирования требуют отдельной проверки.', consequence, action, conditions, opportunity)
        if products.get('deposit') is True and direction == 'up':
            mechanism(['deposit'], 'В профиле отмечен депозитный продукт.',
                      'Если у компании есть свободная ликвидность и условия размещения пересматриваются, рост рыночных ставок может повысить доход от новых размещений.',
                      'Сопоставьте сроки размещения свободных средств с платёжным календарём.', ['Свободный остаток', 'Срок и ставка размещения'], True)
    if 'copper' in topics and sector in ('electrical', 'construction', 'metals'):
        mechanism(['industry', 'okved'], f'ОКВЭД {profile.get("okved", "не указан")} описывает отрасль, в которой возможна зависимость от металлов. Фактическое потребление меди не установлено.',
                  ('Если медь входит в закупки и цена договора пересматривается, снижение её цены может сократить затраты. Возможность зависит также от спроса и переноса экономии в цену продукции.' if direction == 'down' else
                   'Если медь входит в закупаемые материалы и рост цены нельзя перенести покупателю, возможное удорожание закупок может снизить маржу и увеличить потребность в оборотном капитале.'),
                  'Проверьте спецификации закупок, формулу цены и возможность изменения цены для покупателя.',
                  ['Фактическое использование меди', 'Доля меди в затратах', 'Формула цены и перенос затрат'], direction == 'down')
    countries = sorted((set(content['countries']) & set(profile.get('countries', []))) - {'Россия'})
    if countries and topics & {'china', 'logistics', 'regulation', 'fx'}:
        mechanism(['countries', 'industry'], 'В ФТС компании указаны ' + ', '.join(countries) + '. Это не подтверждает импорт, товар или действующий маршрут.',
                  'Если событие относится к товару, маршруту или валюте действующих контрактов компании, могут измениться сроки поставки, стоимость закупок и потребность в оборотном капитале.',
                  'Сопоставьте товарную группу, направление торговли, валюту договора и дату вступления мер с действующими контрактами.',
                  ['Товар и направление торговли', 'Условия и валюта контрактов', 'Срок действия ограничения или изменения'])
    if 'fx' in topics and profile.get('countries') and not countries:
        mechanism(['countries'], 'В профиле есть страны ФТС; валютные обязательства и направление торговли неизвестны.',
                  'Если есть открытая валютная позиция, изменение курса может повлиять на рублёвую стоимость платежей или выручки. Знак эффекта без направления позиции не определён.',
                  'Проверьте валютный платёжный календарь и встречные поступления по каждой валюте.', ['Валюта и направление позиции', 'Даты платежей', 'Условия хеджирования'])
    if not hypotheses and sector in content['sectors']:
        mechanism(['industry', 'okved'], f'В публикации упомянута отрасль, соответствующая ОКВЭД {profile.get("okved", "не указан")} компании.',
                  'Если описанное изменение затрагивает регион, продукцию и действующие контракты компании, могут измениться загрузка, выручка или затраты. Размер и направление эффекта требуют проверки.',
                  'Проверьте охват события по региону, продукции и портфелю заказов; сопоставьте его с планом продаж и затрат.',
                  ['Совпадение продукции и региона', 'Портфель заказов', 'Условия договоров'])
    if not hypotheses and topic in ('demand','inflation') and sector=='wholesale':
        mechanism(['industry','okved'], 'Оптовая торговля может зависеть от спроса покупателей и условий расчётов с ними; состав товаров в профиле не раскрыт.',
                  'Если изменение спроса относится к товарной группе компании, может измениться оборачиваемость запасов и срок возврата оборотного капитала. Рост агрегированного потребления не гарантирует роста продаж компании.',
                  'Сопоставьте продажи и запасы по товарным группам, сроки оплаты покупателей и график расчётов с поставщиками.',
                  ['Товарная группа', 'Оборачиваемость запасов', 'Сроки оплаты покупателей и поставщиков'])
    if not links:
        ids = [features[key]['id'] for key in ('industry', 'okved') if key in features]
        if ids:
            links.append({'text': 'Прямая связь события с подтверждёнными признаками компании не установлена. Отраслевая близость сама по себе не доказывает влияние.', 'field_evidence_ids': ids})
        missing.append('Подтверждённый механизм связи события с деятельностью компании')
        actions.append({'text': 'Уточните механизм связи и первоисточник до принятия решения.', 'rationale_claim_ids': [], 'requires_data': ['Товар, контракт или операция, затронутые событием']})
    if content['conflict'] or event.get('conflicts') or topic == 'conflict':
        missing.append('Официальный документ и проверка противоречивых сообщений')
    claims, document_ids = [], []
    for document_id in dict.fromkeys(chunk['document_id'] for chunk in bank_chunks):
        document_chunks=[chunk for chunk in bank_chunks if chunk['document_id']==document_id and chunk.get('bank_verified') and not chunk.get('is_synthetic')]
        if not document_chunks:continue
        # Quote a bounded extract instead of paraphrasing the bank or inventing its view.
        from .content import sentences
        import re
        opinion=lambda text:bool(re.search(r'\bмы\s+(?:ожида|счита|полага|видим)|\bнаш\w*\s+(?:взгляд|прогноз|мнени)|\bпо\s+нашему',text,re.I))
        candidates=[]
        for chunk in document_chunks:
            terms=chunk.get('match_terms',[])
            candidates.extend((sentence,chunk) for sentence in sentences(' '.join(chunk['text'].split())) if 30<=len(sentence)<=600 and (not terms or any(term in sentence.casefold() for term in terms)))
        candidates.sort(key=lambda item:not opinion(item[0]))
        text,chunk=candidates[0] if candidates else (None,None)
        if text:
            claims.append({'claim_id': 'b' + str(len(claims) + 1), 'text': text, 'evidence_ids': [chunk['id']],'kind':'expert_view' if opinion(text) else 'context'})
            document_ids.append(chunk['document_id'])
            if event.get('published_at') and (datetime.fromisoformat(event['published_at'])-datetime.fromisoformat(chunk['published_at'])).days>365:
                missing.append('Исторический материал банка старше года: нужны актуальные данные по событию')
        if len(claims) == 2:
            break
    if claims:
        for hypothesis in hypotheses:
            hypothesis['premise_claim_ids'].append(claims[0]['claim_id'])
            hypothesis['conditions'].append('Актуальность макроэкономического контекста банковского материала на дату события')
        missing.append('Применимость и актуальность банковского контекста для этого события и договоров компании')
    return {'facts': facts, 'company_links': links,
            'bank_view': {'status': 'supported' if claims else 'not_found', 'claims': claims, 'document_ids': list(dict.fromkeys(document_ids))},
            'hypotheses': hypotheses, 'actions': actions, 'missing_data': list(dict.fromkeys(missing)),
            'assumptions': ['Тестовый режим: интерпретация построена локальными правилами, без вызова LLM.']}
