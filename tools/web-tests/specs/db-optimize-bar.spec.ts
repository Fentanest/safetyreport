import { test, expect, Page } from '@playwright/test';

// DB 최적화 작업(services/db_optimize.py)의 하단 표시줄(base.html #srJobBar).
// 상태 응답을 흉내 내 라벨·진행률과 단계 로그 펼침을 확인한다(라이트/다크, 390px).
async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

const LOG = [
  '2026-10-17 12:00:01 업그레이드 전 백업 완료(before_schema_v7_20261017_120001.db)',
  '2026-10-17 12:00:02 새 표 생성(스키마 7)',
  '2026-10-17 12:00:03 DB 최적화 시작(대상 24건, 파서 pc-parser-13)',
];

const optJob = (state: string, done: number, log: string[]) => ({
  key: 'db_optimize', label: 'DB 최적화 중입니다…', state, total: 24, done, stage: state,
  message: state === 'paused' ? '로그인 뒤 이어서 합니다' : state === 'error' ? 'RuntimeError' : '', log,
});

test('optimize job shows progress and expands the step log', async ({ page }) => {
  await page.route('**/maintenance/status', (route) => route.fulfill({
    json: { active: true, jobs: [optJob('running', 7, LOG)] },
  }));
  await login(page);
  await page.goto('/stats');
  const bar = page.locator('#srJobBar');
  await expect(bar).toBeVisible();
  await expect(bar).toContainText('DB 최적화 중입니다… · 7/24');

  const toggle = page.getByRole('button', { name: '단계 로그' });
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  const log = page.locator('#srJobLog');
  await expect(log).toBeHidden();

  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(log).toBeVisible();
  await expect(log).toContainText('새 표 생성(스키마 7)');
  await expect(log).toContainText('DB 최적화 시작(대상 24건, 파서 pc-parser-13)');

  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(log).toBeHidden();
});

test('optimize error shows as error, not completed', async ({ page }) => {
  let polls = 0;
  const errLog = [...LOG, '2026-10-17 12:00:04 오류로 멈춤(RuntimeError)'];
  await page.route('**/maintenance/status', (route) => {
    polls += 1;
    if (polls <= 2) {
      return route.fulfill({ json: { active: true, jobs: [optJob('running', 7, LOG)] } });
    }
    return route.fulfill({ json: { active: false, jobs: [optJob('error', 7, errLog)] } });
  });
  await login(page);
  await page.goto('/stats');
  const bar = page.locator('#srJobBar');
  await expect(bar).toContainText('오류');
  await expect(bar).not.toContainText('작업 완료');
});
for (const [theme, width] of [['light', 1440], ['dark', 1440], ['dark', 390]] as const) {
  test(`optimize bar fits and expands (${theme} ${width})`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript((t) => { try { localStorage.setItem('sr-theme', t); } catch (e) {} }, theme);
    await page.route('**/maintenance/status', (route) => route.fulfill({
      json: { active: true, jobs: [optJob('running', 7, LOG)] },
    }));
    await login(page);
    await page.goto('/data/all');
    const bar = page.locator('#srJobBar');
    await expect(bar).toBeVisible();
    const closed = await bar.boundingBox();
    expect(closed!.x + closed!.width).toBeLessThanOrEqual(width);

    await page.getByRole('button', { name: '단계 로그' }).click();
    const log = page.locator('#srJobLog');
    await expect(log).toBeVisible();
    const open = await bar.boundingBox();
    expect(open!.x + open!.width).toBeLessThanOrEqual(width);
    expect(open!.x).toBeGreaterThanOrEqual(0);
    await page.screenshot({ path: testInfo.outputPath(`db-optimize-bar-${theme}-${width}.png`) });
  });
}
