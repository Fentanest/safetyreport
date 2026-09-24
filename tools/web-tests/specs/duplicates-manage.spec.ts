import { test, expect, Page } from '@playwright/test';

// 중복 신고 관리(W3): 상태 탭, 사용자 판단 표시, 두 테마·휴대폰 폭.
async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

test('status tabs filter the groups and mark the current one', async ({ page }) => {
  await login(page);
  await page.goto('/duplicates/manage');
  const tabs = page.locator('nav.sr-seg a');
  await expect(tabs).toHaveCount(4);
  await expect(tabs.first()).toHaveAttribute('aria-current', 'page');
  await Promise.all([page.waitForURL('**duplicate_status=confirmed_duplicate'), tabs.filter({ hasText: '중복 확정' }).click()]);
  await expect(page.locator('nav.sr-seg a.active')).toContainText('중복 확정');
  const groups = page.locator('.sr-dup-group');
  expect(await groups.count()).toBeGreaterThan(0);
  for (const g of await groups.all()) await expect(g.locator('.sr-dup-head')).toContainText('중복 확정');
});

test('saving a group records a user decision shown in the header', async ({ page }) => {
  await login(page);
  await page.goto('/duplicates/manage');
  const group = page.locator('.sr-dup-group').first();
  await group.locator('input[name="note"]').fill('테스트 메모');
  await Promise.all([page.waitForURL('**/duplicates/manage**'), group.getByRole('button', { name: '설정 저장' }).click()]);
  const header = page.locator('.sr-dup-group').first().locator('.sr-dup-decision');
  await expect(header).toContainText('사용자 판단');
  await expect(header).toHaveText(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}/);
  // 메모 원상복구(판단 기록은 남는다)
  const again = page.locator('.sr-dup-group').first();
  await again.locator('input[name="note"]').fill('');
  await Promise.all([page.waitForURL('**/duplicates/manage**'), again.getByRole('button', { name: '설정 저장' }).click()]);
});

for (const theme of ['light', 'dark']) {
  test(`no horizontal overflow at 390px in ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript((t) => { try { localStorage.setItem('sr-theme', t); } catch (e) {} }, theme);
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await login(page);
    await page.goto('/duplicates/manage');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    expect(errors).toEqual([]);
  });
}
