import {test, expect, Page} from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';

const root=path.resolve(__dirname,'../../..');
async function login(page:Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'),page.getByRole('button',{name:'로그인'}).click()]);
}
async function stats(page:Page,query='') {
  await page.goto('/stats'+query);
  await expect(page.locator('.stats-pane:visible .dataTables_wrapper')).toHaveCount(1);
}
for(const theme of ['light','dark']) for(const width of [360,768,1366,1920]) {
  test(`crawl/stats/map ${theme} ${width}`,async({page},info)=>{
    await page.setViewportSize({width,height:900});
    await page.addInitScript(t=>localStorage.setItem('sr-theme',t),theme);
    const errors:string[]=[]; page.on('pageerror',e=>errors.push(String(e)));
    await login(page);
    await page.goto('/crawl/');
    await expect(page.locator('#crawlLayout')).toBeVisible();
    expect(await page.locator('#crawlLayout > div').count()).toBe(2);
    const option=await page.locator('#crawlLayout > div').first().boundingBox();
    const log=await page.locator('#crawlLayout > div').nth(1).boundingBox();
    if(width>=768) expect(Math.abs(option!.y-log!.y)).toBeLessThan(2);
    else expect(log!.y).toBeGreaterThan(option!.y+option!.height-2);
    const consoleBox=await page.locator('#logConsole').boundingBox();
    expect(consoleBox!.height).toBeLessThanOrEqual(622);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
    await expect(page.locator('#srRebuildBanner')).toBeHidden();
    await page.screenshot({path:info.outputPath(`crawl-${theme}-${width}.png`),fullPage:true});
    await stats(page);
    await expect(page.locator('#statsResultList')).toContainText('일부수용');
    await expect(page.locator('#statsTypeBase')).toContainText('복수 법규도 신고당 1건');
    await expect(page.locator('#statsMiniMap .leaflet-marker-icon').first()).toBeVisible();
    const marker=page.locator('#statsMiniMap .leaflet-marker-icon').last();
    await marker.hover();
    const tip=page.locator('.report-map-tooltip').filter({visible:true}).first();
    await expect(tip).toBeVisible();
    expect(await tip.evaluate(el=>getComputedStyle(el,'::before').content)).toBe('none');
    expect(await tip.evaluate(el=>getComputedStyle(el,'::after').content)).toBe('none');
    await marker.click();
    await expect(page.locator('.leaflet-popup')).toBeVisible();
    await page.locator('.leaflet-popup-close-button').click();
    await page.locator('#statsMiniMap .leaflet-control-zoom-in').click();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
    await page.screenshot({path:info.outputPath(`stats-${theme}-${width}.png`),fullPage:true});
    const timing=await page.evaluate(()=>({navigation:performance.getEntriesByType('navigation').map(e=>e.toJSON()),
      resources:performance.getEntriesByType('resource').filter(e=>/stats\/content|map\/points|sunwi/.test(e.name)).map(e=>e.toJSON()),
      marks:performance.getEntriesByType('mark').map(e=>e.toJSON())}));
    await info.attach('timings',{body:JSON.stringify(timing),contentType:'application/json'});
    expect(errors).toEqual([]);
  });
}
for(const name of ['legacy','future']) test(`DB ${name} rejection remains visible and state intact`,async({page,request})=>{
  await login(page);
  const before=await page.request.get('/settings/community/rebuild');
  const baseline=await before.json();
  await page.goto('/backup/');
  await page.locator('#dbFile').setInputFiles(path.join(root,`.agent-runs/v3-user-reports/browser-fixture/${name}.db`));
  page.once('dialog',d=>d.accept());
  const response=page.waitForResponse(r=>r.url().endsWith('/backup/upload'));
  await page.locator('#uploadForm button[type=submit]').click();
  const r=await response; expect(r.status()).toBe(409);
  expect((await r.json()).code).toBe(name==='legacy'?'DB_LEGACY_UNSUPPORTED':'DB_SCHEMA_UNSUPPORTED');
  await expect(page.locator('#result')).toContainText(name==='legacy'?'이전 버전 DB는 이 버전에서 복원할 수 없습니다':'더 새 버전');
  await expect(page.locator('#uploadForm button[type=submit]')).toBeEnabled();
  expect(await (await page.request.get('/settings/community/rebuild')).json()).toEqual(baseline);
});
test('completed initialization survives reload and page reentry',async({page})=>{
  await login(page); await page.goto('/onboarding/rebuild');
  await expect(page.locator('#rbState')).toHaveText(/완료|필요 없음/);
  await expect(page.locator('#rbLaterLink')).toHaveAttribute('href','/');
  await expect(page.getByText('나중에 하기',{exact:true})).toHaveCount(0);
  await page.locator('#rbLaterLink').click(); await expect(page.locator('#srRebuildBanner')).toBeHidden();
  await page.reload(); await expect(page.locator('#srRebuildBanner')).toBeHidden();
});
for(const state of ['running','paused','validating','failed']) test(`initialization ${state} UI (fixture status response)`,async({page})=>{
  await login(page);
  await page.route('**/settings/community/rebuild',route=>route.fulfill({json:{status:'success',data:{state,required:true,counts:{fetched:3,pending:2},last_error:state==='failed'?'fixture failure':''}}}));
  await page.goto('/onboarding/rebuild');
  const labels:{[k:string]:string}={running:'진행 중',paused:'일시 중지',validating:'확인 중',failed:'실패'};
  await expect(page.locator('#rbState')).toHaveText(labels[state]);
  if(state==='running') await expect(page.locator('#rbBackground')).toContainText('다른 페이지로 이동해도 서버에서 계속 진행됩니다');
  await expect(page.locator('#rbLaterLink')).toHaveAttribute('href','/');
});
test('late initial map cannot overwrite a newer category',async({page})=>{
  await login(page);
  let release!:()=>void; const held=new Promise<void>(r=>release=r);
  let initial=true;
  await page.route('**/stats/map/points?**',async route=>{
    if(initial){initial=false;await held;}
    await route.continue().catch(()=>{});
  });
  await stats(page);
  const newer=page.waitForResponse(r=>r.url().includes('/stats/map/points?')&&r.url().includes('category=other'));
  await page.locator('.stats-cat-btn[data-cat=other]').click(); await newer;
  await expect(page.locator('#statsMapState')).toContainText('공식 좌표가 있는 신고가 없습니다');
  release();
  await expect(page.locator('.stats-cat-btn[data-cat=other]')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('#statsMiniMap')).toBeHidden();
});
for(const theme of ['light','dark']) test(`200% zoom ${theme}`,async({page},info)=>{
  await page.setViewportSize({width:1366,height:900});
  await page.addInitScript(t=>localStorage.setItem('sr-theme',t),theme);
  await login(page); await page.goto('/crawl/');
  await page.evaluate(()=>{document.documentElement.style.zoom='2';});
  await expect(page.locator('#logConsole')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
  await page.screenshot({path:info.outputPath('zoom.png'),fullPage:true});
  await stats(page);
  await page.evaluate(()=>{document.documentElement.style.zoom='2';});
  await expect(page.locator('#statsResultList')).toContainText('일부수용');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
  await page.screenshot({path:info.outputPath('stats-zoom.png'),fullPage:true});
});

test('bounded batched log stream and explicit follow (synthetic WS frames)',async({page})=>{
  await login(page);
  let feed:any;
  await page.routeWebSocket('**/crawl/ws/logs',socket=>{feed=socket;});
  await page.goto('/crawl/');
  await expect.poll(()=>!!feed).toBeTruthy();
  feed.send(Array.from({length:4000},(_,i)=>`${i} 긴 로그 ${'한글'.repeat(60)}\n`).join(''));
  await expect.poll(()=>page.locator('#logConsole').evaluate(el=>el.textContent!.length)).toBeGreaterThan(1000);
  const size=await page.locator('#logConsole').evaluate(el=>({lines:el.textContent!.split('\n').length,bytes:new TextEncoder().encode(el.textContent!).length}));
  expect(size.lines).toBeLessThanOrEqual(2000); expect(size.bytes).toBeLessThanOrEqual(256*1024);
  await page.locator('#logConsole').evaluate(el=>{el.scrollTop=0;el.dispatchEvent(new Event('scroll'));});
  await expect(page.locator('#logFollow')).toHaveAttribute('aria-pressed','false');
  feed.send('추가 로그\n'); await expect(page.locator('#logConsole')).toContainText('추가 로그');
  expect(await page.locator('#logConsole').evaluate(el=>el.scrollTop)).toBe(0);
  await page.locator('#logFollow').click();
  await expect(page.locator('#logFollow')).toHaveAttribute('aria-pressed','true');
  expect(await page.locator('#logConsole').evaluate(el=>el.scrollTop)).toBeGreaterThan(0);
});

test('law distribution drilldown matches the exact stored combination',async({page})=>{
  await login(page); await stats(page,'?dedupe=canonical');
  const item=page.locator('#statsTypeList li').first();
  const expected=Number((await item.locator('.sr-hbar-value').textContent())!.match(/[\d,]+/)![0].replace(/,/g,''));
  await expect(item.locator('a')).toHaveAttribute('href',/lawExact=true/);
  await Promise.all([page.waitForURL('**/data/traffic?**'),item.locator('a').click()]);
  await expect(page.locator('.dataTables_wrapper')).toHaveCount(1);
  expect(await page.locator('table.dataTable tbody tr').count()).toBe(expected);
  await stats(page,'?excludePolice=true');
  await expect(page.locator('#statsTypeList a')).toHaveCount(0);
});

test('additive client page/map APIs preserve full totals and old clients are refused',async({request})=>{
  const key=fs.readFileSync(path.join(root,'.agent-runs/v3-user-reports/browser-fixture/fixture-api-key.txt'),'utf8').trim();
  const headers={'X-API-Key':key,'X-SafetyReport-Client':'mobile','X-SafetyReport-Version':'2.0.0+31','X-SafetyReport-Protocol':'3'};
  const full=await (await request.get('/api/v1/reports/traffic?dedupe=canonical',{headers})).json();
  const page=await request.get('/api/v1/reports/traffic/page?limit=1&dedupe=canonical',{headers});
  expect(page.status()).toBe(200); const data=await page.json();
  expect(data.total).toBe(full.count); expect(data.count).toBe(1); expect(data.next_offset).toBe(1);
  const map=await (await request.get('/api/v1/stats/map/points?category=traffic&dedupe=canonical&max_points=1',{headers})).json();
  expect(map.data.points.length).toBeLessThanOrEqual(1); expect(map.data.meta.total_reports).toBe(full.count);
  for(const url of ['/api/v1/reports/traffic/page','/api/v1/stats/map/points']) {
    const old=await request.get(url,{headers:{'X-API-Key':key}});
    expect(old.status()).toBe(409); expect((await old.json()).code).toBe('CLIENT_UPGRADE_REQUIRED');
  }
});
