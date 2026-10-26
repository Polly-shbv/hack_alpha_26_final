import {useEffect,useRef,useState} from 'react';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {Link} from 'react-router-dom';
import {api,date,roles,Data,analysisLabel} from './api';

export const collectionQuery=()=>({queryKey:['collection'],queryFn:()=>api('/collection'),staleTime:2000,refetchInterval:(query:any)=>['queued','running'].includes(query.state.data?.batch?.status)||['queued','running'].includes(query.state.data?.cycle?.status)||Date.now()-new Date(query.state.data?.cycle?.ended_at||0).getTime()<300000?5000:false});

export function LiveUpdates({uid,role,notify}: {uid:string;role:string;notify:(message:string)=>void}){
 const client=useQueryClient();const q=useQuery(collectionQuery());const seen=useRef<string|null>(null);
 const token=q.data?.latest_analysis_at;
 useEffect(()=>{
  if(!token)return;
  if(seen.current&&seen.current!==token){
   for(const key of ['overview','news','history','comparison','graph','sources'])client.invalidateQueries({queryKey:[key]});
   if(q.data?.manual_only||q.data?.enabled)notify('Поступили обновлённые результаты анализа');
  }
  seen.current=token;
 },[token,client,notify,q.data?.enabled]);
 return null;
}

export function CollectionPanel({admin=false,notify,role='ceo'}: {admin?:boolean;notify:(message:string)=>void;role?:string}){
 const q=useQuery(collectionQuery());const client=useQueryClient();
 const refresh=async()=>{try{await api('/collection/refresh',{method:'POST'});await q.refetch();notify('Сбор свежих публикаций запущен')}catch(e:any){notify(e.message)}};
 const analyze=async()=>{try{await api('/analysis/batches',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({role})});await q.refetch();notify('Пакетный анализ трёх компаний запущен')}catch(e:any){notify(e.message)}};
 if(!q.data)return <section className="panel collection-panel"><h2>Свежие публикации</h2><p>{q.error?'Не удалось получить состояние сбора':'Загружаем состояние источников…'}</p></section>;
 const s=q.data;const busy=['queued','running'].includes(s.cycle?.status)||['queued','running'].includes(s.batch?.status);
 return <section className="panel collection-panel" aria-label="Сбор новостей">
  <div className="row between"><div><h2>Свежие публикации</h2><p>Обновление только по кнопке · один запрос на все компании</p></div><div className="actions"><button className="btn secondary" disabled={busy} onClick={refresh}>{busy?'Обрабатываем пакет…':'Собрать и проанализировать'}</button><button className="btn secondary" disabled={busy} onClick={analyze}>Анализировать загруженные новости</button></div></div>
  <p className="muted">Пакет: три компании, выбранная роль, до трёх приоритетных новостей на компанию. Неизменённые подтверждённые ответы берутся из кэша.</p><p className="muted">Получено публикаций: {s.articles}. Последний готовый анализ: {s.latest_analysis_at?date(s.latest_analysis_at):'ещё не подготовлен'}. Во время обработки проверяется только готовность результата; это не вызывает модель.</p>
  {s.batch&&<div className="notice batch-status" role="status"><strong>Пакетный анализ: {({queued:'в очереди',running:'выполняется',completed:'готов',completed_with_limits:'готов с ограничениями',failed:'ошибка',interrupted:'прерван',cancelled:'отменён'} as Record<string,string>)[s.batch.status]||s.batch.status}</strong><p>Компаний: {s.batch.payload.company_count??s.batch.payload.company_ids.length} · Новостей: {s.batch.payload.news_count??'—'} · Запросов к модели: {s.batch.payload.model_calls??0} из 1 · Ответов из кэша: {s.batch.payload.cache_hits??0}</p>{s.batch.payload.errors?.map((error:Data,n:number)=><p key={n}>{error.message}</p>)}</div>}
  {s.cycle&&<p role="status">Последний сбор: {({queued:'в очереди',running:'выполняется',completed:'завершён',partial:'завершён с ограничениями',interrupted:'прерван при перезапуске'} as Record<string,string>)[s.cycle.status]||s.cycle.status}{s.cycle.ended_at?' · '+date(s.cycle.ended_at):''}. Новых и изменённых материалов: {s.cycle.payload.changed}.</p>}
  <div className="collection-grid">{s.sources.map((source:Data)=><article key={source.id} className="collection-source">
   <h3><a href={source.homepage} target="_blank" rel="noreferrer">{source.name}</a></h3>
   <p>{source.enabled?'Сбор по кнопке':'Источник выключен'}</p>
   <p>Проверяли: {source.last_checked?date(source.last_checked):'ещё не проверяли'}</p>
   <p>Успешно: {source.last_success?date(source.last_success):'пока нет'}</p>
   <p>Обработано: {source.processed} из {source.found}</p>
   {source.last_error&&<p className="error-message">{source.last_error}</p>}
   {source.content_warning&&<p className="muted">{source.content_warning}</p>}
   {admin&&<button className="btn secondary" onClick={async()=>{try{await api('/collection/sources/'+source.id,{method:'PATCH',body:JSON.stringify({enabled:!source.enabled,poll_seconds:source.poll_seconds})});q.refetch()}catch(e:any){notify(e.message)}}}>{source.enabled?'Выключить':'Включить'} {source.name}</button>}
  </article>)}</div>
  {s.cycle?.payload.sources?.some((source:Data)=>source.items?.some((item:Data)=>item.error))&&<details><summary>Ошибки отдельных публикаций</summary>{s.cycle.payload.sources.flatMap((source:Data)=>(source.items||[]).filter((i:Data)=>i.error).map((item:Data)=><p key={item.url}><a href={item.url} target="_blank" rel="noreferrer">{source.name}</a>: {item.error.message}</p>))}</details>}
  {s.model&&<p className={s.model.status==='available'?'muted':'error-message'}>Модель: {s.model.status==='available'?'последний запрос выполнен успешно':s.model.message} · {date(s.model.at)}</p>}
  {s.bank&&<p className="muted">Материалы банка: {s.bank.documents??'—'} документов · проверка {date(s.bank.checked_at)}. {s.bank.message}</p>}
  {admin&&<BankWatches items={s.bank_watches||[]} notify={notify} reload={()=>q.refetch()}/>}
  <details><summary>Последние запуски компаний</summary>{s.targets.map((target:Data)=><p key={target.company_id+target.role}>{target.company_id} · {roles[target.role]} · {target.payload.run_id?<Link to={'/runs/'+target.payload.run_id}>Ход последнего анализа</Link>:'Ожидает публикаций'}{target.payload.error?' · '+target.payload.error:''}</p>)}</details>
 </section>;
}

export function AnalysisMethod({insight}: {insight:Data}){
 return <div className="analysis-method"><span className="tag">{analysisLabel(insight)}</span>
 {insight.warnings?.includes('external_context_not_authorized')&&<p className="muted">Передача профиля компании внешней модели не разрешена. Результат подготовлен локальными правилами.</p>}
 {insight.warnings?.includes('llm_unavailable_local_rules')&&!insight.warnings?.includes('external_context_not_authorized')&&<p className="muted">Подтверждённый ответ модели не получен. Этот результат подготовлен локальными правилами.</p>}
 {insight.source_metadata?.some((source:Data)=>source.content_extent==='feed_excerpt')&&<p className="muted">Источник предоставляет RSS-анонс. Полный текст статьи недоступен; вывод ограничен анонсом.</p>}
 </div>;
}

function BankWatches({items,notify,reload}:{items:Data[];notify:(message:string)=>void;reload:()=>void}){
 const [url,setUrl]=useState('');const [title,setTitle]=useState('');const [published,setPublished]=useState('');const [busy,setBusy]=useState(false);
 return <details><summary>Отслеживание новых PDF банка</summary><p className="muted">Можно добавить PDF или страницу со ссылками на PDF на официальном домене alfabank.ru. Документы без подтверждённой даты сохраняются, но не участвуют в датированном анализе.</p>
 {items.map(item=><div key={item.id}><a href={item.url} target="_blank" rel="noreferrer">{item.title}</a><p>Проверка: {date(item.last_checked)}{item.error?' · '+item.error:''}</p><button className="btn secondary" onClick={async()=>{try{await api('/collection/bank-watches/'+item.id,{method:'DELETE'});reload()}catch(e:any){notify(e.message)}}}>Удалить отслеживание {item.title}</button></div>)}
 <form className="stack" onSubmit={async e=>{e.preventDefault();setBusy(true);try{await api('/collection/bank-watches',{method:'POST',body:JSON.stringify({url,title,published_at:published?published+'T23:59:59+03:00':null})});setUrl('');setTitle('');setPublished('');reload();notify('Банковский адрес добавлен. Нажмите «Собрать и проанализировать»')}catch(error:any){notify(error.message)}finally{setBusy(false)}}}>
 <label>Адрес банковского PDF или страницы<input type="url" required maxLength={2000} value={url} onChange={e=>setUrl(e.target.value)} placeholder="https://alfabank.ru/…"/></label>
 <label>Название банковского материала<input required maxLength={300} value={title} onChange={e=>setTitle(e.target.value)}/></label>
 <label>Дата публикации для прямой ссылки на PDF<input type="date" value={published} onChange={e=>setPublished(e.target.value)}/></label>
 <button className="btn secondary" disabled={busy||!url||!title}>{busy?'Сохраняем…':'Добавить банковский адрес'}</button></form></details>;
}

export function OriginalLinks({insight}:{insight:Data}){
 const sources=(insight.source_metadata||[]).filter((source:Data)=>/^https?:\/\//.test(source.origin_url||''));
 const distinct=Array.from(new Map(sources.map((source:Data)=>[source.origin_url,source])).values()) as Data[];
 if(!distinct.length)return null;
 return <div className="original-links"><span className="muted">Оригинал новости: </span>{distinct.map(source=><a key={source.origin_url} href={source.origin_url} target="_blank" rel="noopener noreferrer">{source.publisher||'Открыть на сайте источника'} ↗</a>)}</div>;
}
