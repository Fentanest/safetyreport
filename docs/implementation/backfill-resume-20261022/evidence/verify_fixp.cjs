// FIX-P 브라우저 증거(D3): 필수 버전 잠금 중 카카오 로그인 복구 경로와, 업데이트한 버전에서 저장된 잠금이 풀리는지. fixture 서버, 합성 자료.
// node verify_fixp.cjs <out> <locked-base> <upgraded-base>
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const fs = require('fs');
const [out, lockedBase, upgradedBase] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });
async function login(page, base) {
  await page.goto(base + '/login');
  await page.fill('input[name=username]', 'fixture-admin');
  await page.fill('input[name=password]', 'fixture-pass-1234');
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);
}
(async () => {
  const browser = await chromium.launch(); const report = [];
  for (const [label, viewport] of [['desktop', { width: 1366, height: 900 }], ['phone', { width: 390, height: 844 }]]) {
    const ctx = await browser.newContext({ viewport, locale: 'ko-KR', timezoneId: 'Asia/Seoul' }); const page = await ctx.newPage();
    const errors = [], bad = [], posts = [], external = [];
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', e => errors.push(String(e)));
    page.on('request', r => { const u = new URL(r.url()); if (u.hostname !== '127.0.0.1' && !/jsdelivr|jquery|datatables|cdnjs|fonts|kakao/.test(u.hostname)) external.push(r.url()); });
    page.on('response', async r => {
      const u = new URL(r.url());
      if (r.status() >= 400) bad.push(r.status() + ' ' + r.request().method() + ' ' + u.pathname);
      if (r.request().method() === 'POST' && u.pathname.startsWith('/settings/community/')) {
        let body = null; try { body = await r.json(); } catch (e) {}
        posts.push({ path: u.pathname, status: r.status(), code: body && (body.code || body.detail) });
      }
    });
    const r = { viewport: label };
    await login(page, lockedBase);
    // 1) 잠금 화면: 업데이트 버튼과 [카카오 다시 로그인]
    await page.goto(lockedBase + '/');
    r.lock_page = { title: await page.locator('h1').first().textContent(), kakao_href: await page.locator('#srUpdateLockKakao').getAttribute('href'),
                    message_html: await page.locator('#srUpdateLockMessage').innerHTML() };
    await page.screenshot({ path: `${out}/fixp-lock-${label}.png`, fullPage: true });
    // 2) [카카오 다시 로그인] → 설정(세션이 없으면 게이트가 필수 설정 화면으로 보낸다) — 잠금 화면이 아니어야 한다
    await Promise.all([page.waitForNavigation(), page.click('#srUpdateLockKakao')]);
    await page.waitForSelector('#communityCard', { timeout: 10000 });
    await page.waitForFunction(() => { const l = document.getElementById('cmLoading'); return !l || l.classList.contains('d-none') || l.hidden; }, null, { timeout: 10000 }).catch(() => {});
    r.recovery_page = { url: page.url(), lock_screen: await page.locator('#srUpdateLock').count(), banner: await page.locator('#srUpdateLockBanner').count(),
                        banner_text: (await page.locator('#srUpdateLockBanner').textContent().catch(() => '')).replace(/\s+/g, ' ').trim(),
                        state_badge: await page.locator('#cmStateBadge').textContent() };
    // 3) 카카오 로그인 버튼(보이는 첫 버튼)을 눌러 /settings/community/start 가 잠금 409 가 아닌 라우트 응답을 받는지
    const visible = await page.locator('#communityCard button:visible').evaluateAll(b => b.map(x => [x.id, x.textContent.trim()]));
    r.card_buttons = visible;
    const startBtn = page.locator('#communityCard button:visible', { hasText: /카카오|로그인/ }).first();
    if (await startBtn.count()) {
      const [resp] = await Promise.all([page.waitForResponse(x => new URL(x.url()).pathname === '/settings/community/start', { timeout: 15000 }), startBtn.click()]);
      let body = null; try { body = await resp.json(); } catch (e) {}
      r.start = { status: resp.status(), code: body && (body.code || body.detail), not_update_lock: !(body && (body.code === 'COMMUNITY_UPDATE_REQUIRED' || body.detail === 'COMMUNITY_UPDATE_REQUIRED')) };
      await page.waitForTimeout(800);
      r.after_start_alert = (await page.locator('#cmAlert').textContent().catch(() => '')).trim();
    }
    await page.screenshot({ path: `${out}/fixp-recovery-${label}.png`, fullPage: false });
    // 4) 잠금 중에도 막혀야 하는 것: 크롤 시작·설정 저장(POST)
    r.still_blocked = await page.evaluate(async () => {
      const token = document.getElementById('communityCard').dataset.csrf;
      const out = {};
      for (const p of ['/crawl/start', '/settings/community/consent']) {
        const x = await fetch(p, { method: 'POST', headers: { 'Accept': 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': token }, body: '{}' });
        let b = null; try { b = await x.json(); } catch (e) {}
        out[p] = [x.status, b && b.code];
      }
      return out;
    });
    r.overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    // 5) 업데이트한 PC(저장된 잠금 min_version ≤ 지금 버전): status 없이 원래 화면
    await login(page, upgradedBase);
    await page.goto(upgradedBase + '/');
    r.upgraded = { url: page.url(), lock_screen: await page.locator('#srUpdateLock').count(),
                   gate_update_lock: await page.evaluate(async () => (await (await fetch('/settings/community/gate', { headers: { Accept: 'application/json' } })).json()).data.update_lock) };
    r.console_errors = errors; r.http_errors = bad; r.community_posts = posts; r.external_requests = external;
    report.push(r); await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(`${out}/fixp-report.json`, JSON.stringify(report, null, 2)); console.log(JSON.stringify(report, null, 1));
})().catch(e => { console.error(e); process.exit(1); });
