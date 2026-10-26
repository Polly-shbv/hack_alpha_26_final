export type Data = Record<string, any>;
let csrf='';
export async function api<T=Data>(path:string,init:RequestInit={}):Promise<T>{
 const headers=new Headers(init.headers);if(!(init.body instanceof FormData)&&init.body)headers.set('Content-Type','application/json');if(init.method&&init.method!=='GET')headers.set('X-CSRF-Token',csrf);
 const r=await fetch('/api/v1'+path,{...init,headers,credentials:'same-origin'});let data;try{data=await r.json()}catch{throw new Error('Сервис временно недоступен')};if(!r.ok)throw new Error(data.error?.message||'Не удалось выполнить действие');if(path==='/me')csrf=data.csrf_token;return data;
}
export const roles:Record<string,string>={ceo:'Генеральный директор',cfo:'Финансовый директор',treasury:'Казначейство'};
export const date=(v?:string)=>v?new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:'Europe/Moscow'}).format(new Date(v)):'Дата не указана';
export const money=(v?:string)=>v?new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(Number(v)/1e9)+' млрд ₽':'Нет данных';
export const flag=(v:boolean|null)=>v===true?'Отмечен':v===false?'Не отмечен':'Нет данных';
export const eventWord=(n:number)=>n%100>=11&&n%100<=14?'событий':n%10===1?'событие':n%10>=2&&n%10<=4?'события':'событий';
export const bankEmpty='В загруженных материалах не найдена подтверждённая позиция банка по этому событию';
export const assistantText='AI-помощник появится в следующей версии. Сейчас вы можете изучить сводку, события и основания выводов';
export const todayMoscow=()=>new Intl.DateTimeFormat('en-CA',{year:'numeric',month:'2-digit',day:'2-digit',timeZone:'Europe/Moscow'}).format(new Date());
export const sourceDate=(source:Data)=>source.provenance?.date_precision==='day'?date(source.published_at).split(',')[0]:date(source.published_at);

export const analysisLabel=(i:Data)=>{const method=i.analysis_method||'';return /gemma|пакетный запрос/i.test(method)?(/кэш/i.test(method)?'AI-анализ · сохранённый результат':'AI-анализ'):method||(i.mode==='live'?'AI-анализ':'Локальные правила')};
