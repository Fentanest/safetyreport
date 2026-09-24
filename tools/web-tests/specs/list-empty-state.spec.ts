import { test, expect, Page } from '@playwright/test';

// 목록 빈 결과 안내와 좁은 화면 검색칸 (G10 통합 수정, 2026-09-24).
async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

test('empty result message is inside the visible table area', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await login(page);
  await page.goto('/data/all');
  await expect(page.locator('.dataTables_wrapper')).toHaveCount(1);
  await page.locator('div.dataTables_filter input').fill('존재하지않는검색어zz');
  const message = page.getByText('조건에 맞는 신고 내역이 없습니다');
  await expect(message).toBeVisible();
  const { msg, box } = await message.evaluate((el: HTMLElement) => ({
    msg: el.getBoundingClientRect(),
    box: (el.closest('.dataTables_scrollBody') as HTMLElement).getBoundingClientRect(),
  }));
  expect(msg.left).toBeGreaterThanOrEqual(box.left);
  expect(msg.right).toBeLessThanOrEqual(box.right + 1);
});

test('search box stays inside the card at 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await login(page);
  await page.goto('/data/all');
  await expect(page.locator('.dataTables_wrapper')).toHaveCount(1);
  const { inputRight, wrapRight } = await page.locator('div.dataTables_filter input').evaluate((el: HTMLElement) => ({
    inputRight: el.getBoundingClientRect().right,
    wrapRight: (el.closest('.dataTables_wrapper') as HTMLElement).getBoundingClientRect().right,
  }));
  expect(inputRight).toBeLessThanOrEqual(wrapRight + 1);
});
