import { test, expect, Page } from '@playwright/test';

// 업데이트 뒤 한 번 훑기 작업의 하단 진행 표시줄(base.html #srJobBar). 상태 응답을 흉내 내 진행 → 일시정지 → 완료를 확인한다.
async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

const job = (state: string, done: number) => ({ key: 'photo_capture_time', label: '주정차 사진 촬영 시각 읽기', state, total: 480, done,
  filled: done, failed: 0, current: 'SPP-2609-9000123', message: state === 'paused' ? '크롤링이 끝나면 이어서 합니다' : state === 'completed' ? '478건 채움, 2건은 다음에 다시' : '' });

test('bottom bar shows running, paused and completed jobs and hides when idle', async ({ page }) => {
  let reply: any = { active: false, jobs: [] };
  await page.route('**/maintenance/status', (route) => route.fulfill({ json: reply }));
  await login(page);
  const bar = page.locator('#srJobBar');
  await expect(bar).toBeHidden();

  reply = { active: true, jobs: [job('running', 132)] };
  await page.goto('/stats');
  await expect(bar).toBeVisible();
  await expect(bar).toContainText('주정차 사진 촬영 시각 읽기 · 132/480 · SPP-2609-9000123');
  await expect(bar.locator('.sr-busy')).toBeVisible();

  reply = { active: true, jobs: [job('paused', 140)] };
  await expect(bar).toHaveClass(/is-paused/, { timeout: 5000 });
  await expect(bar).toContainText('크롤링이 끝나면 이어서 합니다');

  reply = { active: false, jobs: [job('completed', 480)] };
  await expect(bar).toHaveClass(/is-done/, { timeout: 5000 });
  await expect(bar).toContainText('주정차 사진 촬영 시각 읽기 완료 · 478건 채움');
  await expect(bar).toBeHidden({ timeout: 10000 });
});

for (const [theme, width] of [['light', 1440], ['dark', 1440], ['dark', 390]] as const) {
  test(`bottom bar fits and stays clear of the search button (${theme} ${width})`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript((t) => { try { localStorage.setItem('sr-theme', t); } catch (e) {} }, theme);
    await page.route('**/maintenance/status', (route) => route.fulfill({ json: { active: true, jobs: [job('running', 132)] } }));
    await login(page);
    await page.goto('/data/all');
    const bar = page.locator('#srJobBar');
    await expect(bar).toBeVisible();
    const b = await bar.boundingBox(), s = await page.locator('.floating-search-btn').boundingBox();
    expect(b!.x + b!.width).toBeLessThanOrEqual(width);
    const overlap = !(b!.x + b!.width <= s!.x || s!.x + s!.width <= b!.x || b!.y + b!.height <= s!.y || s!.y + s!.height <= b!.y);
    expect(overlap).toBeFalsy();
    await page.screenshot({ path: testInfo.outputPath(`job-bar-${theme}-${width}.png`) });
  });
}
