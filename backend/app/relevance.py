"""Explainable feature-based scorer, shared by known and unseen companies."""
from datetime import datetime
from .content import event_content, company_sector

VERSION = '2.1'


def rank(profile, event, role, as_of):
    published = event.get('published_at')
    if not published or datetime.fromisoformat(published) > datetime.fromisoformat(as_of):
        return None
    age = (datetime.fromisoformat(as_of) - datetime.fromisoformat(published)).days
    content = event_content(event)
    if content.get('business_eligible') is False and (event.get('title') or event.get('fact')):
        return {'score':0,'components':{k:0 for k in ('industry','products','geography','network','freshness','role_priority','uncertainty_penalty')},
                'reasons':['Вымышленный материал' if content.get('fictional') else 'В заголовке и начале статьи нет подтверждённого делового механизма'],
                'rules_version':VERSION,'feature_evidence_ids':[],'conditions':[],'classification_version':content['version'],'selection':'excluded'}
    topic = event.get('topic', content['topic'])
    topics = set(content['topics']) | {topic}
    sector = company_sector(profile)
    products = profile.get('products', {})
    reasons, evidence, conditions = [], [], []
    industry = 0
    if topic == 'rate':
        industry = 30 if sector == 'construction' else 15 if sector != 'unknown' else 0
    elif topic == 'copper':
        industry = {'electrical': 30, 'construction': 15, 'metals': 25}.get(sector, 0)
        if industry:
            conditions.append('Использование меди и условия закупок не подтверждены отраслью')
    elif topic == 'china':
        industry = 15 if 'Китай' in profile.get('countries', []) or sector in content['sectors'] else 0
    elif topic == 'conflict':
        industry = 15 if sector != 'unknown' else 0
    elif sector in content['sectors']:
        industry = 35
    elif topic in ('logistics', 'fx', 'regulation') and sector in ('wholesale', 'electrical', 'construction', 'transport'):
        industry = 15
    elif topic in ('energy', 'steel') and sector in ('construction', 'electrical', 'metals', 'transport'):
        industry = 20
    elif topic in ('demand', 'inflation') and sector in ('wholesale', 'agriculture'):
        industry = 25
    elif topic == 'labor' and sector != 'unknown':
        industry = 15
    if topic=='regulation' and set(content['countries'])-{'Россия'} and not (set(profile.get('countries',[])) & (set(content['countries'])-{'Россия'})):
        industry=0
    financing = 20 if 'rate' in topics and products.get('credit') is True else 10 if 'rate' in topics and products.get('leasing') is True else 0
    countries = set(profile.get('countries', [])) & set(content['countries']) - {'Россия'}
    # Older canonical events may carry only a topic, with no title/body.
    if topic == 'china' and 'Китай' in profile.get('countries', []):
        countries.add('Китай')
    geography = 5 if countries else 0
    if countries:
        conditions.append('ФТС подтверждает страну, но не направление торговли, товар или текущий контракт')
    role_priority = 0 if topic == 'gold' and not industry else (10 if role in ('cfo', 'treasury') and topic in ('rate', 'fx', 'copper', 'energy', 'inflation') else 10 if role == 'ceo' and topic == 'china' else 5)
    if topic == 'conflict' and role != 'ceo':
        role_priority = 0
    penalty = 10 * bool(topic in ('copper', 'china', 'conflict')) + 10 * bool(content['conflict'] or topic == 'conflict' or event.get('conflicts'))
    # With no business connection, freshness and a role alone must not pass the feed gate.
    if not industry and not financing and not geography:
        role_priority = 0
    components = {'industry': industry, 'products': financing, 'geography': geography, 'network': 0,
                  'freshness': 15 if age <= 7 else 10 if age <= 30 else 5 if age <= 90 else 0,
                  'role_priority': role_priority, 'uncertainty_penalty': penalty}
    if industry:
        reasons.append(f'Отраслевая связь по ОКВЭД ({profile.get("okved", "не указан")}): {industry}')
        evidence.extend([k for k in ('industry', 'okved') if k in profile.get('field_evidence', {})])
    if financing:
        key = 'credit' if products.get('credit') is True else 'leasing'
        reasons.append(f'Признак финансирования ({key}): {financing}')
        evidence.append(key)
    if geography:
        reasons.append('Страна в ФТС: ' + ', '.join(sorted(countries)) + '; товар не подтверждён: 5')
        evidence.append('countries')
    if penalty:
        reasons.append('Недостающий механизм или противоречие: −' + str(penalty))
    score = max(0, min(100, sum(v for k, v in components.items() if k != 'uncertainty_penalty') - penalty))
    return {'score': score, 'components': components, 'reasons': reasons, 'rules_version': VERSION,
            'feature_evidence_ids': [profile['field_evidence'][k]['id'] for k in dict.fromkeys(evidence) if k in profile.get('field_evidence', {})],
            'conditions': conditions, 'classification_version': content['version'],
            'selection': 'relevant' if score >= 50 else 'conditional' if score >= 30 else 'excluded'}
