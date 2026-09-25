import { test, expect, Page } from '@playwright/test';

// 별점 공통 사유(G16 W2): 입력칸·글자 수(코드포인트)·상한 차단·요청 본문의 cause.
const USER = 'fixture-admin';
const PASSWORD = 'fixture-pass-1234';

async function login(page: Page) {
  await page.goto('/login');
  if (await page.locator('#username').isVisible()) {
    await page.locator('#username').fill(USER);
    await page.locator('#password').fill(PASSWORD);
    await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
  }
}

async function openRating(page: Page) {
  await login(page);
  await page.goto('/rating');
  await page.evaluate(() => { (document.getElementById('phoneNumberSet') as HTMLInputElement).value = '01000000000'; });
  await page.locator('#ratingIds').fill('SPP-2601-9000001');
}

test('cause counter counts code points and blocks over the limit', async ({ page }) => {
  await openRating(page);
  const posts: string[] = [];
  await page.route('**/rating/start', route => { posts.push(route.request().postData() || ''); return route.fulfill({ json: { status: 'success', message: 'ok' } }); });

  await page.locator('#ratingCause').fill('😀😀 감사');
  await expect(page.locator('#ratingCauseCount')).toHaveText('5 / 1000자');

  await page.locator('#ratingCause').fill('가'.repeat(1001));
  await expect(page.locator('#ratingCauseCount')).toHaveClass(/text-danger/);
  const messages: string[] = [];
  page.on('dialog', d => { messages.push(d.message()); d.accept(); });
  await page.locator('#btnStartRating').click();
  await expect.poll(() => messages.length).toBe(1);
  expect(messages[0]).toContain('1000자까지');
  expect(posts).toHaveLength(0);
});

test('submit sends the trimmed cause with ids and score', async ({ page }) => {
  await openRating(page);
  let body = '';
  await page.route('**/rating/start', route => { body = route.request().postData() || ''; return route.fulfill({ json: { status: 'success', message: 'ok' } }); });
  const messages: string[] = [];
  page.on('dialog', d => { messages.push(d.message()); d.accept(); });

  await page.locator('#ratingScore').selectOption('4');
  await page.locator('#ratingCause').fill('  신속한 처리 감사합니다  ');
  await page.locator('#btnStartRating').click();
  await expect.poll(() => body).not.toBe('');
  const params = new URLSearchParams(body);
  expect(params.get('cause')).toBe('신속한 처리 감사합니다');
  expect(params.get('score')).toBe('4');
  expect(params.get('ids')).toBe('SPP-2601-9000001');
  expect(messages[0]).toContain('공통 사유도 함께 제출합니다');
});

test('empty cause still submits (old behaviour)', async ({ page }) => {
  await openRating(page);
  let body = '';
  await page.route('**/rating/start', route => { body = route.request().postData() || ''; return route.fulfill({ json: { status: 'success', message: 'ok' } }); });
  page.on('dialog', d => d.accept());
  await page.locator('#btnStartRating').click();
  await expect.poll(() => body).not.toBe('');
  expect(new URLSearchParams(body).get('cause')).toBe('');
});
