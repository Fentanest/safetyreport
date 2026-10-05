import { test, expect } from '@playwright/test';
import * as fs from 'fs';

// Only synthetic fixture data; authenticate through the normal admin form.
test.beforeEach(async ({ page }) => {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await page.getByRole('button', { name: '로그인', exact: true }).click();
  await page.waitForURL('**/');
});

for (const theme of ['light', 'dark']) {
  test(`crawl log link selects logs and tab switching works (${theme})`, async ({ page }) => {
    await page.addInitScript(t => localStorage.setItem('sr-theme', t), theme);
    await page.goto('/crawl/');
    await page.locator('a[href="/file-browser?target=logs"]').click();
    await expect(page.locator('#logs-tab')).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('#logs-pane')).toBeVisible();
    await expect(page.locator('#results-pane')).toBeHidden();
    await page.locator('#results-tab').click();
    await expect(page.locator('#results-pane')).toBeVisible();
    await page.locator('#logs-tab').click();
    await expect(page.locator('#logs-pane')).toBeVisible();
    for (const query of ['', '?target=results', '?target=invalid']) {
      await page.goto('/file-browser' + query);
      await expect(page.locator('#results-tab')).toHaveAttribute('aria-selected', 'true');
      await expect(page.locator('#results-pane')).toBeVisible();
    }
  });
}

test('CSV label downloads the existing BOM CSV', async ({ page }) => {
  await page.goto('/data/all');
  const buttons = page.getByRole('button', { name: 'CSV 다운로드', exact: true });
  await expect(buttons).toHaveCount(2);
  await expect(page.locator('#allTable_wrapper')).toBeVisible();
  const downloading = page.waitForEvent('download');
  await buttons.first().click();
  const download = await downloading;
  expect(download.suggestedFilename()).toBe('safetyreport_data.csv');
  const contents = fs.readFileSync((await download.path())!);
  expect([...contents.subarray(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
  expect(contents.toString('utf8')).toContain('신고번호');
});

test('map describes the existing point groups accurately', async ({ page }) => {
  await page.route('**/*.tile.openstreetmap.org/**', route => route.abort());
  await page.goto('/stats/map');
  await expect(page.getByText('지도 표시 지점', { exact: true })).toBeVisible();
  await expect(page.locator('.map-chip').filter({ hasText: '지도 표시 지점' })).toHaveCount(1);
  await expect(page.getByText('지도 주소 그룹', { exact: true })).toHaveCount(0);
});
