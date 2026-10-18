// 실제 목록 초기화 코드가 서버의 기관 키/담당자 정확 조건을 부분 검색으로 다시 좁히지 않는다.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync('web/static/ui/data-table.js', 'utf8').split('    const isDuplicate =')[0] + '\n});';
function inputs(params) {
    const values = {};
    const doc = { getElementById: () => ({textContent: '{"records":[],"tableId":"allTable"}'}) };
    function $(selector) {
        if (selector === doc) return {ready: fn => fn()};
        return {val: value => {values[selector] = value;}, prop: () => {}};
    }
    vm.runInNewContext(source, {document: doc, window: {location: {search: '?' + new URLSearchParams(params)}}, $, URLSearchParams});
    return values;
}
assert.deepStrictEqual(inputs({agency: '(구)옛기관', agencyKey: 'src:9999993:옛기관', person: 'A,B&C', personExact: 'true'}), {});
assert.deepStrictEqual(inputs({agency: '기관', person: '김민수'}), {'#searchAgency': '기관', '#searchPerson': '김민수'});
console.log('2 identity bootstrap cases passed');
