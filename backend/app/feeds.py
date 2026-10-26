"""Bounded RSS/Atom collection using the same DNS-pinned fetcher as URL import."""
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin
from .content import normalize_date
from .errors import DomainError
from .ingestion import add_document, import_url, plain_text
from .security import fetch_public, public_target


def local_name(tag):
    return tag.rsplit('}', 1)[-1].lower()


def parse_feed(raw, base_url):
    if len(raw) > 2 * 1024 * 1024:
        raise DomainError('FEED_TOO_LARGE', 'RSS/Atom превышает 2 МБ', 413)
    if b'\x00' in raw or b'<!doctype' in raw.lower() or b'<!entity' in raw.lower():
        raise DomainError('UNSAFE_FEED', 'DTD и XML entities в RSS/Atom не поддерживаются', 422)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise DomainError('INVALID_FEED', 'Не удалось прочитать RSS/Atom', 422)
    if local_name(root.tag) not in ('rss', 'feed', 'rdf'):
        raise DomainError('INVALID_FEED', 'Источник не является RSS/Atom', 422)
    items = []
    for node in root.iter():
        if local_name(node.tag) not in ('item', 'entry'):
            continue
        fields = {}
        for child in node:
            name = local_name(child.tag)
            if name == 'link':
                if child.get('rel', 'alternate') == 'alternate' and child.get('type', 'text/html') in ('text/html', 'application/xhtml+xml'):
                    fields['url'] = child.get('href') or (child.text or '').strip()
            elif name in ('title', 'pubdate', 'published', 'updated', 'encoded', 'content', 'description', 'summary', 'author', 'category'):
                fields[name] = ''.join(child.itertext()).strip()
        href = fields.get('url')
        if not href:
            continue
        items.append({'url': urljoin(base_url, href), 'title': plain_text(fields.get('title', ''))[:300],
                      'published_at': normalize_date(fields.get('pubdate') or fields.get('published')),
                      'content': plain_text(fields.get('encoded') or fields.get('content') or ''),
                      'description':plain_text(fields.get('description') or fields.get('summary') or ''),
                      'author':fields.get('author'),'category':fields.get('category'),'modified_at':normalize_date(fields.get('updated'))})
        if len(items) >= 100:
            break
    unique = {item['url']: item for item in items}
    return list(unique.values())


def import_feed(url, mode='fixture', owner='demo', max_items=5):
    started = time.monotonic()
    raw, _, final_url = fetch_public(url)
    entries = parse_feed(raw, final_url)
    results = []
    for entry in entries[:max_items]:
        if time.monotonic() - started > 45:
            results.append({'url': entry['url'], 'status': 'skipped', 'reason': 'Лимит времени сбора; повторите импорт'})
            continue
        try:
            public_target(entry['url'])
            metadata = {'mode': mode, 'owner': owner, 'synthetic': False, 'kind': 'news', 'published_at': entry['published_at']}
            if len(entry['content']) >= 200:
                from urllib.parse import urlsplit
                metadata['publisher']=urlsplit(entry['url']).hostname
                result = add_document(entry['title'] or entry['url'], entry['content'], origin_url=entry['url'], **metadata)
                method = 'feed_full_content'
            else:
                result = import_url(entry['url'], **metadata)
                method = 'article_page'
            results.append({**result, 'url': entry['url'], 'method': method})
        except DomainError as error:
            results.append({'url': entry['url'], 'status': 'failed', 'error': {'code': error.code, 'message': error.message}})
    return {'feed_url': final_url, 'found': len(entries), 'items': results,
            'processed': sum(item['status'] not in ('failed', 'skipped') for item in results),
            'remaining': max(0, len(entries) - max_items), 'elapsed_ms': round((time.monotonic() - started) * 1000)}
