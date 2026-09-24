import { test, Page } from '@playwright/test';
import * as path from 'path';

const USER = 'fixture-admin';
const PASSWORD = 'fixture-pass-1234';

async function login(page: Page) {
  await page.goto('/login');
  if (await page.locator('#username').isVisible()) {
    await page.locator('#username').fill(USER);
    await page.locator('#password').fill(PASSWORD);
    await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
  }
}

test.describe('Take Screenshots', () => {
  const VIEWPORTS = [
    { width: 1440, height: 900, name: '1440' },
    { width: 390, height: 844, name: '390' },
  ];
  const THEMES = ['light', 'dark'];
  const PAGES = [
    { path: '/crawl', name: 'crawl' },
    { path: '/rating', name: 'rating' },
    { path: '/watchlist', name: 'watchlist' },
  ];

  for (const theme of THEMES) {
    for (const vp of VIEWPORTS) {
      test(`Screenshot ${theme} at ${vp.width}x${vp.height}`, async ({ page }) => {
        await page.setViewportSize(vp);
        await login(page);
        for (const p of PAGES) {
          await page.goto(p.path);
          await page.waitForLoadState('load');
          await page.evaluate((t) => {
            localStorage.setItem('sr-theme', t);
            document.documentElement.setAttribute('data-bs-theme', t);
          }, theme);
          await page.waitForTimeout(500);

          const filename = `${p.name}_${vp.name}_${theme}.png`;
          const filepath = path.join(__dirname, '..', '..', '..', '.agent-runs', 'g14', 'shots', filename);
          await page.screenshot({ path: filepath, fullPage: true });
        }
      });
    }
  }
});
