import React from 'react';
import {Data,bankEmpty,date} from './api';

export function SourceMetadata({source}:{source:Data}){
 const content=source.extraction;
 const labels:Record<string,string>={rate:'Ставка и финансирование',copper:'Медь',gold:'Золото',china:'Внешняя торговля с Китаем',conflict:'Противоречивые сведения',fx:'Валюты',logistics:'Логистика',energy:'Энергия и топливо',inflation:'Инфляция',labor:'Рынок труда',demand:'Потребление и спрос',regulation:'Регулирование',construction:'Строительство',electrical:'Электрооборудование',steel:'Сталь и металлы'};
 return <>
  {source.provenance?.content_extent==='feed_excerpt'&&<p className="notice">Доступен только анонс RSS. Полный текст статьи не получен.</p>}
  {source.provenance?.author&&<p>Автор: {source.provenance.author}</p>}
  {source.provenance?.version&&<p>Версия публикации: {source.provenance.version}</p>}
  {source.ocr&&<p className="notice">Текст извлечён локальным OCR. Числа и цитаты следует сверить с оригинальной страницей.</p>}
  {source.provenance?.date_precision==='day'&&<p className="muted">В документе указана дата с точностью до дня; точное время публикации неизвестно.</p>}
  {source.provenance?.reporting_period&&<p>Отчётный период: {source.provenance.reporting_period}. Дата публикации не установлена.</p>}
  {source.analysis_eligible===false&&<p className="notice">Материал сохранён, но не участвует в анализе: проверьте дату публикации и наличие извлечённого текста.</p>}
  {content&&<details><summary>Классификация и ключевая информация</summary><p>{content.summary}</p><p>Темы: {content.topics.map((topic:string)=>labels[topic]||topic).join(', ')||'Не определены'}</p><p>Страны: {content.countries.join(', ')||'Не определены'}</p><p>Числа из источника: {content.numbers.map((n:Data)=>n.value).join('; ')||'Нет'}</p><p className="muted">Классификация по словарям · {content.version}. Совпадение темы не доказывает влияние на компанию.</p></details>}
 </>;
}

export function Consequences({items=[]}:{items:Data[]}){
 const group=(kind:string)=>items.filter(item=>item.kind===kind);
 const render=(kind:string)=>group(kind).length?group(kind).map(item=><div key={item.claim_id}><p>{item.text}</p>{item.conditions?.length>0&&<details><summary>Условия применимости</summary><ul>{item.conditions.map((condition:string)=><li key={condition}>{condition}</li>)}</ul></details>}{item.horizon&&<p className="muted">Горизонт: {item.horizon}</p>}</div>):<p>{kind==='risk'?'Недостаточно оснований для выделения рисков':'Недостаточно оснований для выделения возможностей'}</p>;
 return <><h3>Риски</h3>{render('risk')}<h3>Возможности</h3>{render('opportunity')}<p className="muted">{items.some(item=>item.horizon)?'Горизонт указан для отдельных сценариев':'Горизонт не определён'}</p></>;
}

export function BankView({insight,openSource}:{insight:Data;openSource:(id:string)=>void}){
 const claims=insight.bank_view?.claims||[];
 if(!claims.length)return <p>{bankEmpty}</p>;
 return <>
  <p className="muted">Выдержки из материалов банка. Они не являются оценкой банком конкретной компании.</p>
  {insight.bank_evidence?.some((chunk:Data)=>chunk.ocr_warning)&&<p className="notice">Цитаты распознаны OCR. Числа и формулировки следует сверить с оригинальной страницей.</p>}
  {claims.map((claim:Data)=>{
   const chunk=insight.bank_evidence?.find((item:Data)=>claim.evidence_ids.includes(item.id));
   const sid=claim.evidence_ids[0]?.split('#')[0];
   return <div key={claim.claim_id}>
    <strong>{claim.kind==='expert_view'?'Мнение из отчёта':'Контекст из материала'}</strong>
    <p>«{claim.text}»</p>
    {chunk&&<p className="muted">{chunk.title} · {date(chunk.published_at).split(',')[0]}{chunk.page_number?' · стр. '+chunk.page_number:''}</p>}
    <button className="text-btn" onClick={()=>openSource(sid)}>Открыть основание цитаты</button>
   </div>;
  })}
 </>;
}
