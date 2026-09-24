import { test, expect, Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fixtureDataDir } from '../playwright.config';

const USER = 'fixture-admin';
const PASSWORD = 'fixture-pass-1234';

async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill(USER);
  await page.locator('#password').fill(PASSWORD);
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

test('dashboard cards match api and links', async ({ page, request }) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
  await login(page);

  // fetch api
  const key = fs.readFileSync(path.join(fixtureDataDir, 'fixture-api-key.txt'), 'utf8').trim();
  const res = await request.get('/api/v1/summary', { headers: { 'X-API-Key': key } });
  expect(res.ok()).toBeTruthy();
  const json = await res.json();
  const apiData = json.data;

  // Wait for rendering
  await expect(page.locator('.dashboard-card').first()).toBeVisible();

  // Helper function to find card by exact title
  const getCard = (title: string) => page.locator('.dashboard-card').filter({ has: page.locator('.dashboard-card-title', { hasText: new RegExp(`^${title}$`) }) }).first();

  const getTotal = await getCard('총 신고').locator('.dashboard-card-value').textContent();
  expect(getTotal?.trim()).toBe(apiData.total.toLocaleString());

  const getAccept = await getCard('수용').locator('.dashboard-card-value').textContent();
  expect(getAccept?.trim()).toBe(apiData.acceptCount.toLocaleString());

  // Test links
  await expect(getCard('총 신고')).toHaveAttribute('onclick', "window.location.href='/data/all'");
  await expect(getCard('보완 요청')).toHaveAttribute('onclick', "window.location.href='/data/all?status=보완요청'");
  await expect(getCard('처리 중')).toHaveAttribute('onclick', "window.location.href='/data/all?status=처리중'");
  await expect(getCard('답변 완료')).toHaveAttribute('onclick', "window.location.href='/data/all?status=완료'");
  
  if (!apiData.exclude_withdraw) {
      await expect(getCard('취하')).toHaveAttribute('onclick', "window.location.href='/data/all?status=취하'");
  }
  await expect(getCard('수용')).toHaveAttribute('onclick', "window.location.href='/data/all?status=수용'");
  await expect(getCard('일부수용')).toHaveAttribute('onclick', "window.location.href='/data/all?status=일부수용'");
  
  await expect(getCard('불수용/기타')).toHaveAttribute('onclick', "window.location.href='/data/all?status=불수용'");

  await expect(getCard('과태료 부과')).toHaveAttribute('onclick', "window.location.href='/data/traffic?fine=과태료'");
  await expect(getCard('경고장/범칙금 발부')).toHaveAttribute('onclick', "window.location.href='/data/traffic?fine=경고'");
  await expect(getCard('미확인')).toHaveAttribute('onclick', "window.location.href='/data/traffic?fine=미확인'");

  // Test Sunwi prev/next buttons
  const isSunwiAvailable = await page.locator('#sunwiPrevParentCategory').waitFor({ state: 'visible', timeout: 3000 }).then(() => true).catch(() => false);
  if (isSunwiAvailable) {
    const nextBtn = page.locator('#sunwiNextParentCategory');
    // At first, wait for Sunwi to be loaded (category title should not be empty)
    await expect(page.locator('#sunwiParentCategoryName')).not.toBeEmpty();
    const initTitle = await page.locator('#sunwiParentCategoryName').textContent();
    
    if (await nextBtn.isEnabled()) {
      await nextBtn.click();
      await expect(page.locator('#sunwiParentCategoryName')).not.toHaveText(initTitle!);
    }
  }

  // Test recent answers link opens modal
  const firstReport = page.locator('#recentAnswersBody a.report-detail-link').first();
  if (await firstReport.isVisible()) {
      await firstReport.click();
      await expect(page.locator('#reportDetailModal')).toBeVisible();
      await page.locator('#reportDetailModal .btn-close').click();
  }

  // No console error
  expect(consoleErrors.length).toBe(0);
});

for (const theme of ['light', 'dark']) {
  for (const width of [1440, 390]) {
    test(`no horizontal overflow in ${theme} at ${width}`, async ({ page }) => {
      await page.addInitScript(`localStorage.setItem('sr-theme', '${theme}');`);
      await page.setViewportSize({ width, height: 900 });
      await login(page);
      await expect(page.locator('body')).toBeVisible();
      
      // Wait for network idle or 2s
      await page.waitForTimeout(1000);
      
      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      const innerWidth = await page.evaluate(() => window.innerWidth);
      expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
    });
  }
}
