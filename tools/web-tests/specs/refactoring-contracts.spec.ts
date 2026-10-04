import { test, expect, Page } from '@playwright/test';

async function login(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill('fixture-admin');
  await page.locator('#password').fill('fixture-pass-1234');
  await Promise.all([page.waitForURL('**/'), page.getByRole('button', { name: '로그인' }).click()]);
}

function parseCsv(text: string): string[][] {
  const rows: string[][] = []; let row: string[] = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const character = text[i];
    if (character === '"') {
      if (quoted && text[i + 1] === '"') { cell += '"'; i++; }
      else quoted = !quoted;
    } else if (!quoted && character === ',') { row.push(cell); cell = ''; }
    else if (!quoted && character === '\n') {
      row.push(cell.replace(/\r$/, '')); rows.push(row); row = []; cell = '';
    } else cell += character;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

test('101 rows paginate to distinct records and export the complete filtered population', async ({ page }) => {
  await login(page); await page.goto('/data/all');
  await expect(page.locator('#allTable')).toHaveAttribute('aria-busy', 'false');
  await page.evaluate(() => {
    const table = (window as any).jQuery('#allTable').DataTable(), base = table.row(0).data();
    const records = Array.from({ length: 101 }, (_, index) => ({ ...base,
      ID: `PAGE-${String(index).padStart(3, '0')}`, 신고번호: `SPP-PAGE-${String(index).padStart(3, '0')}`,
      신고내용: `CSV, "인용"\n두 번째 줄 ${index}`, 첨부사진: '', 첨부파일: '' }));
    table.clear().rows.add(records).draw();
  });
  await expect(page.locator('#allTable tbody tr')).toHaveCount(50);
  await expect(page.locator('#allTable tbody tr').first()).toContainText('SPP-PAGE-000');
  await page.locator('#allTable_next').click();
  expect(await page.evaluate(() => (window as any).jQuery('#allTable').DataTable().page())).toBe(1);
  await expect(page.locator('#allTable tbody tr').first()).toContainText('SPP-PAGE-050');
  await page.locator('#allTable_next').click();
  await expect(page.locator('#allTable tbody tr')).toHaveCount(1);
  await expect(page.locator('#allTable tbody tr').first()).toContainText('SPP-PAGE-100');
  const downloadPromise = page.waitForEvent('download');
  await page.locator('.btn-export-excel').first().click();
  const download = await downloadPromise, fs = await import('fs');
  const content = fs.readFileSync((await download.path())!, 'utf8');
  expect(content.charCodeAt(0)).toBe(0xfeff);
  const [headers, ...records] = parseCsv(content.slice(1));
  expect(records).toHaveLength(101);
  const idIndex = headers.indexOf('ID'), bodyIndex = headers.indexOf('신고내용');
  expect(idIndex).toBeGreaterThanOrEqual(0); expect(bodyIndex).toBeGreaterThanOrEqual(0);
  records.forEach((record, index) => {
    expect(record).toHaveLength(headers.length);
    expect(record[idIndex]).toBe(`PAGE-${String(index).padStart(3, '0')}`);
    expect(record[bodyIndex]).toBe(`CSV, "인용"\n두 번째 줄 ${index}`);
  });
  await page.locator('.floating-search-btn').click();
  await page.locator('#searchReport').fill('SPP-PAGE-100');
  await page.locator('#btnSearch').click();
  await expect(page.locator('#allTable tbody tr')).toHaveCount(1);
  await page.locator('#offcanvasSearch .btn-close').click();
  await expect(page.locator('#offcanvasSearch')).not.toBeVisible();
  const filteredPromise = page.waitForEvent('download');
  await page.locator('.btn-export-excel').first().click();
  const filtered = parseCsv(fs.readFileSync((await (await filteredPromise).path())!, 'utf8').replace(/^\ufeff/, ''));
  expect(filtered).toHaveLength(2); expect(filtered[1][idIndex]).toBe('PAGE-100');
});

test('device metadata is rendered as text', async ({ page }) => {
  const attack = '<img src=x onerror="window.__metadataInjected=true">';
  await page.route('**/devices/connected-clients', route => route.fulfill({ json: [{ device_name: attack, ip: attack, connected_at: attack, connection_type: 'WebSocket' }] }));
  await login(page); await page.goto('/devices');
  await expect(page.locator('#clientsList')).toContainText(attack);
  expect(await page.evaluate(() => (window as any).__metadataInjected)).toBeUndefined();
  await expect(page.locator('#clientsList img')).toHaveCount(0);
});

test('attachment URLs cannot create markup or script links', async ({ page }) => {
  await login(page);
  await page.evaluate(() => (window as any).showReportDetail({ ID: 'fixture', 신고번호: 'fixture',
    첨부사진: '/static/logo.png" onerror="window.__attachmentInjected=true',
    첨부파일: 'javascript:window.__attachmentInjected=true\n/static/logo.png' }));
  await expect(page.locator('#rdMediaList img')).toHaveCount(2);
  expect(await page.locator('#rdMediaList img').first().getAttribute('onerror')).toBeNull();
  expect(await page.evaluate(() => (window as any).__attachmentInjected)).toBeUndefined();
  expect(await page.locator('#rdMediaList a').evaluateAll(links => links.every(a => !a.getAttribute('href')?.startsWith('javascript:')))).toBe(true);
});

test('date endpoints include the whole day and time endpoints the whole minute', async ({ page }) => {
  await login(page); await page.goto('/data/all');
  const result = await page.evaluate(() => {
    const range = (window as any).SrListPredicates.inRange;
    return [range('2026-10-04 23:59:59.999', '', '2026-10-04', false),
      range('2026-10-05 00:00:00', '', '2026-10-04', false),
      range('23:59:59', '', '23:59', true), range('2026-02-30', '', '2026-12-31', false),
      range(null, '', '2026-12-31', false)];
  });
  expect(result).toEqual([true, false, true, false, false]);
});

test('an upload HTTP failure cannot show success', async ({ page }) => {
  await login(page); await page.goto('/settings');
  await page.route('**/settings/upload_json', route => route.fulfill({ status: 500, json: { detail: 'fixture failure' } }));
  await page.locator('#jsonFile').setInputFiles({ name: 'fixture.json', mimeType: 'application/json', buffer: Buffer.from('{}') });
  await page.evaluate(() => (window as any).uploadJson());
  await expect(page.locator('#jsonUploadStatus')).toHaveClass(/alert-danger/);
});

test('session mutations require a token and reject a foreign origin', async ({ page }) => {
  await login(page); await page.goto('/devices');
  const token = await page.locator('meta[name="csrf-token"]').getAttribute('content');
  expect(token?.length).toBeGreaterThan(31);
  const missing = await page.request.post('/devices/create-key', { data: { name: 'must-not-create' } });
  expect(missing.status()).toBe(403);
  expect((await missing.json()).detail).toBe('csrf_failed');
  const foreign = await page.request.post('/devices/create-key', {
    headers: { 'X-CSRF-Token': token!, Origin: 'https://foreign.invalid' }, data: { name: 'must-not-create' }
  });
  expect(foreign.status()).toBe(403);
  expect((await foreign.json()).reason).toBe('origin');
});

test('draft filter input does not change pagination or export until applied', async ({ page }) => {
  await login(page); await page.goto('/data/all');
  await expect(page.locator('#allTable')).toHaveAttribute('aria-busy', 'false');
  const before = await page.evaluate(() => (window as any).jQuery('#allTable').DataTable().rows({ search: 'applied' }).count());
  await page.locator('.floating-search-btn').click();
  await expect(page.locator('#offcanvasSearch')).toHaveClass(/show/);
  await page.locator('#searchId').fill('no-fixture-id-matches');
  await page.evaluate(() => (window as any).jQuery('#allTable').DataTable().draw());
  const draft = await page.evaluate(() => (window as any).jQuery('#allTable').DataTable().rows({ search: 'applied' }).count());
  expect(draft).toBe(before);
  await page.locator('#btnSearch').click();
  expect(await page.evaluate(() => (window as any).jQuery('#allTable').DataTable().rows({ search: 'applied' }).count())).toBe(0);
});

test('list attachment modal also renders URLs through DOM properties', async ({ page }) => {
  await login(page); await page.goto('/data/all');
  await page.evaluate(() => {
    const button = document.createElement('button');
    button.className = 'view-all-btn'; button.id = 'fixture-attachment'; button.dataset.type = 'photo';
    button.dataset.links = JSON.stringify(['/static/logo.png?x=" onerror="window.__listInjected=true', 'javascript:window.__listInjected=true']);
    document.body.appendChild(button); button.click();
  });
  await expect(page.locator('#attachModal')).toBeVisible();
  await expect(page.locator('#attachModalBody img')).toHaveCount(1);
  expect(await page.locator('#attachModalBody img').getAttribute('onerror')).toBeNull();
  expect(await page.evaluate(() => (window as any).__listInjected)).toBeUndefined();
});

test('statistics initializes each table on its first display and preserves rows', async ({ page }) => {
  await login(page); await page.goto('/stats');
  await expect(page.locator('#statsTabsContent .stats-pane:visible table')).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as any).jQuery.fn.dataTable.tables().length)).toBe(1);
  const categories = await page.locator('.stats-cat-btn').evaluateAll(buttons => buttons.map(button => button.getAttribute('data-cat')));
  const types = await page.locator('.stats-type-btn').evaluateAll(buttons => buttons.map(button => button.getAttribute('data-type')));
  for (const category of categories) {
    await page.locator(`.stats-cat-btn[data-cat="${category}"]`).click();
    for (const type of types) {
      await page.locator(`.stats-type-btn[data-type="${type}"]`).click();
      const id = await page.locator('#statsTabsContent .stats-pane:visible table').getAttribute('id');
      expect(await page.evaluate(id => (window as any).jQuery.fn.dataTable.isDataTable(document.getElementById(id!)), id)).toBe(true);
      await expect(page.locator(`#${id}_wrapper`)).toBeVisible();
    }
  }
  expect(await page.evaluate(() => (window as any).jQuery.fn.dataTable.tables().length)).toBe(categories.length * types.length);
});

test('rating log DOM keeps at most 2000 lines and 256KiB', async ({ page }) => {
  await login(page); await page.goto('/rating');
  await page.evaluate(() => {
    const console = document.getElementById('ratingLogConsole')!;
    const bounded = (window as any).SrBoundedLog(console);
    bounded.append(('가나다라마바사\n').repeat(50000));
  });
  await expect.poll(() => page.locator('#ratingLogConsole').textContent()).not.toBe('');
  const size = await page.locator('#ratingLogConsole').evaluate(element => ({
    bytes: new TextEncoder().encode(element.textContent!).length,
    lines: element.textContent!.split('\n').length, valid: !element.textContent!.includes('�')
  }));
  expect(size.bytes).toBeLessThanOrEqual(256 * 1024);
  expect(size.lines).toBeLessThanOrEqual(2000); expect(size.valid).toBe(true);
});

test('statistics hydration preserves the shell, draft filter and focus', async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/stats/content*', async route => {
    const response = await route.fetch(); await held; await route.fulfill({ response });
  });
  await login(page); await page.goto('/stats');
  await expect(page.locator('#statsLoading')).toBeVisible();
  await page.locator('#statsOpenSearch').click();
  await expect(page.locator('#offcanvasSearch')).toHaveClass(/show/);
  const input = page.locator('#offcanvasSearch input[name="location"]');
  await expect(page.locator('#offcanvasSearch')).not.toHaveClass(/showing/);
  await input.fill('아직 적용하지 않은 조건');
  await input.focus();
  await expect(input).toBeFocused();
  await page.evaluate(() => { (window as any).__statsShell = document.querySelector('.sr-stats-page');
    (window as any).__statsInput = document.querySelector('#offcanvasSearch input[name="location"]'); });
  release();
  await expect(page.locator('.sr-stats-page')).toHaveAttribute('aria-busy', 'false');
  await expect(input).toHaveValue('아직 적용하지 않은 조건');
  await expect(input).toBeFocused();
  expect(await page.evaluate(() => (window as any).__statsShell === document.querySelector('.sr-stats-page') &&
    (window as any).__statsInput === document.querySelector('#offcanvasSearch input[name="location"]'))).toBe(true);
  expect(await page.locator('script[src*="/ui/stats.js"]').count()).toBe(1);
});

test('statistics twenty remounts keep table filters and document handlers bounded', async ({ page }) => {
  await login(page); await page.goto('/stats');
  await expect(page.locator('.sr-stats-page')).toHaveAttribute('aria-busy', 'false');
  const counts = await page.evaluate(() => {
    const w = window as any, $ = w.jQuery;
    const count = () => ({ filters: $.fn.dataTable.ext.search.length, tables: $.fn.dataTable.tables().length,
      handlers: Object.values($._data(document, 'events') || {}).flat().filter((handler: any) => handler.namespace === 'srStats').length,
      scripts: document.querySelectorAll('script[src*="/ui/stats.js"]').length,
      tableHandlers: $('#statsTabsContent table').toArray().reduce((sum: number, table: any) =>
        sum + Object.values($._data(table, 'events') || {}).flat().length, 0) });
    const before = count();
    for (let iteration = 0; iteration < 20; iteration++) w.SrStats.mount();
    const after = count(); w.SrStats.dispose();
    return { before, after, disposed: count() };
  });
  expect(counts.after).toEqual(counts.before);
  expect(counts.disposed.tables).toBe(0);
  expect(counts.disposed.handlers).toBe(0);
  expect(counts.disposed.filters).toBe(counts.before.filters - 1);
});
