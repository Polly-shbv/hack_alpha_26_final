import {test,expect} from '@playwright/test';

test('E16: новая публикация → возможность → реальная банковская цитата → экспорт',async({page})=>{
 const title='Учебное новое снижение ставки · проверка общего механизма';
 const text='Ключевая ставка снижена по итогам нового заседания. В публикации описано снижение стоимости новых кредитов; условия действующих договоров зависят от формулы пересмотра. Учебный сценарий не сообщает о фактических платежах конкретной компании и не подтверждает условия её обязательств.';
 let sid='';
 await page.goto('/select-company');
 const me=await (await page.request.get('/api/v1/me')).json();
 const headers={'X-CSRF-Token':me.csrf_token};
 try{
  await page.request.patch('/api/v1/me/preferences',{headers,data:{role:'cfo',company_id:'U_9250'}});
  await page.goto('/sources/import?tab=text');
  await page.getByLabel('Заголовок',{exact:true}).fill(title);
  await page.getByLabel('Текст материала',{exact:true}).fill(text);
  await page.getByLabel('Дата публикации (если не определится автоматически)').fill('2026-10-22');
  await page.getByRole('button',{name:'Сохранить материал',exact:true}).click();
  await expect(page.locator('.job-panel')).toContainText('Готово');
  const jid=new URL(page.url()).searchParams.get('job');
  const job=await (await page.request.get('/api/v1/imports/'+jid)).json();sid=job.payload.id;
  await page.request.patch('/api/v1/sources/'+sid,{headers,data:{active:true}});
  await page.getByRole('button',{name:'Открыть материал',exact:true}).click();
  const source=page.getByRole('dialog');
  await source.getByText('Классификация и ключевая информация',{exact:true}).click();
  await expect(source).toContainText('Ключевая ставка снижена');
  await page.keyboard.press('Escape');
  await page.goto('/companies/U_9250/news');
  await page.getByRole('button',{name:'Обновить анализ',exact:true}).click();
  await expect(page.getByRole('status')).toContainText('Анализ обновлён');
  await page.getByRole('button',{name:'Закрыть уведомление',exact:true}).click();
  const card=page.locator('.event-card').filter({has:page.getByRole('heading',{name:title,exact:true})});
  await expect(card).toContainText('Возможность');
  await card.getByRole('link',{name:'Подробнее',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Возможности',exact:true})).toBeVisible();
  await expect(page.locator('.ai-tile')).toContainText('снижение ставки может уменьшить');
  await expect(page.locator('.ai-tile')).not.toContainText('Недостаточно оснований для выделения возможностей');
  await expect(page.getByRole('button',{name:'Открыть основание цитаты'}).first()).toBeVisible();
  await expect(page.locator('.bank-tile')).toContainText('Ключевая ставка ЦБ РФ');
  const iid=new URL(page.url()).pathname.split('/').at(-1);
  const exported=await (await page.request.get('/api/v1/insights/'+iid+'/export')).text();
  expect(exported).toContain('Возможность:');expect(exported).not.toContain('Недостаточно оснований для выделения возможностей');
  await page.screenshot({path:'docs/screenshots/middle-insight-1440.png',fullPage:true});
  await page.getByRole('button',{name:'Открыть основание цитаты'}).first().click();
  await expect(page.getByRole('dialog')).toContainText('Материал из подтверждённого корпуса');
  await expect(page.getByRole('dialog')).toContainText('OCR');
  await page.keyboard.press('Escape');
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>{(document.activeElement as HTMLElement)?.blur();window.scrollTo(0,0)});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:'docs/screenshots/middle-insight-390.png',fullPage:true});
 }finally{
  if(sid)await page.request.patch('/api/v1/sources/'+sid,{headers,data:{active:false}});
 }
});

test('E17: сбор из RSS/Atom доступен в интерфейсе с явными ограничениями',async({page})=>{
 await page.goto('/sources/import?tab=feed');
 await expect(page.getByRole('button',{name:'RSS / Atom',exact:true})).toHaveClass('active');
 await expect(page.getByRole('button',{name:'Собрать новости',exact:true})).toBeDisabled();
 await page.getByLabel('URL RSS/Atom',{exact:true}).fill('https://example.org/feed.xml');
 await expect(page.getByRole('button',{name:'Собрать новости',exact:true})).toBeEnabled();
 await expect(page.locator('.import-form')).toContainText('до 5 статей');
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await page.screenshot({path:'docs/screenshots/rss-import-390.png',fullPage:true});
});
