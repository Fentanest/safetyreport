// Deterministic real-script test: seconds repaint locally, foreground/reload do not POST retries.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
function run(gate, pathname = '/stats') {
    let now = 1_000_000, calls = 0;
    const events = {}, moves = [], timers = {};
    const nodes = {srCloudBanner: {hidden: true}, srCloudText: {textContent: ''}};
    const location = {pathname, assign: x => moves.push(x), replace: x => moves.push(x)};
    const context = {
        location, Date: {now: () => now},
        document: {hidden: false, getElementById: id => nodes[id], addEventListener: (n, f) => events[n] = f},
        window: {addEventListener: (n, f) => events[n] = f},
        fetch: async (url, options) => {
            assert.equal(url, '/settings/community/gate');
            assert.equal(options.method, undefined);
            calls++;
            return {ok: true, json: async () => ({data: gate})};
        },
        setInterval: (f, ms) => { timers[ms] = f; return ms; }, clearInterval() {}
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../web/static/ui/official-account-watch.js'), 'utf8'), context);
    return {events, moves, nodes, timers, calls: () => calls, advance: ms => {now += ms;}};
}
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
    const offline = {state: 'cloud_unavailable', can_local: true, can_browse: true,
        cloud: {next_attempt_at: 1300}, upload_pending: 2};
    const page = run(offline); await flush();
    assert.equal(page.nodes.srCloudBanner.hidden, false);
    assert.match(page.nodes.srCloudText.textContent, /05:00 후 다시 확인 · 업로드 대기 중/);
    for (let second = 0; second < 20; second++) {
        page.advance(1000); page.timers[1000](); page.events.visibilitychange();
    }
    await flush();
    assert.equal(page.calls(), 1);
    assert.match(page.nodes.srCloudText.textContent, /04:40/);
    assert.deepEqual(page.moves, []);
    page.advance(10000); page.timers[30000](); await flush();
    assert.equal(page.calls(), 2);
    const empty = run({...offline, upload_pending: 0}); await flush();
    assert.match(empty.nodes.srCloudText.textContent, /업로드 대기 없음/);
    const trapped = run(offline, '/onboarding/cloud'); await flush();
    assert.deepEqual(trapped.moves, ['/']);
    const mismatch = run({state: 'official_account_mismatch'}); await flush();
    assert.deepEqual(mismatch.moves, ['/settings/']);
    const healthy = run({state: 'ok', can_local: true}); await flush();
    assert.equal(healthy.nodes.srCloudBanner.hidden, true);
    assert.doesNotMatch(fs.readFileSync(path.join(__dirname, '../../web/templates/onboarding_cloud.html'), 'utf8'), /id="cloudRetry"/);
    console.log('PASS: local countdown, no per-second network, bounded foreground reads, no trap, pending accuracy, no retry button');
})().catch(error => { console.error(error); process.exitCode = 1; });
