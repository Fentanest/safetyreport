import { test, expect, Page } from '@playwright/test';

// 통계 행 클릭 → 목록 드릴다운 (statistics-spec D-STAT-8). 기관명의 & # + 와 '법규 없음'이 그대로 전달돼야 한다.
async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

async function setAgency(page: Page, value: string) {
  await page.goto('/db-editor/traffic/90000002');
  await page.locator('input[name="처리기관"]').fill(value);
  await Promise.all([page.waitForURL('**/db-editor?category=traffic'), page.getByRole('button', { name: '저장' }).click()]);
}

test('agency with & # + drills down to exactly its reports', async ({ page }) => {
  await login(page);
  await page.goto('/db-editor/traffic/90000002');
  const original = await page.locator('input[name="처리기관"]').inputValue();
  const special = 'A&B #1 시험구청+분소';
  await setAgency(page, special);
  try {
    await page.addInitScript(() => { sessionStorage.setItem('stats_cat', 'traffic'); sessionStorage.setItem('stats_type', 'agency'); });
    await page.goto('/stats');
    await expect(page.locator('#traffic-agency .dataTables_wrapper')).toHaveCount(1);
    await Promise.all([
      page.waitForURL('**/data/traffic?**'),
      page.locator('#traffic-agency tbody tr', { hasText: special }).first().click(),
    ]);
    const params = new URL(page.url()).searchParams;
    expect(params.get('agency')).toBe(special);
    await expect(page.locator('.dataTables_wrapper')).toHaveCount(1);
    await expect(page.locator('table.dataTable tbody tr', { hasText: '90000002' })).toHaveCount(1);
  } finally {
    await setAgency(page, original);
  }
});

test('reports without a law drill down to a non-empty list', async ({ page }) => {
  await login(page);
  await page.goto('/data/traffic?law=__없음__');
  await expect(page.locator('.dataTables_wrapper')).toHaveCount(1);
  const rows = page.locator('table.dataTable tbody tr');
  await expect(rows.first()).not.toHaveClass(/dataTables_empty/);
  expect(await page.locator('td.dataTables_empty').count()).toBe(0);
});
