import { test, expect, Page } from '@playwright/test';
import * as fs from 'fs';

// P0 시범: 공통 셸 테마. 캡처는 사용자 검토용 기록이며 golden 이 아니다(승인 후 golden 등록).
const USER = 'fixture-admin';
const PASSWORD = 'fixture-pass-1234';

async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill(USER);
  await page.locator('#password').fill(PASSWORD);
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

for (const theme of ['light', 'dark'] as const) {
  test(`pilot screens render in ${theme} theme without new console errors`, async ({ page }, testInfo) => {
    await page.addInitScript((t) => { try { localStorage.setItem('sr-theme', t); } catch (e) {} }, theme);
    await page.emulateMedia({ reducedMotion: 'reduce' }); // 애니메이션 중간 캡처 방지(테마 CSS 가 reduced motion 을 존중)
    const errors: string[] = [];
    // Firefox 는 페이지 이동으로 중단된 웹폰트 조각 다운로드(NS_BINDING_ABORTED=2152398850)를 오류로 남긴다. 제품 결함이 아니라 제외한다.
    const navigationAbort = (t: string) => t.includes('downloadable font') && t.includes('2152398850');
    page.on('console', (m) => { if (m.type() === 'error' && !navigationAbort(m.text())) errors.push(m.text()); });

    await page.goto('/login');
    await expect(page.locator('html')).toHaveAttribute('data-bs-theme', theme);
    await page.screenshot({ path: testInfo.outputPath(`${theme}-login.png`) });
    await login(page);

    for (const [name, url] of [['dashboard', '/'], ['data-all', '/data/all'], ['stats', '/stats']] as const) {
      await page.goto(url);
      await expect(page.locator('html')).toHaveAttribute('data-bs-theme', theme);
      await expect(page.locator('.sr-theme-switch button[data-sr-theme="' + theme + '"]')).toHaveAttribute('aria-pressed', 'true');
      if (name === 'data-all') {
        // DataTables 는 한국어 파일(CDN)을 받은 뒤 표를 그린다 — 네트워크가 느리면 5초를 넘길 수 있다.
        await expect(page.locator('.dataTables_wrapper')).toHaveCount(1, { timeout: 15_000 });
        await expect(page.locator('table tbody tr').first()).toBeVisible({ timeout: 15_000 });
      }
      if (name === 'dashboard') {
        await page.waitForFunction(() => Array.from(document.querySelectorAll('.progress-bar[data-width]'))
          .every((el) => parseFloat((el as HTMLElement).style.width) === parseFloat(el.getAttribute('data-width') || '')));
      }
      await page.screenshot({ path: testInfo.outputPath(`${theme}-${name}.png`) });
    }
    // 상세 모달
    await page.goto('/data/all');
    const shown = page.evaluate(() => new Promise((resolve) =>
      document.getElementById('reportDetailModal')!.addEventListener('shown.bs.modal', () => resolve(true), { once: true })));
    await page.locator('a.report-detail-link').first().click();
    await shown;
    await expect(page.locator('#reportDetailModal')).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`${theme}-modal.png`) });
    fs.writeFileSync(testInfo.outputPath('console-errors.json'), JSON.stringify(errors, null, 1));
    expect(errors).toEqual([]);
  });
}

test('theme switch persists across reload and does not submit forms', async ({ page }) => {
  await login(page);
  await page.goto('/stats');
  await page.locator('.sr-theme-switch button[data-sr-theme="dark"]').click();
  await expect(page.locator('html')).toHaveAttribute('data-bs-theme', 'dark');
  await expect(page).toHaveURL(/\/stats$/); // 전역 form submit 핸들러에 걸리지 않음
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-bs-theme', 'dark');
  await page.locator('.sr-theme-switch button[data-sr-theme="light"]').click();
  await expect(page.locator('html')).toHaveAttribute('data-bs-theme', 'light');
});

test('narrow viewport keeps sidebar toggle and theme switch reachable', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => { try { localStorage.setItem('sr-theme', 'dark'); } catch (e) {} });
  await login(page);
  await page.goto('/data/all');
  await page.locator('#btnSidebarToggle').click();
  await expect(page.locator('#mainSidebar')).toHaveClass(/sidebar-open/);
  await page.locator('#mainSidebar .sr-theme-switch').scrollIntoViewIfNeeded();
  await expect(page.locator('#mainSidebar .sr-theme-switch')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('dark-390-sidebar.png') });
});

test('on phones the menu button does not cover an open search panel', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
  for (const url of ['/data/all', '/stats']) {
    await page.goto(url);
    await expect(page.locator('#btnSidebarToggle')).toBeVisible();
    await page.locator('.floating-search-btn').click();
    await expect(page.locator('#offcanvasSearch.show')).toHaveCount(1);
    await expect(page.locator('#btnSidebarToggle')).toBeHidden();
    await page.locator('#offcanvasSearch .btn-close').click();
    await expect(page.locator('#offcanvasSearch.show')).toHaveCount(0);
    await expect(page.locator('#btnSidebarToggle')).toBeVisible();
  }
});
