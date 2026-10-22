// C 브라우저 증거: 필수 버전 잠금 화면(18671)·업로드 중지 배너(18672). fixture 서버, 합성 자료.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const fs = require('fs');
const out = process.argv[2];
const apiKeyLock = process.argv[3];
fs.mkdirSync(out, { recursive: true });
const report = { cases: [] };
async function login(page, base) {
  await page.goto(base + '/login');
  await page.fill('input[name=username]', 'fixture-admin');
  await page.fill('input[name=password]', 'fixture-pass-1234');
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);
}
(async () => {
  const browser = await chromium.launch();
  for (const [label, viewport] of [['desktop', { width: 1366, height: 900 }], ['phone', { width: 390, height: 844 }]]) {
    // 1) 잠금 화면
    {
      const ctx = await browser.newContext({ viewport, locale: 'ko-KR', timezoneId: 'Asia/Seoul' });
      const page = await ctx.newPage();
      const errors = []; const external = [];
      page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
      page.on('pageerror', e => errors.push(String(e)));
      page.on('request', r => { const u = new URL(r.url()); if (!['127.0.0.1'].includes(u.hostname) && !/jsdelivr|jquery|datatables|cdnjs|fonts/.test(u.hostname)) external.push(r.url()); });
      const base = 'http://127.0.0.1:18671';
      await login(page, base);
      const results = {};
      for (const path of ['/', '/data/all', '/settings/', '/crawl/']) {
        await page.goto(base + path);
        results[path] = { title: await page.locator('h1').first().textContent(), url: page.url() };
      }
      const msg = await page.locator('#srUpdateLockMessage').textContent();
      const html = await page.locator('#srUpdateLockMessage').innerHTML();
      const button = await page.locator('#srUpdateLockButton');
      const href = await button.getAttribute('href');
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      // 버튼은 새 탭으로 연다(실제 이동은 하지 않는다)
      const target = await button.getAttribute('target');
      const post = await page.evaluate(async () => {
        const token = document.querySelector('meta[name=csrf-token]')?.content || '';
        const r = await fetch('/crawl/start', { method: 'POST', headers: { 'Accept': 'application/json', 'x-csrf-token': token } });
        return { status: r.status, body: await r.json() };
      });
      const gate = await page.evaluate(async () => (await (await fetch('/settings/community/gate', { headers: { Accept: 'application/json' } })).json()).data.update_lock);
      await page.goto(base + '/');
      await page.screenshot({ path: `${out}/lock-${label}.png`, fullPage: true });
      report.cases.push({ case: 'update_lock', viewport: label, results, message_text: msg, message_html: html, href, target, overflow, post, gate, console_errors: errors, external_requests: external });
      await ctx.close();
    }
    // 2) 업로드 중지 배너
    {
      const ctx = await browser.newContext({ viewport, locale: 'ko-KR', timezoneId: 'Asia/Seoul' });
      const page = await ctx.newPage();
      const errors = [];
      page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
      page.on('pageerror', e => errors.push(String(e)));
      const base = 'http://127.0.0.1:18672';
      await login(page, base);
      await page.goto(base + '/onboarding/cloud');
      await page.waitForSelector('#srUploadPauseBanner:not([hidden])', { timeout: 10000 });
      const text = await page.locator('#srUploadPauseText').textContent();
      const message = await page.locator('#srUploadPauseMessage').textContent();
      const messageHtml = await page.locator('#srUploadPauseMessage').innerHTML();
      const scripts = await page.locator('#srUploadPauseBanner script').count();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      await page.locator('#srUploadPauseBanner').scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${out}/pause-${label}.png`, fullPage: false });
      report.cases.push({ case: 'upload_pause', viewport: label, url: page.url(), text, message, message_html: messageHtml, script_nodes: scripts, overflow, console_errors: errors });
      await ctx.close();
    }
  }
  // 3) Client API(실제 앱): 잠금 중 409 SERVER_UPGRADE_REQUIRED
  {
    const ctx = await browser.newContext();
    const headers = { 'X-API-Key': apiKeyLock, 'X-SafetyReport-Client': 'mobile', 'X-SafetyReport-Version': '2.5.1+180', 'X-SafetyReport-Protocol': '3' };
    const gate = await ctx.request.get('http://127.0.0.1:18671/api/v1/community/gate', { headers });
    const version = await ctx.request.get('http://127.0.0.1:18671/api/v1/server/version', { headers });
    report.cases.push({ case: 'client_api', gate_status: gate.status(), gate_body: await gate.json(), version_status: version.status() });
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(`${out}/report.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 1));
})().catch(e => { console.error(e); process.exit(1); });
