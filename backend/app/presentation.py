"""User-facing wording; persisted model output and audit provenance stay intact."""
from copy import deepcopy
import re


def present_insight(value):
    result=deepcopy(value)
    method=result.get('analysis_method','')
    if re.search(r'gemma|пакетный запрос',method,re.I):
        result['analysis_method']='AI-анализ · сохранённый результат' if 'кэш' in method.lower() else 'AI-анализ'
    for link in result.get('company_links',[]):
        text=link['text']
        if re.search(r'(?:может\s+)?соответств\w*.{0,45}профил\w*\s+деятельност',text,re.I):
            replacement=('Новость касается строительных проектов. Для оценки влияния нужны данные о текущих объектах и регионах работы компании.'
                         if result.get('topic')=='construction' else
                         'Для оценки влияния новости нужны данные о текущих проектах и действующих контрактах компании.')
            link['text']=replacement
            result['summary']=result.get('summary','').replace(text,replacement)
    return result
