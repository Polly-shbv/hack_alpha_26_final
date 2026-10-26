import {test,expect} from '@playwright/test';

test('E20: анализ пакета запускается только кнопкой, повторный клик блокируется',async({page})=>{
 await page.goto('/select-company');const state=await (await page.request.get('/api/v1/collection')).json();state.enabled=false;state.cycle=null;state.batch=null;
 let submissions=0;
 await page.route('**/api/v1/collection',route=>route.fulfill({json:state}));
 await page.route('**/api/v1/analysis/batches',async route=>{
  submissions++;state.batch={id:'synthetic-browser-batch',status:'running',payload:{company_ids:['U_9250','U_1941','U_106783'],news_count:3,model_calls:1,cache_hits:0,errors:[]}};
  await route.fulfill({status:202,json:{batch_id:state.batch.id,run_ids:{}}});
 });
 await page.goto('/sources');const panel=page.getByRole('region',{name:'Сбор новостей'});
 await expect(panel).toContainText('Обновление только по кнопке');
 await expect(page.getByRole('button',{name:'Включить автообновление'})).toHaveCount(0);
 expect(submissions).toBe(0);
 await panel.getByRole('button',{name:'Анализировать загруженные новости',exact:true}).click();
 await expect(panel.getByRole('button',{name:'Анализировать загруженные новости',exact:true})).toBeDisabled();
 await expect(panel).toContainText('Компаний: 3');await expect(panel).toContainText('Запросов к модели: 1 из 1');
 await expect.poll(()=>submissions,{timeout:6000}).toBe(1);
 await page.waitForTimeout(5500);expect(submissions).toBe(1);
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await page.screenshot({path:'docs/screenshots/manual-batch-390.png',fullPage:true});
});

test('E21: прямая ссылка на оригинал есть в карточке и разборе',async({page})=>{
 await page.goto('/select-company');
 await expect(page.getByRole('heading',{name:'Альфа Контекст',exact:true})).toBeVisible();
 const meResponse=await page.request.get('/api/v1/me');
 expect(meResponse.status(),await meResponse.text()).toBe(200);
 const me=await meResponse.json();const headers={'X-CSRF-Token':me.csrf_token};
 const response=await page.request.post('/api/v1/companies/U_9250/runs',{headers,data:{role:'ceo',mode:'fixture'}});
 expect(response.status(),await response.text()).toBe(202);
 const r=await response.json();expect(r.run_id).toBeTruthy();
 await expect.poll(async()=>(await (await page.request.get('/api/v1/runs/'+r.run_id)).json()).status).toMatch(/^completed/);
 const overview=await (await page.request.get('/api/v1/companies/U_9250/overview?role=ceo')).json();
 const item=overview.items[0];const url='https://lenta.ru/news/2026/10/26/synthetic-link-test/';
 item.source_metadata=[{id:item.source_document_ids[0],publisher:'Лента.ру',origin_url:url,content_extent:'full_article'}];
 await page.request.patch('/api/v1/me/preferences',{headers,data:{role:'ceo',company_id:'U_9250'}});
 await page.route('**/api/v1/companies/U_9250/overview?**',route=>route.fulfill({json:overview}));
 await page.route('**/api/v1/companies/U_9250/insights/'+item.id,route=>route.fulfill({json:item}));
 await page.goto('/companies/U_9250/overview');
 const link=page.locator('.event-card').filter({has:page.getByRole('link',{name:item.title,exact:true})}).locator('.original-links a');
 await expect(link).toHaveAttribute('href',url);await expect(link).toHaveAttribute('target','_blank');
 await page.goto('/companies/U_9250/insights/'+item.id);
 await expect(page.locator('.original-links a')).toHaveAttribute('href',url);
 await expect(page.locator('.original-links a')).toContainText('Лента.ру');
});

test('E22: в карточке и разборе нет технического имени модели',async({page})=>{
 await page.goto('/select-company');
 await expect(page.getByRole('heading',{name:'Альфа Контекст',exact:true})).toBeVisible();
 const meResponse=await page.request.get('/api/v1/me');
 expect(meResponse.status(),await meResponse.text()).toBe(200);
 const me=await meResponse.json();const headers={'X-CSRF-Token':me.csrf_token};
 const response=await page.request.post('/api/v1/companies/U_9250/runs',{headers,data:{role:'ceo',mode:'fixture'}});
 expect(response.status(),await response.text()).toBe(202);
 const run=await response.json();expect(run.run_id).toBeTruthy();
 await expect.poll(async()=>(await (await page.request.get('/api/v1/runs/'+run.run_id)).json()).status).toMatch(/^completed/);
 const overview=await (await page.request.get('/api/v1/companies/U_9250/overview?role=ceo')).json();const item=overview.items[0];
 item.analysis_method='Gemma 4 · пакетный запрос';
 await page.request.patch('/api/v1/me/preferences',{headers,data:{role:'ceo',company_id:'U_9250'}});
 await page.route('**/api/v1/companies/U_9250/overview?**',route=>route.fulfill({json:overview}));
 await page.route('**/api/v1/companies/U_9250/insights/'+item.id,route=>route.fulfill({json:item}));
 for(const path of ['/companies/U_9250/overview','/companies/U_9250/insights/'+item.id,'/sources','/settings']){
  await page.goto(path);await expect(page.locator('h1')).toBeVisible();await expect(page.locator('body')).not.toContainText(/Gemma/i);
 }
 await page.goto('/companies/U_9250/insights/'+item.id);await expect(page.locator('.analysis-method')).toContainText('AI-анализ');
});
