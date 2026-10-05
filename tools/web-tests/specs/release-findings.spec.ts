import { test, expect, Page } from '@playwright/test';
import * as fs from 'fs';

// Only synthetic fixture data; authenticate through the normal admin form.
test.beforeEach(async ({ page }) => {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await page.getByRole('button', { name: '로그인', exact: true }).click();
  await page.waitForURL('**/');
});

async function expectActiveTab(page: Page, target: string) {
  // Wait for document.ready (including saved-tab restoration) to finish.
  await expect(page.locator('#resultTable_wrapper')).toBeAttached();
  const other = target === 'logs' ? 'results' : 'logs';
  await expect(page.locator(`#${target}-tab`)).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator(`#${other}-tab`)).toHaveAttribute('aria-selected', 'false');
  await expect(page.locator(`#${target}-pane`)).toBeVisible();
  await expect(page.locator(`#${other}-pane`)).toBeHidden();
}

for (const theme of ['light', 'dark']) {
  test(`crawl log link selects logs and tab switching works (${theme})`, async ({ page }) => {
    await page.addInitScript(t => localStorage.setItem('sr-theme', t), theme);
    await page.evaluate(() => localStorage.setItem('activeFileTab', 'results-tab'));
    await page.goto('/crawl/');
    await page.locator('a[href="/file-browser?target=logs"]').click();
    await expectActiveTab(page, 'logs');
    await page.locator('#results-tab').click();
    await expectActiveTab(page, 'results');
    await expect.poll(() => page.evaluate(() => localStorage.getItem('activeFileTab'))).toBe('results-tab');
    await page.locator('#logs-tab').click();
    await expectActiveTab(page, 'logs');
    await expect.poll(() => page.evaluate(() => localStorage.getItem('activeFileTab'))).toBe('logs-tab');

    for (const saved of [null, 'logs', 'results']) {
      for (const target of [null, 'invalid', 'logs', 'results']) {
        await test.step(`saved=${saved}, target=${target}`, async () => {
          await page.evaluate(value => {
            if (value) localStorage.setItem('activeFileTab', value + '-tab');
            else localStorage.removeItem('activeFileTab');
          }, saved);
          await page.goto('/file-browser' + (target === null ? '' : `?target=${target}`));
          const explicit = target === 'logs' || target === 'results';
          await expectActiveTab(page, explicit ? target : saved ?? 'results');
          if (explicit) {
            await expect.poll(() => page.evaluate(() => localStorage.getItem('activeFileTab'))).toBe(`${target}-tab`);
          }
        });
      }
    }
  });
}

test('CSV label downloads the existing BOM CSV', async ({ page }) => {
  await page.goto('/data/all');
  const buttons = page.getByRole('button', { name: /CSV 다운로드/ });
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
