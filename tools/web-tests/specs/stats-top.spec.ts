import { test, expect, Page } from '@playwright/test';

// 통계 상단(P2): 조건 요약, 세그먼트 탭(aria-pressed), 법규 칩, 접는 열 선택 패널.
async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

async function ready(page: Page) {
  await expect(page.locator('.stats-pane:visible .dataTables_wrapper')).toHaveCount(1);
}

test('filter summary lists the active conditions and offers a reset', async ({ page }) => {
  await login(page);
  await page.goto('/stats?agency=' + encodeURIComponent('강서') + '&year=2026');
  await ready(page);
  const summary = page.locator('#statsFilterSummary');
  await expect(summary).toContainText('답변 연도');
  await expect(summary).toContainText('2026');
  await expect(summary).toContainText('처리기관');
  await expect(summary).toContainText('강서');
  await expect(summary.getByRole('link', { name: '조건 초기화' })).toHaveAttribute('href', '/stats');

  await page.goto('/stats');
  await ready(page);
  await expect(page.locator('#statsFilterSummary').getByRole('link', { name: '조건 초기화' })).toHaveCount(0);
});

test('category and grouping tabs mark the selection and survive a reload', async ({ page }) => {
  await login(page);
  await page.goto('/stats');
  await ready(page);
  await page.locator('.stats-cat-btn[data-cat="parking"]').click();
  await page.locator('.stats-type-btn[data-type="person"]').click();
  await expect(page.locator('.stats-cat-btn[data-cat="parking"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.stats-cat-btn[data-cat="traffic"]')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('#parking-person')).toBeVisible();
  await page.reload();
  await ready(page);
  await expect(page.locator('.stats-type-btn[data-type="person"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#parking-person')).toBeVisible();
  await expect(page.locator('.stats-year-btn[data-year="all"]')).toHaveAttribute('aria-pressed', 'true');
});

test('law chip shows the active law', async ({ page }) => {
  await login(page);
  await page.goto('/stats?law=' + encodeURIComponent('도로교통법 제5조'));
  await ready(page);
  await expect(page.locator('#statsLawSidebar button.active')).toHaveText('도로교통법 제5조');
  await expect(page.locator('#statsLawSidebar button', { hasText: '전체' })).toHaveAttribute('aria-pressed', 'false');
});

test('column panel is collapsed by default and hiding a column updates the count', async ({ page }) => {
  await login(page);
  await page.goto('/stats');
  await ready(page);
  const toggle = page.locator('#statsColumnsToggle');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('#statsColumnBody')).toBeHidden();
  await expect(page.locator('#statsColumnCount')).toHaveText('(13/13)');
  await toggle.click();
  await expect(page.locator('#statsColumnBody')).toBeVisible();
  await page.locator('.stats-column-checkbox[data-column-key="별점"]').uncheck();
  await expect(page.locator('#statsColumnCount')).toHaveText('(12/13)');
  await expect(page.locator('.stats-pane:visible thead th', { hasText: '★' })).toHaveCount(0);
  await page.locator('#statsColumnsSelectAll').click();
  await expect(page.locator('#statsColumnCount')).toHaveText('(13/13)');
});

test('summary cards match the mobile overview API for each category', async ({ page, request }) => {
  const fs = await import('fs');
  const path = await import('path');
  const { fixtureDataDir } = await import('../playwright.config');
  const key = fs.readFileSync(path.join(fixtureDataDir, 'fixture-api-key.txt'), 'utf-8').trim();
  const api = (await (await request.get('/api/v1/stats/overview', { headers: { 'X-API-Key': key } })).json()).data;

  await login(page);
  await page.goto('/stats');
  await ready(page);
  for (const cat of ['traffic', 'parking', 'other']) {
    await page.locator(`.stats-cat-btn[data-cat="${cat}"]`).click();
    const s = api[cat];
    const box = page.locator('#statsOverview');
    await expect(box.locator('[data-v="total"]')).toHaveText(`${s.total}건`);
    await expect(box.locator('[data-v="completed"]')).toHaveText(`${s.completed}건`);
    await expect(box.locator('[data-v="processing"]')).toHaveText(`${s.processing}건`);
    await expect(box.locator('[data-v="avg"]')).toHaveText(s.avg_days == null ? '—' : `${s.avg_days.toFixed(1)}일`);
    const bars = await box.locator('rect.sr-bar-reported').count();
    expect(bars).toBeGreaterThanOrEqual(s.monthly_reported.length);
  }
});
