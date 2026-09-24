import { test, expect, Page } from '@playwright/test';

// 저장 계층 재설계 R4: 편집한 필드 표시·원본 보기·되돌리기 (결정 D-1).
async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

test('edited field shows a badge and the site original, and can be reverted', async ({ page }) => {
  await login(page);
  const url = '/db-editor/traffic/90000002';
  await page.goto(url);
  const content = page.locator('textarea[name="처리내용"]');
  const original = await content.inputValue();
  await expect(page.locator('.sr-revert')).toHaveCount(0);

  await content.fill('편집기에서 고친 처리내용');
  await Promise.all([page.waitForURL('**/db-editor?category=traffic'), page.getByRole('button', { name: '저장' }).click()]);

  await page.goto(url);
  await expect(page.locator('textarea[name="처리내용"]')).toHaveValue('편집기에서 고친 처리내용');
  const revert = page.locator('.sr-revert[data-target="처리내용"]');
  await expect(revert).toHaveCount(1);
  await expect(page.locator('.sr-site-original')).toContainText(original.slice(0, 10));
  await expect(page.locator('.sr-revert')).toHaveCount(1); // 다른 필드는 원본과 같아 표시 없음

  await revert.click();
  await expect(page.locator('textarea[name="처리내용"]')).toHaveValue(original);
  await Promise.all([page.waitForURL('**/db-editor?category=traffic'), page.getByRole('button', { name: '저장' }).click()]);
  await page.goto(url);
  await expect(page.locator('.sr-revert')).toHaveCount(0);
});
