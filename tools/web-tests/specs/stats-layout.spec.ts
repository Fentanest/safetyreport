import { test, expect, Page } from '@playwright/test';

// 통계 표는 PC 폭(1280 이상)에서 좌우 스크롤 없이 들어와야 한다(사용자 요청 2026-09-24).
// 건수+비율을 한 칸으로 합치고 위반법규 필터를 표 위로 옮긴 구조를 지킨다.
async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

for (const width of [1280, 1440]) {
  test(`stats tables fit without horizontal scroll at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await login(page);
    await page.goto('/stats');
    for (const type of ['agency', 'person']) {
      await page.locator(`.stats-type-btn[data-type="${type}"]`).first().click();
      const table = page.locator('.stats-pane:visible table').first();
      await expect(table).toBeVisible();
      // DataTables 는 한국어 파일을 비동기로 받은 뒤 표를 감싼다 — 초기화가 끝난 뒤에 잰다.
      await expect(page.locator('.stats-pane:visible .dataTables_wrapper')).toHaveCount(1);
      const { tableW, boxW, headCells, footCells } = await table.evaluate((t: HTMLTableElement) => ({
        tableW: t.scrollWidth,
        boxW: (t.closest('.dataTables_wrapper') as HTMLElement).clientWidth,
        headCells: t.querySelectorAll('thead th').length,
        footCells: t.querySelectorAll('tfoot td').length,
      }));
      expect(tableW, `${type} table width`).toBeLessThanOrEqual(boxW + 1);
      expect(footCells, `${type} footer cells match header`).toBe(headCells);
    }
  });
}
