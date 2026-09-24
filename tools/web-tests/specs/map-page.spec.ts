import { test, expect, Page } from '@playwright/test';

// 신고 지도(W4): 지도 바깥 영역 — 세그먼트 버튼, 미변환 주소 창, 두 테마·휴대폰 폭.
async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

test('year and category buttons mark the selection and filter by URL', async ({ page }) => {
  await login(page);
  await page.goto('/stats/map');
  await expect(page.locator('#mapCategoryGroup [data-category="all"]')).toHaveAttribute('aria-pressed', 'true');
  await Promise.all([page.waitForURL('**category=parking**'), page.locator('#mapCategoryGroup [data-category="parking"]').click()]);
  await expect(page.locator('#mapCategoryGroup [data-category="parking"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#mapYearGroup [data-year="all"]')).toHaveClass(/active/);
});

test('missing address list opens', async ({ page }) => {
  await login(page);
  await page.goto('/stats/map');
  await page.getByRole('button', { name: /미변환 주소/ }).click();
  await expect(page.locator('#missingAddressModal')).toBeVisible();
  await expect(page.locator('#missingAddressModal .map-chip').first()).toContainText('주소 그룹');
});

for (const theme of ['light', 'dark']) {
  test(`no horizontal overflow at 390px in ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript((t) => { try { localStorage.setItem('sr-theme', t); } catch (e) {} }, theme);
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await login(page);
    await page.goto('/stats/map');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    expect(errors).toEqual([]);
  });
}
