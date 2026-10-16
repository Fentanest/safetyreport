import {test,expect,Page} from '@playwright/test';
const laws=['도로교통법 제32조','도로교통법 제33조','도로교통법 제34조'];
async function login(page:Page){await page.goto('/login');await page.locator('#username').fill('fixture-admin');await page.locator('#password').fill('fixture-pass-1234');await Promise.all([page.waitForURL('**/'),page.getByRole('button',{name:'로그인',exact:true}).click()]);}
async function count(page:Page){return page.evaluate(()=>{const w=window as any;const el=document.querySelector('table.dataTable[id]');return el && w.jQuery.fn.dataTable.isDataTable(el) ? w.jQuery(el).DataTable().rows({search:'applied'}).count() : -1;});}
test('second/third citation URL and interactive search with article boundaries',async({page})=>{
 await login(page);
 for(const law of laws.slice(1)){await page.goto('/data/all?law='+encodeURIComponent(law));await expect(page.locator('#allTable a.report-detail-link')).toHaveCount(1);await expect.poll(()=>count(page)).toBe(1);}
 await page.goto('/data/all');await page.locator('.floating-search-btn').click();await expect(page.locator('#offcanvasSearch')).toBeVisible();
 await page.locator('#searchLaw').fill('제34조');await page.locator('#btnSearch').click();await expect.poll(()=>count(page)).toBe(1);
 await page.locator('#searchLaw').fill('도로교통법 제17조');await page.locator('#btnSearch').click();await expect.poll(()=>count(page)).toBe(2);
});
test('detail has separate law links and layouts in both themes',async({page},info)=>{
 await login(page);
 for(const theme of ['light','dark']){
  await page.setViewportSize({width:1440,height:1000});await page.goto('/data/all?law='+encodeURIComponent(laws[1]));await page.locator(`[data-sr-theme="${theme}"]`).click();
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:1000});await page.locator('#allTable a.report-detail-link').first().click();
   await expect(page.locator('#reportDetailModal')).toBeVisible();
   for(const law of laws){const link=page.locator('#rdFields').getByRole('link',{name:new RegExp('^'+law+'(?: |$)')});await expect(link).toHaveCount(1);expect(new URL(await link.getAttribute('href')||'','http://localhost').searchParams.get('law')).toBe(law);}
   await page.locator('#rdFields').getByRole('link',{name:new RegExp('^'+laws[2]+'(?: |$)')}).scrollIntoViewIfNeeded();
   await page.screenshot({path:info.outputPath(`laws-${theme}-${width}.png`)});
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+2)).toBeTruthy();
   await page.locator('#reportDetailModal .btn-close').click();await expect(page.locator('#reportDetailModal')).not.toBeVisible();
  }
 }
 await page.setViewportSize({width:1440,height:1000});await page.locator('#allTable a.report-detail-link').first().click();await page.locator('#rdFields').getByRole('link',{name:new RegExp('^'+laws[2]+'(?: |$)')}).click();
 await expect(page).toHaveURL(/law=/);expect(new URL(page.url()).searchParams.get('law')).toBe(laws[2]);await expect.poll(()=>count(page)).toBe(1);
});
test('stats choices are individual and unrelated edit preserves LF field',async({page})=>{
 await login(page);await page.goto('/stats?category=traffic');await page.locator('#statsLawToggle').click();
 for(const law of laws){await expect(page.locator('#statsLawSidebar').getByRole('button',{name:new RegExp('^'+law+'(?: |$)')})).toHaveCount(1);}
 const labels=await page.locator('#statsLawSidebar button').allTextContents();expect(labels.every(s=>!s.includes('\n'))).toBeTruthy();
 await page.goto('/db-editor/traffic/90000001');await expect(page.locator('textarea[name="위반법규"]')).toHaveValue(laws.join('\n'));
 await page.locator('input[name="담당자"]').fill('합성 편집 검증');await page.locator('button[type="submit"]').click();await expect(page).toHaveURL(/\/db-editor\?category=traffic/);
 await page.goto('/db-editor/traffic/90000001');await expect(page.locator('textarea[name="위반법규"]')).toHaveValue(laws.join('\n'));
});

test('CSV keeps multiple laws in one quoted field',async({page})=>{
 await login(page);await page.goto('/data/all?law='+encodeURIComponent(laws[1]));await expect.poll(()=>count(page)).toBe(1);
 const pending=page.waitForEvent('download');await page.locator('.btn-export-excel').first().click();const download=await pending;
 const stream=await download.createReadStream();expect(stream).not.toBeNull();const chunks:Buffer[]=[];for await(const chunk of stream!){chunks.push(Buffer.from(chunk));}
 const csv=Buffer.concat(chunks).toString('utf8');expect(csv).toContain('"'+laws.join('\n')+'"');
});

// 2026-10-16: 위반법규 막대 건수 == 막대 링크(lawExact=true) 목록 건수. 법규 필터가 걸린 통계에서도 같다.
async function barCounts(page:Page){
 return page.locator('#statsTypeList li').evaluateAll(items=>items.map(li=>({href:(li.querySelector('a') as HTMLAnchorElement|null)?.getAttribute('href')||'',
  name:(li.querySelector('.sr-hbar-label')?.textContent||'').trim(),count:Number((li.querySelector('.sr-hbar-value')?.textContent||'').match(/[\d,]+/)![0].replace(/,/g,''))})));
}
for(const statsQuery of ['?dedupe=raw&year=all','?dedupe=raw&year=all&law='+encodeURIComponent('도로교통법 제32조')]){
 test('every law bar opens a list with the same count '+statsQuery,async({page})=>{
  await login(page);await page.goto('/stats'+statsQuery);
  await expect(page.locator('#statsTypeList li .sr-hbar-value').first()).toBeVisible();
  const more=page.locator('#statsTypeMore');if(await more.isVisible()){await more.click();await expect(more).toHaveAttribute('aria-expanded','true');}
  const bars=await barCounts(page);expect(bars.length).toBeGreaterThan(0);
  if(!statsQuery.includes('law=')){
   const names=bars.map(b=>b.name);
   for(const n of ['도로교통법','도로교통법 제5조, 제6조','도로교통법 제32조의2제1항','자동차관리법 제29조 제30조','주차장법 제33조'])expect(names).toContain(n);
  }
  for(const bar of bars){
   expect(bar.href).toContain('lawExact=true');
   await page.goto(bar.href);await expect.poll(()=>count(page),{message:bar.name+' '+bar.href}).toBe(bar.count);
  }
 });
}

