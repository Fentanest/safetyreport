// 웹 목록 검색(web/static/ui/list-predicates.js)이 contracts/report-filter-vectors.json 과 같은 행을 고르는지(EO R-02).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const rootDir = path.resolve(__dirname, '../..');
const sandbox = { window: {} };
for (const file of ['web/static/ui/report-policy.js', 'web/static/ui/list-predicates.js']) {
    vm.runInNewContext(fs.readFileSync(path.join(rootDir, file), 'utf8'), sandbox);
}
const P = sandbox.window.SrListPredicates;
const vectors = JSON.parse(fs.readFileSync(path.join(rootDir, 'contracts/report-filter-vectors.json'), 'utf8'));

const TEXT = { reportName: '신고명', location: '위반장소', agency: '처리기관' };
const RANGES = { 신고일: 'reportDate', 답변일: 'responseDate', 발생일자: 'occurDate', 발생시각: 'occurTime' };

function searchOf(filters) {
    return {
        text: Object.entries(TEXT).filter(([key]) => filters[key]).map(([key, field]) => ({ field, groups: P.parseGroups(filters[key]) })),
        ranges: P.RANGE_FIELDS.map((field) => ({
            min: filters[RANGES[field] + 'Start'] || '', max: filters[RANGES[field] + 'End'] || '', time: field === '발생시각',
        })),
        statuses: filters.statuses || [], ratings: [], poll: '',
        excludePolice: !!filters.excludePolice, onlyPolice: !!filters.onlyPolice,
    };
}

let checked = 0;
for (const c of vectors.cases) {
    if (!c.targets.includes('web_list')) continue;
    const search = searchOf(c.filters);
    const got = vectors.rows.map((row, i) => [row, i]).filter(([row]) => P.matchesSearch(search, row)).map(([, i]) => i);
    assert.deepEqual(got, c.expected, c.name);
    checked += 1;
}
assert.ok(checked > 20, 'web_list cases');
console.log(`report-filter ok (${checked})`);
