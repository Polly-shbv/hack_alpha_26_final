"""Public publisher feeds and article selectors. No browser/cookie circumvention."""
from urllib.parse import urlsplit, urlunsplit
from bs4 import BeautifulSoup
from .content import normalize_date
from .errors import DomainError
from .ingestion import plain_text

PUBLISHERS = [
    {'id':'lenta','name':'Лента.ру','homepage':'https://lenta.ru/amp/','feed_url':'https://lenta.ru/rss/news','hosts':['lenta.ru'],'selectors':['.topic-body__content','.js-topic__text','article']},
    {'id':'ria','name':'РИА Новости','homepage':'https://ria.ru/','feed_url':'https://ria.ru/export/rss2/archive/index.xml','hosts':['ria.ru'],'selectors':['.article__body','[itemprop="articleBody"]','article']},
    {'id':'tass','name':'ТАСС','homepage':'https://tass.ru/','feed_url':'https://tass.ru/rss/v2.xml','hosts':['tass.ru'],'selectors':['[itemprop="articleBody"]','.news-content','.text-block','article']},
]

def publisher_for(url):
    host=(urlsplit(url).hostname or '').lower().removeprefix('www.')
    return next((p for p in PUBLISHERS if host in p['hosts']),None)

def article_url(url):
    from .security import canonical_url
    url=canonical_url(url)
    p=urlsplit(url)
    if p.hostname in ('lenta.ru','www.lenta.ru') and p.path.startswith('/amp/'):
        url=urlunsplit(('https','lenta.ru',p.path[4:],p.query,''))
    return url

def extract_article(raw,url,fallback=None):
    """Keep the article body rather than the site's navigation and related stories."""
    import json
    fallback=fallback or {}; soup=BeautifulSoup(raw,'html.parser'); structured=[]
    for el in soup.select('script[type="application/ld+json"]'):
        try:
            value=json.loads(el.string or el.get_text())
            structured.extend(value if isinstance(value,list) else value.get('@graph',[value]))
        except (ValueError,AttributeError,TypeError):continue
    item=next((v for v in structured if isinstance(v,dict) and any(t in ('NewsArticle','Article','Report','AnalysisNewsArticle') for t in (v.get('@type',[]) if isinstance(v.get('@type'),list) else [v.get('@type')]))),{})
    og=soup.select_one('meta[property="og:title"]');h1=soup.find('h1')
    title=item.get('headline') or (og.get('content') if og else None) or (h1.get_text(' ',strip=True) if h1 else None) or fallback.get('title') or (soup.title.get_text(' ',strip=True) if soup.title else url)
    date_meta=soup.select_one('meta[property="article:published_time"],meta[itemprop="datePublished"],meta[name="date"]')
    time_el=soup.select_one('time[datetime]')
    date=normalize_date(item.get('datePublished')) or normalize_date(date_meta.get('content') if date_meta else None) or normalize_date(time_el.get('datetime') if time_el else None) or normalize_date(fallback.get('published_at'))
    updated=normalize_date(item.get('dateModified'))
    publisher=publisher_for(url)
    author=item.get('author') or fallback.get('author')
    if isinstance(author,list):author=', '.join(str(a.get('name','')) if isinstance(a,dict) else str(a) for a in author)
    if isinstance(author,dict):author=author.get('name')
    for el in soup(['script','style','nav','footer','header','aside','form']):el.decompose()
    for el in soup.select('[class*="related"],.topic-body__content-ad,.social-share,.article__quote-share,.article__block[data-type="article"],.article__block[data-type="related"]'):
        el.decompose()
    body=item.get('articleBody')
    method='json_ld'
    # Publisher body containers avoid descriptions masquerading as complete articles.
    for selector in (publisher or {}).get('selectors',[]):
        container=soup.select_one(selector)
        if container and len(container.get_text(' ',strip=True))>=100:
            body=container.get_text(' ',strip=True);method='publisher_html';break
    if not body:
        container=soup.find('article') or soup.find('main')
        if container:body=container.get_text(' ',strip=True);method='article_html'
    if not body and not publisher:
        body=soup.get_text(' ',strip=True);method='generic_html'
    text=plain_text(str(body or ''))
    if len(text)<50:raise DomainError('EMPTY_SOURCE','Не удалось извлечь текст статьи',422)
    name=item.get('publisher',{})
    if isinstance(name,dict):name=name.get('name')
    return {'title':plain_text(str(title))[:300],'text':text,'published_at':date,'publisher':(publisher or {}).get('name') or name or urlsplit(url).hostname,
            'extraction_method':method,'provenance':{'type':'external_article','author':str(author)[:300] if author else None,'modified_at':updated,'content_extent':'full_article'}}
