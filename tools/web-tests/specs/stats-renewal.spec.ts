import { test, expect, Page } from '@playwright/test';
import * as fs from 'fs';

// 통계 개편(2026-09-28): 여섯 보기·선택 항목 상세·CSV·지도(응답 역전)·보기 상태 복원·전국 안전신고 현황.
// 수치는 서버 집계(overview API·표 셀)와 비교한다. 시안 숫자와 무관하다.
async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

async function openStats(page: Page, query = '') {
  await page.addInitScript(() => {
    try {
      if (!sessionStorage.getItem('__stats_spec_init')) {
        sessionStorage.setItem('__stats_spec_init', '1');
        sessionStorage.setItem('stats_cat', 'traffic');
        sessionStorage.setItem('stats_type', 'agency');
      }
    } catch (e) {}
  });
  await page.goto('/stats' + query);
  await expect(page.locator('.stats-pane:visible .dataTables_wrapper')).toHaveCount(1);
}

const TYPES = ['agency', 'person', 'police-agency', 'police-person', 'other-agency', 'other-person'];

test('six views are selectable and never change the summary cards', async ({ page }) => {
  await login(page);
  await openStats(page);
  const total = await page.locator('#statsOverview [data-v="total"]').textContent();
  for (const type of TYPES) {
    await page.locator(`.stats-type-btn[data-type="${type}"]`).click();
    await expect(page.locator(`.stats-type-btn[data-type="${type}"]`)).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator(`#traffic-${type}`)).toBeVisible();
    await expect(page.locator('#statsOverview [data-v="total"]')).toHaveText(total!);
    await expect(page.locator('#statsDetailScope')).toContainText('교통위반');
  }
  // 경찰 기관 + 비경찰 기관 행 수 = 기관별 행 수
  const count = async (type: string) => page.locator(`#traffic-${type} tbody tr.sr-drill`).count();
  expect((await count('police-agency')) + (await count('other-agency'))).toBe(await count('agency'));
  expect(await page.locator('#traffic-police-agency tbody tr.sr-drill', { hasNotText: '경찰' }).count()).toBe(0);
});

test('row selection opens a detail panel with the same values and clears on view change', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1000 });
  await login(page);
  await openStats(page);
  const row = page.locator('#traffic-agency tbody tr.sr-drill').first();
  const name = (await row.locator('td').first().textContent())!.trim();
  const totalCell = (await row.locator('td').nth(1).getAttribute('data-order'))!;
  await row.click();
  const panel = page.locator('#statsDetailPanel');
  await expect(panel).toBeVisible();
  await expect(panel.locator('#statsPanelName')).toHaveText(name);
  await expect(panel.locator('.sr-big')).toHaveText(Number(totalCell).toLocaleString('ko-KR'));
  await expect(row).toHaveClass(/is-selected/);
  // 목록 이동 링크는 행이 가진 드릴다운 주소(기관·정확히 일치)
  await expect(panel.getByRole('link', { name: '해당 신고 내역 보기' })).toHaveAttribute('href', (await row.getAttribute('data-href'))!);
  await expect(panel.getByRole('link', { name: '지도에서 보기' })).toHaveAttribute('href', /targetAgency=/);
  await page.locator('.stats-type-btn[data-type="person"]').click();
  await expect(panel).toBeHidden();
  await expect(page.locator('tr.sr-drill.is-selected')).toHaveCount(0);
});

test('narrow screens show the detail in a drawer', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await login(page);
  await openStats(page);
  await page.locator('#traffic-agency tbody tr.sr-drill').first().click();
  await expect(page.locator('#statsDetailDrawer.show')).toHaveCount(1);
  await expect(page.locator('#statsDetailDrawer #statsPanelName')).toBeVisible();
  await page.locator('#statsDetailDrawer .offcanvas-header .btn-close').click();
  await expect(page.locator('#statsDetailDrawer.show')).toHaveCount(0);
  await expect(page.locator('tr.sr-drill.is-selected')).toHaveCount(0);
});

test('table search filters only the detail table and the scope says so', async ({ page }) => {
  await login(page);
  await openStats(page);
  const total = await page.locator('#statsOverview [data-v="total"]').textContent();
  await page.locator('#statsTableSearch').fill('강서경찰');
  await expect(page.locator('#traffic-agency tbody tr.sr-drill')).toHaveCount(1);
  await expect(page.locator('#statsDetailScope')).toContainText('검색');
  await expect(page.locator('#statsOverview [data-v="total"]')).toHaveText(total!);
  // 숫자 열은 검색하지 않는다
  await page.locator('#statsTableSearch').fill('5');
  await expect(page.locator('#traffic-agency tbody td.dataTables_empty')).toHaveCount(1);
});

test('CSV export has the whole filtered table with confirmed and estimated amounts in separate columns', async ({ page }) => {
  await login(page);
  await openStats(page);
  await page.locator('.stats-type-btn[data-type="person"]').click();
  const rows = await page.locator('#traffic-person tbody tr.sr-drill').count();
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#statsExportCsv').click()]);
  expect(download.suggestedFilename()).toMatch(/\.csv$/);
  const text = fs.readFileSync((await download.path())!, 'utf-8').replace(/^﻿/, '');
  const lines = text.trim().split(/\r?\n/);
  const header = lines[0].split(',');
  expect(header).toEqual(expect.arrayContaining(['처리기관', '담당자', '확정 과태료(원)', '추정 과태료(원)', '추정 과태료 건수', '금액 미확인 과태료 건수']));
  expect(lines.length - 1).toBe(rows);
  const first = lines[1].split(',');
  const row = page.locator('#traffic-person tbody tr.sr-drill').first();
  expect(Number(first[header.indexOf('총 건수')])).toBe(Number(await row.locator('td').nth(2).getAttribute('data-order')));
  expect(Number(first[header.indexOf('확정 과태료(원)')])).toBe(Number(await row.locator('td').nth(4).getAttribute('data-order')));
});

test('late map response for an old category does not overwrite the new one', async ({ page }) => {
  await login(page);
  const meta = (total: number, geo: number) => ({ points: [], meta: { total_reports: total, geocoded_reports: geo, missing_reports: 0, address_groups: 0 } });
  await page.route('**/stats/map/points**', async (route) => {
    const cat = new URL(route.request().url()).searchParams.get('category');
    if (cat === 'traffic') {
      await new Promise((r) => setTimeout(r, 1500)); // 느린 이전 응답
      await route.fulfill({ json: meta(111, 11) });
    } else {
      await route.fulfill({ json: meta(222, 22) });
    }
  });
  await openStats(page);
  await page.locator('.stats-cat-btn[data-cat="parking"]').click();
  await expect(page.locator('#statsMapMeta')).toContainText('대상 222건');
  await page.waitForTimeout(1800);
  await expect(page.locator('#statsMapMeta')).toContainText('대상 222건');
  await expect(page.locator('#statsMapMeta')).not.toContainText('111');
});

test('map failure keeps the statistics and offers a retry', async ({ page }) => {
  await login(page);
  let fail = true;
  await page.route('**/stats/map/points**', (route) => (fail ? route.fulfill({ status: 500, body: 'x' })
    : route.fulfill({ json: { points: [], meta: { total_reports: 3, geocoded_reports: 0 } } })));
  await openStats(page);
  await expect(page.locator('#statsMapState')).toContainText('지도를 불러오지 못했습니다');
  await expect(page.locator('#traffic-agency tbody tr.sr-drill').first()).toBeVisible();
  fail = false;
  await page.locator('#statsMapRetry').click();
  await expect(page.locator('#statsMapMeta')).toContainText('대상 3건');
});

test('real map meta counts the same reports as the summary card', async ({ page }) => {
  await login(page);
  await openStats(page);
  await expect(page.locator('#statsMapMeta')).toContainText('대상');
  const total = (await page.locator('#statsOverview [data-v="total"]').textContent())!.replace(/[^0-9]/g, '');
  await expect(page.locator('#statsMapMeta')).toContainText(`대상 ${Number(total).toLocaleString('ko-KR')}건`);
  await expect(page.locator('#statsMapOpen')).toHaveAttribute('href', /category=traffic/);
});

test('a dedupe choice on the stats page carries over to the map population', async ({ page }) => {
  await login(page);
  await openStats(page, '?dedupe=raw');
  const total = (await page.locator('#statsOverview [data-v="total"]').textContent())!.replace(/[^0-9]/g, '');
  await expect(page.locator('#statsMapMeta')).toContainText(`대상 ${Number(total).toLocaleString('ko-KR')}건`);
  await expect(page.locator('#statsMapOpen')).toHaveAttribute('href', /dedupe=raw/);
});

test('coming back from the report list restores view, selection and page state', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1000 });
  await login(page);
  await openStats(page);
  await page.locator('.stats-type-btn[data-type="person"]').click();
  const row = page.locator('#traffic-person tbody tr.sr-drill').nth(1);
  const key = await row.getAttribute('data-key');
  await row.click();
  await Promise.all([page.waitForURL('**/data/traffic?**'), page.locator('#statsDetailPanel').getByRole('link', { name: '해당 신고 내역 보기' }).click()]);
  await page.goBack();
  await expect(page.locator('.stats-type-btn[data-type="person"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator(`#traffic-person tbody tr.sr-drill.is-selected`)).toHaveAttribute('data-key', key!);
});

test('law picker searches the law list and keeps the contract ids', async ({ page }) => {
  await login(page);
  await openStats(page);
  await page.locator('#statsLawToggle').click();
  await page.locator('#statsLawSearch').fill('제13조');
  await expect(page.locator('#statsLawSidebar button:visible')).toHaveCount(1);
  await Promise.all([page.waitForURL(/law=/), page.locator('#statsLawSidebar button:visible').click()]);
  await expect(page.locator('#statsLawCurrent')).toHaveText('도로교통법 제13조');
  await expect(page.locator('#statsFilterSummary')).toContainText('위반법규');
  // 칩의 X 로 그 조건만 해제
  await Promise.all([page.waitForURL((u) => !u.search.includes('law=')), page.getByRole('link', { name: '위반법규 조건 해제' }).click()]);
});

test('charts follow the theme tokens after switching themes', async ({ page }) => {
  await login(page);
  await openStats(page);
  const fill = () => page.locator('#statsMonthlyChart rect.sr-bar').first().evaluate((el) => getComputedStyle(el).fill);
  await page.locator('.sr-theme-switch button[data-sr-theme="light"]').click();
  const light = await fill();
  await page.locator('.sr-theme-switch button[data-sr-theme="dark"]').click();
  const dark = await fill();
  expect(light).not.toBe(dark);
});

test('nationwide Sunwi section lives on the statistics page', async ({ page }) => {
  await login(page);
  await page.route('**/sunwi/payload', (route) => route.fulfill({ json: {
    available: true, updated_at: '2026-09-27 03:00', failed_count: 0, csv_download_url: '/sunwi/download/top5',
    categories: [{ name: '교통', children: [{ name: '신호위반', items: [1, 2, 3, 4, 5].map((i) => ({ rank: i, region: `(합성) 지역 ${i}`, count: 100 - i })) }] }],
  } }));
  await openStats(page);
  await expect(page.locator('#statsSunwi')).toBeVisible();
  await expect(page.locator('#sunwiItems .sr-sunwi-item')).toHaveCount(5, { timeout: 10_000 });
  await expect(page.locator('#sunwiUpdatedAtLabel')).toContainText('2026-09-27 03:00');
  await page.locator('#sunwiPauseBtn').click();
  await expect(page.locator('#sunwiPauseBtn')).toHaveAttribute('aria-pressed', 'true');
});

for (const [theme, width] of [['light', 1920], ['dark', 1440], ['dark', 1280], ['light', 390]] as const) {
  test(`stats page has no page-level horizontal scroll (${theme} ${width})`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.addInitScript((t) => { try { localStorage.setItem('sr-theme', t); } catch (e) {} }, theme);
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await login(page);
    await openStats(page);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    // 모든 열을 켠 담당자 표도 페이지가 아니라 표 영역 안에서만 넘친다
    await page.locator('.stats-type-btn[data-type="person"]').click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.screenshot({ path: testInfo.outputPath(`stats-${theme}-${width}.png`), fullPage: true });
    expect(errors).toEqual([]);
  });
}
