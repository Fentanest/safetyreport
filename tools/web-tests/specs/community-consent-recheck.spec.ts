import { test, expect } from '@playwright/test';
import { execFileSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';

// 서버 재시작 직후 게이트가 '확인 중'(verification_required)을 먼저 돌려줘도, 온보딩 동의 카드가 스스로 다시 읽어
// 새 동의문을 펼친다(2026-10-21: 예전엔 '상태 다시 확인'을 눌러야 보였다). 실제 템플릿을 Jinja 로 렌더해 쓴다.
const ROOT = path.resolve(__dirname, '../../..');

function renderCard(): string {
  const py = [
    'import jinja2, sys',
    "env = jinja2.Environment(loader=jinja2.FileSystemLoader(sys.argv[1]))",
    "class S: app_version = 'test'",
    "class R: state = S()",
    "print(env.get_template('components/community_consent_card.html').render(cm_mode='onboarding', request=R()))",
  ].join('\n');
  const python = fs.existsSync(path.join(ROOT, '.venv/bin/python')) ? path.join(ROOT, '.venv/bin/python') : 'python3';
  return execFileSync(python, ['-c', py, path.join(ROOT, 'web/templates')], { encoding: 'utf8' });
}

test('onboarding consent card re-checks a pending gate and opens the new consent text without reload', async ({ page }) => {
  const markdown = fs.readFileSync(path.join(ROOT, 'web/static/ui/community_consent_markdown.js'), 'utf8');
  let gateCalls = 0;
  await page.route('**/static/ui/community_consent_markdown.js*', (r) => r.fulfill({ contentType: 'application/javascript', body: markdown }));
  await page.route('**/settings/community/gate', (r) => {
    gateCalls += 1;
    // 첫 읽기와 카카오 연결 신호 뒤 읽기 모두 '확인 중'(서버 재시작 직후 15초 재시도 간격 안) — 그 뒤에야 결과가 나온다.
    const state = gateCalls <= 2 ? 'verification_required' : 'consent_required';
    r.fulfill({ contentType: 'application/json', body: JSON.stringify({ data: { state, can_enter: false, consent: { state: gateCalls <= 2 ? null : 'outdated' } } }) });
  });
  await page.route('**/settings/community/policy', (r) => r.fulfill({ contentType: 'application/json',
    body: JSON.stringify({ data: { policy_version: '2026-10-21.1', consent_text_sha256: 'a'.repeat(64), text: '# [필수] 신고내용 공유 동의\n새 문서' } }) }));
  await page.route('http://card.test/', (r) => r.fulfill({ contentType: 'text/html; charset=utf-8', body: '<!doctype html><html><head><meta charset="utf-8"><style>.d-none{display:none!important}</style></head><body>' + renderCard() + '</body></html>' }));
  await page.goto('http://card.test/');
  await page.evaluate(() => document.dispatchEvent(new CustomEvent('sr:community-status', { detail: { state: 'connected' } })));

  await expect(page.locator('#ccPanelAsk')).toBeHidden();
  await expect(page.locator('#ccPanelAsk')).toBeVisible({ timeout: 10000 });
  await expect(page.locator('#ccOutdatedNote')).toBeVisible();
  await expect(page.locator('#ccPolicyVersion')).toHaveText('2026-10-21.1');
  expect(gateCalls).toBeGreaterThanOrEqual(3);
});
