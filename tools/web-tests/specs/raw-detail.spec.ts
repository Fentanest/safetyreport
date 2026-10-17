import { test, expect, Page } from '@playwright/test';

// 상세 원본 보기(2026-10-17 명세 4절): 신고 상세 모달 footer "원본 응답" → 별도 모달.
// fixture 시드: 90000001(SPP-2512-9000001)은 원본 있음, 90000002(SPP-2601-9000002)는 없음.
async function login(page: Page) {
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

async function openDetailFor(page: Page, reportNumber: string) {
  await page.goto('/data/all');
  const row = page.locator('#allTable tbody tr', { hasText: reportNumber }).first();
  await expect(row).toBeVisible();
  await row.locator('a.report-detail-link').click();
  await expect(page.locator('#reportDetailModal')).toBeVisible();
}

async function openRawModal(page: Page, reportNumber: string) {
  await openDetailFor(page, reportNumber);
  await page.getByRole('button', { name: '원본 응답' }).click();
  const modal = page.locator('#rawDetailModal');
  await expect(modal).toBeVisible();
  return modal;
}

test('raw modal shows stored JSON without reporter keys, then search/copy/download', async ({ page }, testInfo) => {
  await login(page);
  const modal = await openRawModal(page, 'SPP-2512-9000001');
  const pre = page.locator('#rawDetailPre');
  await expect(pre).toBeVisible();
  const text = await page.locator('#rawDetailCode').textContent();
  expect(text).toContain('C_A_CONTENTS');
  expect(text).toContain('신호위반 신고 본문 90000001');
  // 신고자 키 5개는 저장 전에 빠져 있다
  for (const key of ['"C_NAME"', '"C_EMAIL"', '"C_PHONE"', '"C_PHONE2"', '"C_ID"']) {
    expect(text).not.toContain(key);
  }
  // 담당자 연락처·본문은 그대로 둔다
  expect(text).toContain('02-000-0000');

  // 검색: 일치 강조·건수 (모달 범위로 좁혀 목록의 동명 버튼과 섞이지 않게 한다)
  await page.getByLabel('원본 JSON 검색').fill('VHRNO');
  await expect(page.getByLabel('원본 JSON 검색')).toHaveValue('VHRNO');
  await expect(page.locator('#rawDetailCode mark')).toHaveCount(1);
  await expect(page.locator('#rawDetailCount')).toContainText('1/1건');
  await modal.getByRole('button', { name: '다음' }).click();
  await expect(page.locator('#rawDetailCount')).toContainText('1/1건');
  await modal.getByRole('button', { name: '이전' }).click();
  await expect(page.locator('#rawDetailCode mark.rd-raw-current')).toHaveCount(1);
  const search = page.getByLabel('원본 JSON 검색');
  await search.click();
  await page.keyboard.press('ControlOrMeta+a');
  await page.keyboard.press('Backspace');
  await search.pressSequentially('없는검색어-xyz');
  await expect(page.locator('#rawDetailCount')).toContainText('0건');
  await expect(page.locator('#rawDetailCode mark')).toHaveCount(0);

  // 복사: 클립보드에 저장 원본 그대로 들어간다
  await search.click();
  await page.keyboard.press('ControlOrMeta+a');
  await page.keyboard.press('Backspace');
  await expect(search).toHaveValue('');
  await modal.getByRole('button', { name: '복사' }).click();
  await expect(modal.getByRole('button', { name: '복사됨' })).toBeVisible();
  const pasted = await page.evaluate(() => navigator.clipboard.readText());
  expect(pasted).toContain('C_A_CONTENTS');
  expect(pasted).not.toContain('"C_NAME"');

  // 다운로드: <ID>.json
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: /90000001\.json 내려받기/ }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('90000001.json');

  // 파일로 다시 쓰기
  await page.getByRole('button', { name: '파일로 다시 쓰기' }).click();
  await expect(page.locator('#rawDetailNotice')).toContainText('파일로 다시 썼습니다');
  await expect(modal).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('raw-detail-modal.png') });
});

test('raw modal shows the missing notice when there is no stored envelope', async ({ page }) => {
  await login(page);
  await openRawModal(page, 'SPP-2601-9000002');
  await expect(page.locator('#rawDetailPre')).toBeHidden();
  await expect(page.locator('#rawDetailNotice')).toContainText('아직 원본이 없습니다(DB 최적화가 채웁니다)');
});

test('closing the raw modal returns to the detail modal', async ({ page }) => {
  await login(page);
  await openDetailFor(page, 'SPP-2512-9000001');
  await page.getByRole('button', { name: '원본 응답' }).click();
  await expect(page.locator('#rawDetailModal')).toBeVisible();
  // 원본을 보는 동안 상세 모달은 숨는다(두 모달의 포커스 트랩 충돌 방지)
  await expect(page.locator('#reportDetailModal')).toBeHidden();
  await page.locator('#rawDetailModal .modal-footer button').click();
  await expect(page.locator('#rawDetailModal')).toBeHidden();
  await expect(page.locator('#reportDetailModal')).toBeVisible();
  await expect(page.getByRole('button', { name: '원본 응답' })).toBeVisible();
});

for (const [theme, width] of [['light', 1440], ['dark', 1440], ['light', 390], ['dark', 390]] as const) {
  test(`raw modal fits and keeps accessible names (${theme} ${width})`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript((t) => { try { localStorage.setItem('sr-theme', t); } catch (e) {} }, theme);
    await login(page);
    const modal = await openRawModal(page, 'SPP-2512-9000001');
    await expect(page.locator('#rawDetailPre')).toBeVisible();
    // 접근성 이름: 검색 입력·대화상자 제목·원본 모달 동작 버튼
    // (원본을 보는 동안 상세 모달은 숨으므로 그 버튼은 가려진다)
    await expect(page.getByLabel('원본 JSON 검색')).toBeVisible();
    await expect(modal.getByRole('button', { name: '파일로 다시 쓰기' })).toBeVisible();
    await expect(modal).toHaveAttribute('aria-labelledby', 'rawDetailTitle');
    const box = await modal.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    await page.screenshot({ path: testInfo.outputPath(`raw-detail-${theme}-${width}.png`) });
  });
}
