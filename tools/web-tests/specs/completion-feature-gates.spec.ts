import { test, expect, Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { spawnSync } from 'child_process';
import { fixtureDataDir } from '../playwright.config';

const USER = 'fixture-admin';
const PASSWORD = 'fixture-pass-1234';

async function login(page: Page, nextUrl?: string) {
  const url = nextUrl ? `/login?next=${encodeURIComponent(nextUrl)}` : '/login';
  await page.goto(url);
  await page.locator('#username').fill(USER);
  await page.locator('#password').fill(PASSWORD);
  await Promise.all([
    page.waitForURL(url => url.pathname === (nextUrl || '/')),
    page.getByRole('button', { name: '로그인' }).click()
  ]);
}

test('SH sidebar active and mobile overlay close', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);

  await page.goto('/data/all');
  await page.locator('#btnSidebarToggle').click();
  await expect(page.locator('#mainSidebar')).toHaveClass(/sidebar-open/);
  await expect(page.locator('#mainSidebar a[href="/data/all"]')).toHaveClass(/active/);

  await Promise.all([
    page.waitForURL('**/stats'),
    page.locator('#mainSidebar a[href="/stats"]').click()
  ]);
  await expect(page.locator('#mainSidebar')).not.toHaveClass(/sidebar-open/);
  await page.locator('#btnSidebarToggle').click();
  await expect(page.locator('#mainSidebar a[href="/stats"]')).toHaveClass(/active/);
});

test('logout confirm', async ({ page }) => {
  await login(page);
  await page.goto('/');
  page.once('dialog', dialog => {
    expect(dialog.message()).toContain('로그아웃');
    dialog.accept();
  });
  await Promise.all([
    page.waitForURL('**/login'),
    page.locator('#mainSidebar a[href="/logout"]').click()
  ]);
  await page.goto('/data/all');
  await expect(page).toHaveURL(/.*\/login.*/);
});

test('version fakefetch, status text, and update timer (clock advance)', async ({ page }) => {
  await page.clock.install();
  // First mock: unknown
  await page.route('**/version/latest', async route => {
    await route.fulfill({ json: { version: 'v2.0.0', status: 'unknown' } });
  });

  await login(page);
  await page.goto('/');

  // Wait for the text to appear naturally (fetch completes)
  await expect(page.locator('#sidebarVersionStatus')).toContainText('불가');

  // Now we want to test the timer. We must unroute the previous mock and add the new one.
  await page.unroute('**/version/latest');
  await page.route('**/version/latest', async route => {
    await route.fulfill({ json: { latest: '2.0.1', status: 'outdated' } });
  });

  // Install the clock *after* the initial load so it doesn't freeze initial rendering

  // Advance 5 minutes to trigger setInterval
  await page.clock.runFor(5 * 60 * 1000 + 1000);
  await expect(page.locator('#sidebarVersionStatus')).toContainText('2.0.1');
});

test('Detail modal displays complete information', async ({ page }) => {
  await login(page);
  await page.goto('/data/all');

  await expect(page.locator('#allTable')).toHaveAttribute('aria-busy', 'false');
  const firstReport = page.locator('a.report-detail-link').first();
  const sample = await page.evaluate(() => {
    const w = window as any, $ = w.jQuery;
    return $('#allTable').DataTable().row($('a.report-detail-link').first().closest('tr')).data();
  });
  await firstReport.click();
  await expect(page.locator('#reportDetailModal')).toBeVisible();

  await expect(page.locator('#rdName')).toHaveText(sample['신고명']);
  await expect(page.locator('#rdStatusBadge')).toContainText(sample['처리상태']);
  await expect(page.locator('#rdFields')).toContainText(sample['신고번호']);
  await expect(page.locator('#rdReportContentText')).toHaveText(sample['신고내용']);
  if (sample['처리내용']) await expect(page.locator('#rdProcessContentText')).toHaveText(sample['처리내용']);

  if (await page.locator('#rdSupplementsBadge').isVisible()) {
      await expect(page.locator('#rdSupplements')).toBeVisible();
  }
});

test('flatpickrko, search button hidden, fallback error', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.goto('/data/all');

  await expect(page.locator('.floating-search-btn')).toBeVisible();
  await page.locator('.floating-search-btn').click();
  await expect(page.locator('#offcanvasSearch')).toHaveClass(/show/);
  await expect(page.locator('.floating-search-btn')).toBeHidden();

  await page.locator('#searchReportDateStart').click();
  await expect(page.locator('.flatpickr-calendar.open')).toBeVisible();

  await page.locator('#offcanvasSearch .btn-close').click();

  // fallback error trigger
  await page.evaluate(() => {
      // @ts-ignore
      const record = { '신고명': '합성 fallback', '신고번호': 'FIXTURE-FALLBACK', 'ID': 'fixture-fallback' };
      Object.defineProperty(record, '발생일자', { get() { throw Error('synthetic render failure'); } });
      window.showReportDetail(record);
  });
  await expect(page.locator('#rdFields')).toContainText('일부 필드 형식이 예상과 달라');
});

test('login next and administrator credentials persist through logout and relogin', async ({ page }) => {
  await page.goto('/setup');
  await expect(page).toHaveURL(/.*\/login/);

  await login(page, '/data/traffic');
  await expect(page).toHaveURL(/.*\/data\/traffic/);

  try {
      await page.goto('/settings/admin');
      await page.locator('[name=current_password]').fill(PASSWORD);
      await page.locator('[name=new_username]').fill('new-admin');
      await page.locator('[name=new_password]').fill('new-pass-1234');
      await page.getByRole('button', { name: '변경', exact: true }).click();

      await expect(page).toHaveURL(/\/settings\/admin\?admin_success=/);
      await expect(page.locator('[name=new_username]')).toHaveValue('new-admin');
      await page.goto('/logout');

      await page.locator('#username').fill('new-admin');
      await page.locator('#password').fill('new-pass-1234');
      await Promise.all([
        page.waitForURL(url => url.pathname === '/'),
        page.getByRole('button', { name: '로그인' }).click()
      ]);
      await expect(page).toHaveURL(/.*\//);
  } finally {
      await page.goto('/settings/admin');
      await page.locator('[name=current_password]').fill('new-pass-1234');
      await page.locator('[name=new_username]').fill(USER);
      await page.locator('[name=new_password]').fill(PASSWORD);
      await page.getByRole('button', { name: '변경', exact: true }).click();
      await expect(page).toHaveURL(/\/settings\/admin\?admin_success=/);
  }
});

test('dashboard completed card URL and full population count', async ({ page }) => {
  await login(page);
  await page.goto('/');

  const getCard = (title: string) => page.locator('.dashboard-card').filter({ has: page.locator('.dashboard-card-title', { hasText: new RegExp(`^${title}$`) }) }).first();
  await Promise.all([
    page.waitForURL('**/data/all?status=*'),
    getCard('답변 완료').click()
  ]);
  await expect(page).toHaveURL(/.*\/data\/all\?status=%EC%99%84%EB%A3%8C/);

  await page.goto('/');
  const countStr = await getCard('총 신고').locator('.dashboard-card-value').textContent();
  const key = fs.readFileSync(path.join(fixtureDataDir, 'fixture-api-key.txt'), 'utf8').trim();
  const summary = await page.request.get('/api/v1/summary', { headers: { 'X-API-Key': key, 'X-SafetyReport-Client': 'mobile', 'X-SafetyReport-Version': '2.0.0+31', 'X-SafetyReport-Protocol': '3' } });
  expect(summary.ok()).toBe(true);
  expect(parseInt(countStr!.replace(/,/g, ''))).toBe((await summary.json()).data.total);
});

test('selected watchlist addition, intact SQLite backup, protocol3 version and duplicate page', async ({ page }) => {
  await login(page);
  page.on('dialog', dialog => dialog.accept());

  await page.goto('/data/all');
  await expect(page.locator('#allTable')).toHaveAttribute('aria-busy', 'false');
  const selected = await page.locator('a.report-detail-link').first().textContent();
  await page.locator('.row-checkbox').first().check();
  const [added] = await Promise.all([page.waitForResponse(r => r.url().endsWith('/watchlist/add')),
    page.locator('.btn-add-watchlist').first().click()]);
  expect(added.ok()).toBe(true);
  await page.goto('/');
  await expect(page.locator('#watchlistBody')).toContainText(selected!.trim());

  const res = await page.request.get('/backup/download');
  expect(res.ok()).toBeTruthy();
  const body = await res.body();
  expect(body.subarray(0,16).toString('binary')).toBe('SQLite format 3\u0000');
  const root = path.resolve(__dirname, '../../..');
  const artifact = path.join(root, '.agent-runs/refactoring-implementation/completion-download.db');
  fs.writeFileSync(artifact, body);
  const result = spawnSync(path.join(root,'.venv/bin/python'), ['-c',
    'import sqlite3,sys; c=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True); assert c.execute("PRAGMA quick_check").fetchone()[0]=="ok"; assert c.execute("SELECT COUNT(*) FROM mysafety").fetchone()[0]==24; c.close()', artifact]);
  expect(result.status, result.stderr.toString()).toBe(0);

  // API device name HTTPfailedfallback
  const key = fs.readFileSync(path.join(process.env.SR_FIXTURE_DIR!, 'fixture-api-key.txt'), 'utf8').trim();
  const res2 = await page.request.get('/api/v1/server/version', {headers: {'X-API-Key': key,
    'X-SafetyReport-Client':'mobile','X-SafetyReport-Version':'2.0.0+31','X-SafetyReport-Protocol':'3'}});
  expect(res2.ok()).toBeTruthy();
  expect((await res2.json()).protocol_version).toBe(3);
  await page.goto('/duplicates/manage');
  await expect(page.locator('#duplicateGroupsAccordion')).toBeAttached();
});
