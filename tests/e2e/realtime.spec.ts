import {test,expect} from '@playwright/test';

test('E18: состояние трёх источников, ручное обновление и честное ограничение ТАСС',async({page})=>{
 await page.goto('/select-company');
 const state=await (await page.request.get('/api/v1/collection')).json();
 state.cycle=null;state.enabled=false;
 await page.route('**/api/v1/collection',route=>route.fulfill({json:state}));
 await page.route('**/api/v1/collection/refresh',async route=>{
  state.cycle={id:'synthetic-browser-cycle',status:'partial',ended_at:new Date().toISOString(),payload:{changed:3,sources:[]}};
  for(const source of state.sources){source.found=10;source.processed=1;source.last_checked=new Date().toISOString();source.last_success=source.last_checked}
  state.sources.find((s:any)=>s.id==='tass').content_warning='Часть материалов доступна только как RSS-анонс';
  await route.fulfill({status:202,json:{cycle_id:state.cycle.id,status:'queued'}});
 });
 await page.goto('/sources');const panel=page.getByRole('region',{name:'Сбор новостей'});
 await expect(panel.getByRole('link',{name:'Лента.ру',exact:true})).toBeVisible();
 await expect(panel.getByRole('link',{name:'РИА Новости',exact:true})).toBeVisible();
 await expect(panel.getByRole('link',{name:'ТАСС',exact:true})).toBeVisible();
 await panel.getByRole('button',{name:'Собрать и проанализировать',exact:true}).click();
 await expect(panel).toContainText('завершён с ограничениями');
 await expect(panel).toContainText('RSS-анонс');
 await page.getByRole('button',{name:'Закрыть уведомление',exact:true}).click();
 await page.screenshot({path:'docs/screenshots/realtime-sources-1440.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await page.screenshot({path:'docs/screenshots/realtime-sources-390.png',fullPage:true});
});

test('E19: готовая новая сводка появляется без перезагрузки страницы',async({page})=>{
 await page.goto('/select-company');const me=await (await page.request.get('/api/v1/me')).json();const headers={'X-CSRF-Token':me.csrf_token};
 const collection=await (await page.request.get('/api/v1/collection')).json();
 collection.enabled=false;collection.batch={status:'running',payload:{company_ids:['U_9250']}};
 await page.route('**/api/v1/collection',route=>route.fulfill({json:collection}));
 const run=async()=>{
  const r=await (await page.request.post('/api/v1/companies/U_9250/runs',{headers,data:{role:'ceo',mode:'fixture',as_of:new Date().toISOString()}})).json();
  await expect.poll(async()=> (await (await page.request.get('/api/v1/runs/'+r.run_id)).json()).status,{timeout:10000}).toMatch(/^completed/);
  return (await page.request.get('/api/v1/runs/'+r.run_id)).json();
 };
 let sid='';
 try{
  await page.request.patch('/api/v1/me/preferences',{headers,data:{role:'ceo',company_id:'U_9250'}});
  const first=await run();collection.latest_analysis_at=first.ended_at;
  await page.goto('/companies/U_9250/overview');
  const title='Учебное новое снижение ключевой ставки · автообновление';
  const imported=await (await page.request.post('/api/v1/imports/text',{headers,data:{title,text:'Ключевая ставка снижена по итогам учебного заседания. Новые кредиты могут стать дешевле, если банки пересмотрят условия. Влияние на действующие договоры зависит от формулы ставки и сроков пересмотра. Это синтетическая публикация для проверки автоматического обновления интерфейса. Идентификатор теста: '+crypto.randomUUID(),published_at:new Date().toISOString(),kind:'news',mode:'fixture'}})).json();
  await expect.poll(async()=>(await (await page.request.get('/api/v1/imports/'+imported.job_id)).json()).status).toBe('ready');
  sid=(await (await page.request.get('/api/v1/imports/'+imported.job_id)).json()).payload.id;
  expect((await (await page.request.get('/api/v1/sources/'+sid)).json()).active).toBe(true);
  const second=await run();collection.latest_analysis_at=second.ended_at;
  await expect(page.getByRole('link',{name:title,exact:true})).toBeVisible({timeout:10000});
  await expect(page.getByText('Поступили обновлённые результаты анализа',{exact:true})).toBeVisible();
 }finally{
  if(sid)await page.request.patch('/api/v1/sources/'+sid,{headers,data:{active:false}});
  await page.request.patch('/api/v1/me/preferences',{headers,data:{role:me.preferences.role,company_id:me.preferences.company_id||'U_9250'}});
 }
});
