import { test, expect, Page } from '@playwright/test';

// 설정 계열(W2): 앱 설정·관리자 계정·기기 연동·백업·파일 브라우저.
async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}
const PAGES = ['/settings', '/settings/admin', '/devices', '/backup', '/file-browser'];

for (const theme of ['light', 'dark']) {
  test(`settings pages fit 390px and have no errors in ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript((t) => { try { localStorage.setItem('sr-theme', t); } catch (e) {} }, theme);
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await login(page);
    for (const url of PAGES) {
      await page.goto(url);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), url).toBeTruthy();
    }
    expect(errors).toEqual([]);
  });
}

test('a setting survives a save and reload, then is restored', async ({ page }) => {
  await login(page);
  await page.goto('/settings');
  const field = page.locator('input[name="retry_interval"]');
  const original = await field.inputValue();
  const changed = String(Number(original || '10') + 1);
  await field.fill(changed);
  await Promise.all([page.waitForURL('**/settings**'), page.locator('.sr-sticky-save').click()]);
  await expect(page.locator('input[name="retry_interval"]')).toHaveValue(changed);
  await page.locator('input[name="retry_interval"]').fill(original);
  await Promise.all([page.waitForURL('**/settings**'), page.locator('.sr-sticky-save').click()]);
  await expect(page.locator('input[name="retry_interval"]')).toHaveValue(original);
});

test('API key can be created (name shown as text) and deleted', async ({ page }) => {
  await login(page);
  await page.goto('/devices');
  const name = 'e2e <b>key</b>';
  await page.locator('#newKeyName').fill(name);
  await page.getByRole('button', { name: '키 생성' }).click();
  await expect(page.locator('#newKeyResult')).toBeVisible();
  await expect(page.locator('#apiKeyTableBody tr').first().locator('td').first()).toHaveText(name); // 태그가 아니라 글자로
  await page.reload();
  const row = page.locator('#apiKeyTableBody tr', { hasText: 'e2e <b>key</b>' });
  await expect(row).toHaveCount(1);
  page.once('dialog', (d) => d.accept());
  await Promise.all([page.waitForURL('**/devices**'), row.locator('button.btn-outline-danger').click()]);
  await expect(page.locator('#apiKeyTableBody tr', { hasText: 'e2e <b>key</b>' })).toHaveCount(0);
});

test('backup download responds', async ({ page }) => {
  await login(page);
  const res = await page.request.get('/backup/download');
  expect(res.status()).toBe(200);
  expect((await res.body()).length).toBeGreaterThan(1000);
});
