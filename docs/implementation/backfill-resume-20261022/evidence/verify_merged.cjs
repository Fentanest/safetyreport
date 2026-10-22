// 합친 사본(C+E): 게이트 화면(base.html)에서 점검 배너·사용자 조치 안내·공지 팝업과 '보지 않기'를 실제 브라우저로 본다. 합성 자료.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const fs = require('fs');
const out = process.argv[2]; const base = process.argv[3] || 'http://127.0.0.1:18673';
(async () => {
  const b = await chromium.launch(); const report = [];
  for (const [label, viewport] of [['desktop', { width: 1366, height: 900 }], ['phone', { width: 390, height: 844 }]]) {
    const ctx = await b.newContext({ viewport, locale: 'ko-KR', timezoneId: 'Asia/Seoul' }); const p = await ctx.newPage();
    const errors = [], bad = [];
    p.on('console', m => { if (m.type() === 'error') errors.push(m.text()); }); p.on('pageerror', e => errors.push(String(e)));
    p.on('response', r => { if (r.status() >= 400) bad.push(r.status() + ' ' + r.request().method() + ' ' + new URL(r.url()).pathname); });
    await p.goto(base + '/login'); await p.fill('input[name=username]', 'fixture-admin'); await p.fill('input[name=password]', 'fixture-pass-1234');
    await Promise.all([p.waitForNavigation(), p.click('button[type=submit]')]);
    await p.goto(base + '/onboarding/cloud');
    await p.waitForSelector('#srUploadPauseBanner:not([hidden])', { timeout: 10000 });
    const modal = p.locator('#srAnnouncementModal');
    await modal.waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});
    const r = { viewport: label, pause_text: await p.locator('#srUploadPauseText').textContent(),
      notices: await p.locator('#srActionNotices [data-notice-code]').evaluateAll(n => n.map(x => [x.dataset.noticeCode, x.querySelector('.sr-notice-text').textContent, (x.querySelector('a,button')||{}).textContent])),
      modal_visible: await modal.isVisible(),
      modal_title: await p.locator('#srAnnouncementTitle').textContent().catch(() => null),
      modal_body: await p.locator('#srAnnouncementBody').textContent().catch(() => null) };
    await p.screenshot({ path: `${out}/merged-${label}.png`, fullPage: false });
    if (r.modal_visible) {
      const [resp] = await Promise.all([p.waitForResponse(x => /\/api\/announcements\/.+\/snooze$/.test(x.url())), p.locator('#srAnnouncementSnooze').click()]);
      r.snooze_status = resp.status();
      await p.waitForTimeout(600);
      r.modal_after_snooze = await modal.isVisible();
      await p.goto(base + '/onboarding/cloud'); await p.waitForTimeout(1500);
      r.modal_after_reload = await p.locator('#srAnnouncementModal').isVisible().catch(() => false);
      r.pending_api = await p.evaluate(async () => (await (await fetch('/api/announcements/pending', { headers: { Accept: 'application/json' } })).json()));
    }
    r.console_errors = errors; r.http_errors = bad; report.push(r); await ctx.close();
  }
  await b.close(); fs.writeFileSync(`${out}/merged-report.json`, JSON.stringify(report, null, 2)); console.log(JSON.stringify(report, null, 1));
})().catch(e => { console.error(e); process.exit(1); });
