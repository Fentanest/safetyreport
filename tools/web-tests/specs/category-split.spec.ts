import { test, expect, Page } from '@playwright/test';

// 2026-10-11 사용자 지시: '기타' 한 칸을 안전신고·생활불편·기타 세 칸으로 나눈다(5분류).
// 사이드바 조회 메뉴와 통계 분류 세그먼트를 확인한다. 통계 상세표 자료(records_safety_*/records_living_*)는
// Stage 4A 가 집계를 합치기 전까지 비어 있을 수 있다 — 그 경우에도 분류 버튼·빈 표 전환이 깨지지 않는지만 본다.
async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

test('sidebar lists 안전신고·생활불편·기타 조회 and each list loads its rows', async ({ page }) => {
  await login(page);
  await page.goto('/');

  const safetyLink = page.locator('#mainSidebar a[href="/data/safety"]');
  const livingLink = page.locator('#mainSidebar a[href="/data/living"]');
  const otherLink = page.locator('#mainSidebar a[href="/data/other"]');
  await expect(safetyLink).toHaveText(/안전신고 조회/);
  await expect(livingLink).toHaveText(/생활불편 조회/);
  await expect(otherLink).toHaveText(/기타 조회/);

  await safetyLink.click();
  await expect(page).toHaveURL(/\/data\/safety$/);
  await expect(page.locator('#mainSidebar a[href="/data/safety"]')).toHaveClass(/active/);
  await expect(page.locator('body')).toContainText('SPP-2601-9000201'); // fixture 안전신고(도로 파손)

  await page.goto('/data/living');
  await expect(page.locator('#mainSidebar a[href="/data/living"]')).toHaveClass(/active/);
  await expect(page.locator('body')).toContainText('SPP-2608-9000205'); // fixture 생활불편(자전거 방치)

  await page.goto('/data/other');
  await expect(page.locator('#mainSidebar a[href="/data/other"]')).toHaveClass(/active/);
  await expect(page.locator('body')).toContainText('SPP-2609-9000206'); // fixture 기타(접수 메뉴 변경 문의)
  // traffic/parking 건은 /data/other 묶음에 섞이지 않는다
  await expect(page.locator('body')).not.toContainText('SPP-2512-9000001');
});

test('stats category segment has all plus five categories and switches to safety/living without breaking the pane', async ({ page }) => {
  await login(page);
  await page.goto('/stats');
  await expect(page.locator('.stats-pane:visible .dataTables_wrapper')).toHaveCount(1);

  const cats = ['all', 'traffic', 'parking', 'safety', 'living', 'other'];
  await expect(page.locator('#statsCatGroup .stats-cat-btn')).toHaveCount(cats.length);
  for (const cat of cats) {
    await expect(page.locator(`.stats-cat-btn[data-cat="${cat}"]`)).toHaveCount(1);
  }

  await page.locator('.stats-cat-btn[data-cat="safety"]').click();
  await expect(page.locator('.stats-cat-btn[data-cat="safety"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#safety-agency')).toBeVisible();
  await expect(page.locator('#statsOverviewTitle')).toContainText('안전신고');

  await page.locator('.stats-cat-btn[data-cat="living"]').click();
  await expect(page.locator('.stats-cat-btn[data-cat="living"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.stats-cat-btn[data-cat="safety"]')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('#living-agency')).toBeVisible();
  await expect(page.locator('#statsOverviewTitle')).toContainText('생활불편');

  // 기타위반 → 기타로 이름이 바뀌었다
  await expect(page.locator('.stats-cat-btn[data-cat="other"]')).toHaveText(/^\s*기타\s*$/);
});

// 검수용 캡처(assert 없음) — 라이트/다크 사이드바·통계·지도 분류 버튼, 색 대비 눈으로 확인.
test('capture screenshots for review', async ({ page }) => {
  const dir = '/tmp/claude-1000/-home-better0101-projects/35e720c4-b93d-4a30-a02c-511ea7a941cc/scratchpad/s4ui-shots';
  const fs = require('fs');
  fs.mkdirSync(dir, { recursive: true });

  await login(page);
  await page.goto('/data/safety');
  await expect(page.locator('table tbody tr').first()).toBeVisible();
  await page.screenshot({ path: `${dir}/data-safety-light.png`, fullPage: false });
  await page.goto('/data/living');
  await expect(page.locator('table tbody tr').first()).toBeVisible();
  await page.screenshot({ path: `${dir}/data-living-light.png`, fullPage: false });

  await page.goto('/stats');
  await expect(page.locator('.stats-pane:visible .dataTables_wrapper')).toHaveCount(1);
  await page.screenshot({ path: `${dir}/stats-traffic-light.png`, fullPage: false });
  await page.locator('.stats-cat-btn[data-cat="safety"]').click();
  await page.screenshot({ path: `${dir}/stats-safety-light.png`, fullPage: false });
  await page.locator('.stats-cat-btn[data-cat="living"]').click();
  await page.screenshot({ path: `${dir}/stats-living-light.png`, fullPage: false });

  await page.goto('/stats/map');
  await page.screenshot({ path: `${dir}/map-light.png`, fullPage: false });

  await page.goto('/db-editor?category=safety');
  await page.screenshot({ path: `${dir}/db-editor-safety-light.png`, fullPage: false });

  // 다크 테마
  await page.evaluate(() => { document.querySelector('[data-sr-theme="dark"]')?.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
  await page.waitForTimeout(150);
  await page.goto('/stats');
  await expect(page.locator('.stats-pane:visible .dataTables_wrapper')).toHaveCount(1);
  await page.locator('.stats-cat-btn[data-cat="safety"]').click();
  await page.screenshot({ path: `${dir}/stats-safety-dark.png`, fullPage: false });
  await page.locator('.stats-cat-btn[data-cat="living"]').click();
  await page.screenshot({ path: `${dir}/stats-living-dark.png`, fullPage: false });
  await page.goto('/stats/map');
  await page.screenshot({ path: `${dir}/map-dark.png`, fullPage: false });
  await page.goto('/');
  await page.screenshot({ path: `${dir}/sidebar-dark.png`, fullPage: false });
});
