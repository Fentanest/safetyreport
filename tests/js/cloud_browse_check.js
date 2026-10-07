// node tests/js/cloud_browse_check.js — real scripts with deterministic browser events.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function run(name, gate, failedFetch = false) {
    const events = {}, moves = [], alerts = [];
    const nodes = {
        cloudRetry: {addEventListener: (name, fn) => { events[name] = fn; }},
        cloudRetryStatus: {},
        mainSettingsForm: {addEventListener: (name, fn) => { events[name] = fn; }, requestSubmit: () => { events.submitted = true; }},
        fld_username: {value: 'fixture', dataset: {originalUsername: 'fixture'}},
        officialAccountConfirm: {}
    };
    const location = {pathname: '/stats', search: '', assign: x => moves.push(x), replace: x => moves.push(x)};
    const context = {
        location, URLSearchParams,
        document: {hidden: false, getElementById: id => nodes[id], querySelector: () => ({content: 'fixture-csrf'}),
            addEventListener: (name, fn) => { events[name] = fn; }},
        window: {location, alert: msg => alerts.push(msg), addEventListener() {}},
        fetch: async () => {
            if (failedFetch) throw new Error('fixture network failure');
            return {ok: true, json: async () => ({data: gate})};
        },
        setInterval() {}, clearInterval() {}, setTimeout() {}, clearTimeout() {}
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../web/static/ui/', name), 'utf8'), context);
    return {events, moves, alerts};
}
async function flush() { await new Promise(resolve => setImmediate(resolve)); }
(async () => {
    const offline = {state: 'cloud_unavailable', can_enter: false, can_browse: true};
    for (const [gate, expected] of [
        [offline, []],
        [{...offline, can_browse: false}, ['/onboarding/cloud']],
        [{state: 'official_account_mismatch', can_browse: false}, ['/settings/']],
    ]) {
        const page = run('official-account-watch.js', gate);
        page.events.visibilitychange(); await flush();
        assert.deepEqual(page.moves, expected);
    }
    const retry = run('official-account-retry.js', offline);
    retry.events.click(); await flush();
    assert.deepEqual(retry.moves, ['/']);
    for (const failed of [false, true]) {
        const settings = run('official-account-settings.js', offline, failed);
        await settings.events.submit({preventDefault() {}});
        assert.deepEqual(settings.moves, []);
        assert.equal(settings.alerts.length, 1);
        assert.equal(settings.events.submitted, undefined);
    }
    console.log('PASS: offline browsing, protected-account redirect, retry escape, settings stay visible');
})().catch(error => { console.error(error); process.exitCode = 1; });
