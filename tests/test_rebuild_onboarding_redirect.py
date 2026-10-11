"""실제 다시 불러오기 스크립트의 인증 안내 이동과 재시도 중단을 검증한다."""
import pathlib
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'JavaScript 실행에 Node가 필요함')
class RebuildOnboardingRedirectTests(unittest.TestCase):
    def test_gate_redirects_stop_retry_for_get_and_post(self):
        template = pathlib.Path(__file__).resolve().parents[1] / 'web/templates/onboarding_rebuild.html'
        script = template.read_text().split('<script>')[1].split('</script>')[0]
        harness = r'''
const vm = require('node:vm');
const assert = require('node:assert/strict');
const script = process.argv[1];
async function check(state, target, post) {
  const nodes = new Map();
  const timers = new Set();
  let requests = 0;
  const context = {
    document: {getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, {
        classList: {toggle() {}, add() {}}, getAttribute() { return ''; },
        addEventListener(event, handler) { this[event] = handler; },
      });
      return nodes.get(id);
    }},
    window: {location: {href: ''}, addEventListener() {}, confirm() {return true;}},
    setTimeout(fn) {timers.add(fn); return fn;}, clearTimeout(fn) {timers.delete(fn);},
    fetch: async () => {
      requests++;
      if (post && requests === 1) return {
        status: 200, ok: true, json: async () => ({data: {state: 'required'}}),
      };
      return {status: 403, ok: false, json: async () => ({
        code: 'COMMUNITY_ONBOARDING_REQUIRED', gate: {state},
      })};
    },
  };
  vm.runInNewContext(script, context);
  await new Promise(setImmediate);
  if (post) {
    const button = nodes.get('rbStartBtn');
    button.click.call(button);
    await new Promise(setImmediate);
  }
  assert.equal(context.window.location.href, target);
  assert.equal(timers.size, 0, '이동 뒤 자동 재시도 금지');
  assert.equal(requests, post ? 2 : 1);
}
(async () => {
  for (const post of [false, true]) {
    for (const state of ['cloud_unavailable', 'official_account_protocol_required'])
      await check(state, '/onboarding/cloud', post);
    for (const state of ['official_account_mismatch', 'official_account_taken', 'official_account_change_pending'])
      await check(state, '/settings/', post);
    for (const state of ['kakao_reauth_required', 'kakao_required', undefined])
      await check(state, '/onboarding/community?next=/onboarding/rebuild', post);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run(['node', '-e', harness, script], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
