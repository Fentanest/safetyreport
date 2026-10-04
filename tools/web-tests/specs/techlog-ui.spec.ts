// 2026-10-04 기술일지 화면 수리 회귀: C01 C02 C03 C07 C08 O-01 O-04 O-06 B-03 F-05.
import { test, expect, Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const data = process.env.SR_FIXTURE_DIR as string;

async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

test('C01 file browser select-all stays inside its tab and the delete confirm names each tab', async ({ page }) => {
  for (const [dir, name] of [['results', 'techlog-result.csv'], ['logs', 'techlog-old.log']]) {
    fs.mkdirSync(path.join(data, dir), { recursive: true });
    fs.writeFileSync(path.join(data, dir, name), 'x');
  }
  await login(page);
  await page.goto('/file-browser');
  await page.locator('#results-tab').click();
  await page.locator('#results-pane .select-all').check();
  expect(await page.locator('#resultTable .file-checkbox:checked').count()).toBeGreaterThan(0);
  expect(await page.locator('#logTable .file-checkbox:checked').count()).toBe(0);
  let message = '';
  page.once('dialog', d => { message = d.message(); d.dismiss(); });
  await page.locator('#btnDeleteSelected').click();
  await expect.poll(() => message).toContain('결과 파일');
  expect(message).toContain('techlog-result.csv');
  expect(message).not.toContain('로그 파일');
  expect(fs.existsSync(path.join(data, 'logs', 'techlog-old.log'))).toBe(true);
});

test('C02 C08 reset crawl needs the typed confirmation, chosen by keyboard; C03 status endpoint', async ({ page }) => {
  await login(page);
  await page.goto('/crawl/');
  await page.locator('#crawlFull').focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('#crawlReset')).toBeChecked();
  await page.locator('#btnStart').click();
  const ok = page.locator('.modal.show [data-sr-confirm=ok]');
  await expect(ok).toBeDisabled();
  await expect(page.locator('.modal.show')).toContainText('지워지는 것');
  await page.locator('.modal.show input.form-control').fill('DB 초기화');
  await expect(ok).toBeEnabled();
  await page.locator('.modal.show .btn-outline-secondary').click();
  await expect(page.locator('.modal.show')).toHaveCount(0);
  await expect(page.locator('#btnStart')).toBeEnabled();
  const status = await (await page.request.get('/crawl/status')).json();
  expect(status).toEqual({ running: false });
});

test('O-01 O-06 dashboard ratio bar adds up and has a legend; F-05 tables do not overlap at 390px', async ({ page }) => {
  await login(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  const widths = await page.locator('.sr-ratio-bar').first().locator('.progress-bar').evaluateAll(
    els => els.map(el => parseFloat(el.getAttribute('data-width') || '0')));
  expect(Math.abs(widths.reduce((a, b) => a + b, 0) - 100)).toBeLessThan(0.6);
  expect(await page.locator('.sr-ratio-legend').first().locator('li').count()).toBe(widths.length);
  const cells = page.locator('.sr-dash-table tbody tr').first().locator('td');
  if (await cells.count() > 1) {
    const [a, b] = [await cells.nth(0).boundingBox(), await cells.nth(1).boundingBox()];
    const link = await cells.nth(0).locator('a').boundingBox();
    if (a && b && link) expect(link.x + link.width).toBeLessThanOrEqual(b.x + 1);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('C07 closed mobile sidebar is inert and Escape returns focus to the toggle', async ({ page }) => {
  await login(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.locator('#mainSidebar')).toHaveAttribute('inert', '');
  await page.locator('#btnSidebarToggle').click();
  await expect(page.locator('#mainSidebar')).not.toHaveAttribute('inert', '');
  await page.keyboard.press('Escape');
  await expect(page.locator('#mainSidebar')).toHaveAttribute('inert', '');
  await expect(page.locator('#btnSidebarToggle')).toBeFocused();
});

test('B-03 map missing list loads when the modal opens; O-04 dates to the minute', async ({ page }) => {
  await login(page);
  await page.goto('/stats/map');
  const count = Number(await page.locator('#missingAddressList').getAttribute('data-count'));
  expect(await page.locator('#missingAddressList .missing-report-card').count()).toBe(0);
  if (count > 0) {
    const loaded = page.waitForResponse(r => r.url().includes('/stats/map/missing'));
    await page.locator('[data-bs-target="#missingAddressModal"]').first().click();
    await loaded;
    await expect(page.locator('#missingAddressList .accordion-item').first()).toBeVisible();
    const text = await page.locator('#missingAddressList .missing-report-meta').first().innerText();
    expect(text).not.toMatch(/\d{2}:\d{2}:\d{2}/);
  }
  await page.goto('/data/all');
  await expect(page.locator('#allTable')).toHaveAttribute('aria-busy', 'false');
  const dates = await page.locator('#allTable tbody tr td').allInnerTexts();
  expect(dates.some(t => /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(t.trim()))).toBe(true);
  expect(dates.some(t => /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(t.trim()))).toBe(false);
});
