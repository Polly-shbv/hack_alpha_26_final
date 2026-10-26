"""Conservative grouping and access/time-scoped event projection."""
import json
import re
from datetime import datetime
from .content import extract_content, normalized
from .db import dumps


def overlap(a, b):
    a, b = set(normalized(a).split()), set(normalized(b).split())
    return len(a & b) / max(1, len(a | b))


def store_event(connection, document, mode, owner):
    extraction = document['extraction']
    event = {'id': 'event-' + document['id'], 'title': document['title'], 'fact': extraction['summary'],
             'published_at': document['published_at'], 'topic': extraction['topic'], 'extraction': extraction,
             'is_synthetic': document['is_synthetic'], 'source_document_ids': [document['id']], 'owner_scope': owner}
    if document['published_at']:
        for row in connection.execute('SELECT payload FROM events WHERE mode=?', (mode,)):
            old = json.loads(row[0])
            if old.get('owner_scope') != owner or old.get('is_synthetic') != event['is_synthetic'] or not old.get('published_at'):
                continue
            if datetime.fromisoformat(old['published_at']).date() != datetime.fromisoformat(event['published_at']).date():
                continue
            if overlap(old['title'], event['title']) < .82 or overlap(old['fact'], event['fact']) < .78:
                continue
            old_numbers = re.findall(r'\d+(?:[.,]\d+)?', old['title'] + ' ' + old['fact'])
            new_numbers = re.findall(r'\d+(?:[.,]\d+)?', event['title'] + ' ' + event['fact'])
            if old_numbers != new_numbers and normalized(old['title']) != normalized(event['title']):
                continue
            old['source_document_ids'] = list(dict.fromkeys(old['source_document_ids'] + [document['id']]))
            if old_numbers != new_numbers:
                old['conflicts'] = ['Сообщения об одном событии содержат разные числа; нужна проверка первоисточника']
            connection.execute('UPDATE events SET payload=? WHERE id=?', (dumps(old), old['id']))
            return old['id']
    connection.execute('INSERT INTO events VALUES(?,?,?)', (event['id'], dumps(event), mode))
    return event['id']


def select_events(connection, mode, owner, as_of):
    cutoff = datetime.fromisoformat(as_of)
    sources = {}
    for row in connection.execute('SELECT id,payload FROM sources WHERE active=1 AND mode=? AND owner IN (?,?)', (mode, 'public', owner)):
        document = json.loads(row['payload'])
        if document.get('published_at') and datetime.fromisoformat(document['published_at']) <= cutoff:
            sources[row['id']] = document
    events = []
    for row in connection.execute('SELECT payload FROM events WHERE mode=?', (mode,)):
        event = json.loads(row[0])
        available = [sid for sid in event.get('source_document_ids', []) if sid in sources]
        if not available:
            continue
        documents = [sources[sid] for sid in available]
        event['extraction']=extract_content(documents[0]['title'],documents[0]['text'])
        if 'owner_scope' in event:
            event['title']=documents[0]['title']
            event['topic']=event['extraction']['topic']
        event['source_document_ids'] = available
        event['source_metadata']=[{**{key:doc.get(key) for key in ('id','title','publisher','origin_url','published_at','extraction_method','is_synthetic')},'content_extent':doc.get('provenance',{}).get('content_extent'),'version':doc.get('provenance',{}).get('version',1)} for doc in documents]
        event['published_at'] = documents[0]['published_at']
        if not any(' '.join(event['fact'].split()) in ' '.join(doc['text'].split()) for doc in documents):
            event['fact'] = documents[0].get('extraction', {}).get('summary') or extract_content(documents[0]['title'], documents[0]['text'])['summary']
        event.pop('conflicts', None)
        numbers = {tuple(re.findall(r'\d+(?:[.,]\d+)?', doc.get('extraction', {}).get('summary', doc['text'][:1200]))) for doc in documents}
        if len(numbers) > 1:
            event['conflicts'] = ['Разные числовые значения в доступных сообщениях об одном событии']
        events.append(event)
    return events
