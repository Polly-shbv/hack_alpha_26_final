"""Extractive, deterministic document understanding; no model or fixture IDs."""
import re
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

VERSION = 'content-2.1'
TOPICS = {
    'rate': ('Ставка и финансирование', [r'\bключев\w*\s+ставк', r'\bпроцентн\w*\s+ставк', r'\bрефинансирован', r'\bстоимост\w*\s+кредит',r'\bставк\w*\s+(?:сни|повыш|вырос|сохран|неизмен)']),
    'copper': ('Медь', [r'\b(?:медь|меди|медью)\b|\bмедн', r'\bcopper\b']),
    'gold': ('Золото', [r'\bзолот', r'\bgold\b']),
    'china': ('Внешняя торговля с Китаем', [r'\bкита[йяею]', r'\bchina\b']),
    'conflict': ('Противоречивые сведения', [r'\bрасходятся', r'\bпротиворечив', r'\bисточники\s+противоречат']),
    'fx': ('Валютные курсы', [r'\bвалютн\w*\s+курс', r'\bкурс\w*\s+(?:рубл|доллар|юан)', r'\bрубль\s+(?:ослаб|укреп)']),
    'logistics': ('Логистика', [r'\bлогист', r'\bгрузоперевоз', r'\bморск\w*\s+перевоз', r'\bконтейнер', r'\bдоставк']),
    'energy': ('Энергия и топливо', [r'\bнефт', r'\bдизел', r'\bэнергоресурс', r'\bэлектроэнерг', r'\bтариф\w*\s+на\s+газ', r'\bтоплив']),
    'inflation': ('Инфляция', [r'\bинфляц', r'\bпотребительск\w*\s+цен']),
    'labor': ('Рынок труда', [r'\bрын\w*\s+труда', r'\bбезработиц', r'\bзарплат', r'\bкадров']),
    'demand': ('Потребление и спрос', [r'\bпотреблен', r'\bпотребительск\w*\s+спрос', r'\bрозничн\w*\s+продаж']),
    'regulation': ('Регулирование', [r'\bпошлин', r'\bэкспортн\w*\s+огранич', r'\bсанкц', r'\bмаркировк', r'\bналог']),
    'construction': ('Строительство', [r'\bкадастр',r'\bземельн',r'земл.{0,45}участ',r'\bстроительств', r'\bстроительн', r'\bинфраструктур', r'\bгосзаказ']),
    'electrical': ('Электрооборудование', [r'\bэлектрооборуд', r'\bэлектротех', r'\bкабел', r'\bтрансформатор']),
    'steel': ('Сталь и металлы', [r'\bсталь', r'\bстали\b', r'\bсталелит', r'\bметаллург', r'\bsteel\b']),
}
COUNTRIES = {
    'Китай': r'\bкита[йяею]|\bchina\b', 'Турция': r'\bтурци|\bтурец',
    'Египет': r'\bегип', 'ОАЭ': r'\bоаэ\b|\bэмират', 'Литва': r'\bлитв|\bлитов',
    'Индия': r'\bиндии|\bиндия|\bиндий', 'США': r'\bсша\b|\bамерикан',
    'Сербия': r'\bсерби', 'Украина': r'\bукраин', 'Норвегия': r'\bнорвег',
    'Германия': r'\bгерман', 'Россия': r'\bросси|\bрф\b',
}
SECTORS = {
    'construction': r'\bстроитель|\bинфраструктур|\bдорожн|\bкадастр|\bземельн|земл.{0,45}участ',
    'electrical': r'\bэлектрооборуд|\bэлектротех|\bкабел|\bтрансформатор',
    'wholesale': r'\bоптов|\bдистрибьют|\bбытов\w*\s+товар',
    'agriculture': r'\bаграр|\bсельскохозяй|\bзерн|\bурожа',
    'transport': r'\bтранспорт|\bгрузоперевоз|\bлогист',
    'metals': r'\bметаллург|\bсталелит|\bсталь|\bстали\b',
}


def normalize_date(value):
    if not value:
        return None
    try:
        date = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        try:
            date = parsedate_to_datetime(value)
        except (ValueError, TypeError, IndexError):
            return None
    # A timezone is required. Guessing a publisher's timezone changes the cutoff.
    if date.tzinfo is None:
        return None
    return date.astimezone(timezone.utc).isoformat()


def normalized(text):
    return ' '.join(re.findall(r'[\w]+', unicodedata.normalize('NFKC', text).casefold().replace('ё', 'е')))


def sentences(text):
    # Do not split decimal numbers or turn abbreviations into unsupported claims.
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+(?=[А-ЯA-Z«])|\n{2,}', text) if s.strip()]


def extract_content(title, text):
    joined = title + '\n' + text
    # Only the headline/lead can establish a business topic. Related links later
    # in a publisher body must not turn sports/politics into company exposure.
    lead_text=re.split(r'Материалы по теме:|Читайте также:|Смотрите также:',text,flags=re.I)[0]
    lead=title+'\n'+' '.join(sentences(lead_text)[:2])[:700]
    fictional=bool(re.search(r'события.{0,35}вымышлен|вымышленн\w*\s+(?:новост|событ)|художественн\w*\s+вымысел',joined,re.I))
    consumer_only=bool(re.search(r'садоводств|огородничеств|личн\w*\s+подсобн\w*\s+хозяйств',text,re.I)) and not bool(re.search(r'юридическ\w*\s+лиц|коммерческ\w*\s+(?:участ|объект)|предприяти|компани',text,re.I))
    business=bool(re.search(r'ключев\w*\s+ставк|процентн\w*\s+ставк|стоимост\w*\s+кредит|рефинансирован|медь|меди|медн|кабел|зарплат|маркировк|инфляц|пошлин|экспортн\w*\s+огранич|торгов\w*\s+(?:сделк|барьер)|внешн\w*\s+торгов|импорт|экспорт|поставк|производств|закупк|себестоим|строитель|кадастр|земельн|перераспределен|электрооборуд|электротех|трансформатор|металлург|нефт|дизел|топлив|энергоресурс|электроэнерг|инвестиц|логист|грузоперевоз|контейнеровоз|валютн\w*\s+курс|курс\w*\s+(?:рубл|юан|доллар)|рын\w*\s+труда|безработиц|инфляц|потреблен|розничн\w*\s+продаж|налог|санкц',lead,re.I))
    topics, matches = [], []
    for topic, (_, patterns) in TOPICS.items():
        for pattern in patterns:
            hit = re.search(pattern, lead, re.I)
            if hit:
                topics.append(topic)
                matches.append({'topic': topic, 'quote': joined[hit.start():hit.end()], 'start': hit.start(), 'end': hit.end(), 'scope': 'title_and_text'})
                break
    low = joined.casefold()
    direction = 'unknown'
    if re.search(r'без\s+изменен|не\s+измен|сохран\w*.{0,45}ставк|не\s+(?:сниз|сниж|вырос|повыс|подорож|подешев|упал|увелич|сократ)', low):
        direction = 'unchanged'
    else:
        down = bool(re.search(r'\bсниз|\bсниж|\bупал|\bпаден|\bподешев|\bсократ', low))
        up = bool(re.search(r'\bвырос|\bрост|\bповыс|\bповыш|\bподорож|\bувелич', low))
        direction = 'mixed' if up and down else 'down' if down else 'up' if up else 'unknown'
    facts = []
    for sentence in sentences(text):
        if len(sentence) < 20:
            continue
        if len(sentence) > 1200:
            sentence = sentence[:1199].rsplit(' ', 1)[0]
        if sum(map(len, facts)) + len(sentence) > 1200:
            break
        facts.append(sentence)
        if len(facts) == 2:
            break
    fact = ' '.join(facts) or text[:1200]
    title_topics=[topic for topic in topics if any(re.search(pattern,title,re.I) for pattern in TOPICS[topic][1])]
    return {
        'version': VERSION, 'topic': title_topics[0] if title_topics else topics[0] if topics else 'no-bank', 'topics': topics,
        'business_eligible':business and not fictional and not consumer_only,'fictional':fictional,'consumer_only':consumer_only,
        'topic_evidence': matches, 'summary': fact, 'key_facts': facts,
        'countries': [country for country, pattern in COUNTRIES.items() if re.search(pattern, lead, re.I)],
        'sectors': [sector for sector, pattern in SECTORS.items() if re.search(pattern, lead, re.I)],
        'numbers': [{'value': h.group(0), 'start': h.start(), 'end': h.end(), 'scope': 'title_and_text'} for h in re.finditer(r'\b\d+(?:[.,]\d+)?\s*(?:%|млрд|млн|руб(?:лей|ля)?\b|доллар\w*)', joined)][:30],
        'direction': direction,
        'conflict': bool(re.search(r'\bрасходятся|\bпротиворечив|\bисточники\s+противоречат', low)),
        'limitations': ['Автоматическая классификация по словарям; не подтверждает экономическую причинность'],
    }


def event_content(event):
    return event.get('extraction') or extract_content(event.get('title', ''), event.get('fact', ''))


def company_sector(profile):
    prefix = str(profile.get('okved') or '').split('.')[0]
    if prefix in ('41', '42', '43'):
        return 'construction'
    if prefix in ('26', '27'):
        return 'electrical'
    if prefix == '46':
        return 'wholesale'
    if prefix in ('01', '02', '03'):
        return 'agriculture'
    if prefix in ('24', '25'):
        return 'metals'
    if prefix in ('49', '50', '51', '52', '53'):
        return 'transport'
    title = profile.get('industry', '') + ' ' + profile.get('okved_name', '')
    return next((sector for sector, pattern in SECTORS.items() if re.search(pattern, title, re.I)), 'unknown')
