import {test,expect,Page} from '@playwright/test';
import * as fs from 'fs';

async function login(page:Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'),page.getByRole('button',{name:'로그인'}).click()]);
}
async function ready(page:Page) {
  await expect(page.locator('.stats-pane:visible .dataTables_wrapper')).toHaveCount(1);
}
const panel=(page:Page)=>page.locator('#statsDetailPanel:visible, #statsDetailDrawer.show').first();

test('HTTP Upgrade cannot impersonate a websocket or authenticated admin',async({request})=>{
  for(const url of ['/data/all','/backup/download','/stats','/settings/']) {
    const r=await request.get(url,{headers:{Upgrade:'websocket'},maxRedirects:0});
    expect(r.status()).toBe(302);expect(r.headers().location).toMatch(/^\/login/);
  }
});

test('same-name agencies/persons: distinct selection, panel, CSV, list and map',async({page})=>{
  await login(page);await page.goto('/stats');await ready(page);
  for(const type of ['agency','person']) {
    await page.locator(`.stats-type-btn[data-type="${type}"]`).click();
    await page.locator('#statsTableSearch').fill('검수 동일명 기관');
    const rows=page.locator(`.stats-pane:visible tbody tr.sr-drill`);
    await expect(rows).toHaveCount(2);
    const keys=await rows.evaluateAll(els=>els.map(el=>JSON.parse(el.getAttribute('data-key')!)[0]));
    expect(new Set(keys).size).toBe(2);
    for(let i=0;i<2;i++) {
      await rows.nth(i).click();
      await expect(page.locator('.stats-pane:visible tr.is-selected')).toHaveCount(1);
      const selectedPanel=panel(page);
      await expect(selectedPanel).toBeVisible();
      await expect(selectedPanel.locator('[data-disp="fines"] .sr-v')).toHaveText(keys[i].includes('99999998')?'1건100.0%':'0건0.0%');
      const list=selectedPanel.getByRole('link',{name:'해당 신고 내역 보기'});
      const href=await list.getAttribute('href');
      const url=new URL(href!,'http://fixture.invalid');
      expect(url.searchParams.get('agencyKey')).toBe(keys[i]);
      expect(url.searchParams.get('status')).toBe('완료');
      const html=await (await page.request.get(href!)).text();
      expect(html).toContain(keys[i].includes('99999998')?'SR-REVIEW-1':'SR-REVIEW-2');
      expect(html).not.toContain(keys[i].includes('99999998')?'SR-REVIEW-2':'SR-REVIEW-1');
      const mapHref=await selectedPanel.getByRole('link',{name:'지도에서 보기'}).getAttribute('href');
      const mapURL=new URL(mapHref!,'http://fixture.invalid');
      expect(mapURL.searchParams.get('targetAgencyKey')).toBe(keys[i]);
      expect(mapURL.searchParams.get('completedOnly')).toBe('true');
      const points=await (await page.request.get('/stats/map/points?'+mapURL.searchParams)).json();
      expect(points.meta.total_reports).toBe(1);
      if(await page.locator('#statsDetailDrawer').isVisible()) {
        await page.locator('#statsDetailDrawer [data-bs-dismiss="offcanvas"]').click();
        await expect(page.locator('#statsDetailDrawer')).toBeHidden();
      }
    }
    const download=page.waitForEvent('download');await page.locator('#statsExportCsv').click();
    const csv=fs.readFileSync((await(await download).path())!,'utf8');
    const lines=csv.trim().split('\r\n');expect(lines).toHaveLength(3);
    const header=lines[0].replace(/^\uFEFF/,'').split(',');
    const amounts=lines.slice(1).map(l=>Number(l.split(',')[header.indexOf('확정 과태료(원)')]));
    expect(amounts.sort((a,b)=>a-b)).toEqual([0,40000]);
    expect(lines.slice(1).map(l=>l.split(',')[header.indexOf('기관 집계 키')]).sort()).toEqual(keys.sort());
    if(await page.locator('#statsDetailDrawer').isVisible()) {
      await page.locator('#statsDetailDrawer [data-bs-dismiss="offcanvas"]').click();
      await expect(page.locator('#statsDetailDrawer')).toBeHidden();
    }
  }
});

test('completed scope note and drilldown agree without marking unfinished reports as missing agency',async({page})=>{
  await login(page);await page.goto('/stats');await ready(page);
  await expect(page.locator('#statsDetailScope')).toContainText('답변 완료 전·그 외 상태 2건은 표에서 제외');
  await expect(page.locator('#statsDetailScope')).not.toContainText('기관 정보 없음');
  await page.locator('#statsTableSearch').fill('서울특별시 강서경찰서 교통과');
  await expect(page.locator('.stats-pane:visible tbody tr.sr-drill')).toHaveCount(1);
  await page.locator('.stats-pane:visible tbody tr.sr-drill').click();
  const href=await panel(page).getByRole('link',{name:'해당 신고 내역 보기'}).getAttribute('href');
  await page.goto(href!);
  await expect.poll(()=>page.evaluate(()=>window.jQuery('#trafficTable').DataTable().rows({search:'applied'}).count())).toBe(3);
});

test('slow DataTables language response does not race initial query/open',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await login(page);
  await page.route('**/i18n/ko.json',async route=>{
    // Intentional fault injection, not a readiness sleep.
    await new Promise(resolve=>setTimeout(resolve,650));
    await route.fulfill({json:{emptyTable:'비어 있음',zeroRecords:'결과 없음'}});
  });
  await page.goto('/data/traffic?open=SR-REVIEW-1');
  await expect(page.locator('#reportDetailModal')).toBeVisible();
  await expect.poll(()=>page.evaluate(()=>window.jQuery('#trafficTable').DataTable().rows({search:'applied'}).count())).toBe(1);
  expect(errors).toEqual([]);
});

test('pending stats shell controls work before detail and discard older map responses',async({page})=>{
  await login(page);
  let release!:()=>void;const pending=new Promise<void>(resolve=>release=resolve);
  await page.route('**/stats/content*',async route=>{await pending;await route.continue();});
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/stats');
  await expect(page.locator('#statsLawToggle')).toBeDisabled();
  await page.locator('.stats-cat-btn[data-cat="parking"]').click();
  await expect(page.locator('.stats-cat-btn[data-cat="parking"]')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('#statsMapOpen')).toHaveAttribute('href',/category=parking/);
  await expect(page.locator('#statsMiniMap .leaflet-marker-icon').first()).toBeVisible();
  // Replace a rendered early map as well as aborting a pending request.
  await page.locator('.stats-cat-btn[data-cat="traffic"]').click();
  await expect(page.locator('#statsMapOpen')).toHaveAttribute('href',/category=traffic/);
  await page.locator('.stats-cat-btn[data-cat="parking"]').click();
  release();await ready(page);
  await expect(page.locator('#parking-agency')).toBeVisible();
  await expect(page.locator('#statsMapOpen')).toHaveAttribute('href',/category=parking/);
  await expect(page.locator('#statsLawToggle')).toBeEnabled();
  expect(errors).toEqual([]);
});

test('year navigation works while detailed statistics are still pending',async({page})=>{
  await login(page);
  let release!:()=>void;const pending=new Promise<void>(resolve=>release=resolve);
  let first=true;
  await page.route('**/stats/content*',async route=>{
    if(first) {first=false;await pending;await route.abort();}
    else await route.continue();
  });
  try {
    await page.goto('/stats?agency='+encodeURIComponent('강서')+'&year=2026');
    await expect(page.locator('#statsLawToggle')).toBeDisabled();
    // Playwright click은 이동 완료까지 기다린다. 이전 페이지의 보류 route는
    // 버튼이 실제 다음 문서 요청을 만든 직후 해제해 탐색 대기를 가로막지 않는다.
    const request=page.waitForRequest(r=>r.isNavigationRequest()&&r.url().includes('/stats?')&&!new URL(r.url()).searchParams.has('year'));
    const click=page.locator('.stats-year-btn[data-year="all"]').click();
    await request;release();await click;
    await expect(page).not.toHaveURL(/year=/);
    expect(new URL(page.url()).searchParams.get('agency')).toBe('강서');
    await ready(page);
  } finally {release();}
});

for(const theme of ['light','dark']) for(const width of [360,768,1366,1920]) {
  test(`fixed stats/list layout ${theme} ${width}`,async({page},info)=>{
    const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
    await page.setViewportSize({width,height:900});
    await page.addInitScript(t=>localStorage.setItem('sr-theme',t),theme);
    await login(page);await page.goto('/stats');await ready(page);
    await expect(page.locator('.sr-stats-page')).toHaveAttribute('aria-busy','false');
    await expect(page.locator('#statsMiniMap .leaflet-marker-icon').first()).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
    await page.locator('#statsTableSearch').fill('검수 동일명 기관');
    await expect(page.locator('.stats-pane:visible tbody tr.sr-drill')).toHaveCount(2);
    const first=page.locator('.stats-pane:visible tbody tr.sr-drill').first();
    await first.focus();await page.keyboard.press('Enter');
    await expect(panel(page)).toBeVisible();
    await page.screenshot({path:info.outputPath(`identity-${theme}-${width}.png`),fullPage:true});
    await page.goto('/data/traffic');
    await expect(page.locator('#trafficTable')).toHaveAttribute('aria-busy','false');
    await expect(page.locator('#trafficTable_wrapper .dataTables_scrollBody')).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
    if(width===1366) {
      await page.evaluate(()=>document.documentElement.style.zoom='2');
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
      await page.screenshot({path:info.outputPath(`render-zoom-200-${theme}.png`),fullPage:true});
    }
    expect(errors).toEqual([]);
  });
}
