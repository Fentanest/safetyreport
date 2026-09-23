import { test, expect, Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import * as fs from 'fs';
import * as path from 'path';
import { fixtureDataDir } from '../playwright.config';

// 현재(리뉴얼 전) 웹UI의 baseline 동선. 스크린샷은 기록용이며 golden 비교가 아니다.
const USER = 'fixture-admin';
const PASSWORD = 'fixture-pass-1234';

async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill(USER);
  await page.locator('#password').fill(PASSWORD);
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

test('unauthenticated navigation redirects to login; AJAX gets 401', async ({ page, request }) => {
  await page.goto('/data/all');
  await expect(page).toHaveURL(/\/login\?next=\/data\/all/);
  const res = await request.get('/data/all', { headers: { Accept: 'application/json' }, maxRedirects: 0 });
  expect(res.status()).toBe(401);
});

test('dashboard, list, stats render with fixture data', async ({ page }, testInfo) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
  await login(page);

  await expect(page.locator('body')).toContainText('2026-09-23 09:00:00'); // last_sync fixture
  await page.screenshot({ path: testInfo.outputPath('dashboard.png'), fullPage: true });

  await page.goto('/data/all');
  const rows = page.locator('table tbody tr');
  await expect(rows.first()).toBeVisible();
  await expect(page.locator('body')).toContainText('SPP-2601-9000002');
  await page.screenshot({ path: testInfo.outputPath('data-all.png'), fullPage: false });

  await page.goto('/stats');
  await expect(page.locator('body')).toContainText('서울특별시 강서경찰서');
  await page.screenshot({ path: testInfo.outputPath('stats.png'), fullPage: false });

  fs.writeFileSync(testInfo.outputPath('console-errors.json'), JSON.stringify(consoleErrors, null, 1));
});

test('fixture mode blocks crawl start instead of spawning crawler', async ({ page }) => {
  await login(page);
  const res = await page.request.post('/crawl/enqueue-selected', { data: { report_numbers: ['SPP-2604-9000006'] } });
  const body = await res.json();
  expect(body.status).toBe('error');
  expect(body.message).toContain('fixture');
});

test('mobile API contract: /api/v1/summary requires key and keeps fields', async ({ request }) => {
  const key = fs.readFileSync(path.join(fixtureDataDir, 'fixture-api-key.txt'), 'utf-8').trim();
  expect((await request.get('/api/v1/summary')).status()).toBe(401);
  const res = await request.get('/api/v1/summary', { headers: { 'X-API-Key': key } });
  expect(res.ok()).toBeTruthy();
  const json = await res.json();
  for (const field of ['last_crawl_time', 'total', 'acceptCount', 'partialCount', 'rejectCount', 'processingCount',
    'supplementCount', 'completedCount', 'withdrawCount', 'withdrawRawCount', 'recent_answers', 'watchlist', 'exclude_withdraw']) {
    expect(json.data, field).toHaveProperty(field);
  }
});

test('accessibility baseline (report only)', async ({ page }, testInfo) => {
  const summary: Record<string, number> = {};
  await page.goto('/login');
  summary['/login'] = (await new AxeBuilder({ page }).analyze()).violations.length;
  await login(page);
  for (const url of ['/', '/data/all', '/stats']) {
    await page.goto(url);
    summary[url] = (await new AxeBuilder({ page }).analyze()).violations.length;
  }
  fs.writeFileSync(testInfo.outputPath('axe-violation-counts.json'), JSON.stringify(summary, null, 1));
});
