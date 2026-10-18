// 실제 초기 로더를 실행한다. fetch 는 응답 없는 합성 Promise 로 네트워크를 사용하지 않는다.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
for (const [saved, expected] of [[null, 'all'], ['unknown', 'all'], ['constructor', 'all'], ['parking', 'parking'], ['all', 'all']]) {
    const requests = [], elements = {};
    const buttons = ['all', 'traffic', 'parking', 'safety', 'living', 'other'].map(cat => ({
        dataset: {cat}, classList: {toggle() {}}, addEventListener() {}, removeEventListener() {},
        setAttribute(key, value) { this[key] = value; }
    }));
    const sandbox = {
        window: {addEventListener() {}, removeEventListener() {}},
        sessionStorage: {getItem() {return saved;}},
        location: {search: ''}, URLSearchParams, AbortController,
        performance: {mark() {}},
        document: {
            querySelectorAll(selector) {return selector === '.stats-cat-btn' ? buttons : [];},
            getElementById(id) {return elements[id] ||= {};}
        },
        fetch(url) {requests.push(url); return new Promise(() => {});}
    };
    vm.runInNewContext(fs.readFileSync(path.join(root, 'web/static/ui/stats-loader.js'), 'utf8'), sandbox);
    sandbox.window.SrStatsLoader.start({});
    assert.equal(sandbox.window.srPendingStatsCat, expected);
    assert.equal(buttons.find(b => b['aria-pressed'] === 'true').dataset.cat, expected);
    assert.equal(new URL(requests[0], 'https://fixture.invalid').searchParams.get('category'), expected);
    assert.equal(requests[1], '/stats/content');
}
// 완성 화면의 기본값/저장 복원도 실제 초기화 구문으로 확인한다.
const source = fs.readFileSync(path.join(root, 'web/static/ui/stats.js'), 'utf8');
const labels = source.match(/var CAT_LABELS = .*;/)[0];
const initial = source.slice(source.indexOf('var currentCat ='), source.indexOf("if (!TYPE_LABELS[currentType])"));
for (const [saved, expected] of [[null, 'all'], ['unknown', 'all'], ['constructor', 'all'], ['parking', 'parking'], ['all', 'all']]) {
    const sandbox = {window: {}, sessionStorage: {}, storageGet: (_, key) => key === 'stats_cat' ? saved : null};
    vm.runInNewContext(labels + initial, sandbox);
    assert.equal(sandbox.currentCat, expected);
    assert.equal(sandbox.CAT_LABELS.all, '전체');
}
const sandbox = {window: {}, URLSearchParams};
vm.runInNewContext(fs.readFileSync(path.join(root, 'web/static/ui/stats-calc.js'), 'utf8'), sandbox);
assert.equal(sandbox.window.SrStatsCalc.kpiListUrl('all', {}, {filters: {}, year: 'all', dedupeMode: 'raw'}), '/data/all?dedupe=raw');
assert.equal(sandbox.window.SrStatsCalc.mapParams('all', null, {filters: {}, search: ''}).get('category'), 'all');
