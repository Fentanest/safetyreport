// 신고 목록 표 셀 렌더러(web/static/ui/data-table-cells.js, EO R-14)의 순수 동작.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const rootDir = path.resolve(__dirname, '../..');
const sandbox = { window: {} };
for (const file of ['web/static/ui/report-policy.js', 'web/static/ui/data-table-cells.js']) {
    vm.runInNewContext(fs.readFileSync(path.join(rootDir, file), 'utf8'), sandbox);
}
const C = sandbox.window.SrDataTableCells;

assert.equal(C.esc('<a href="x">&</a>'), '&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;');
assert.equal(C.normalizeCellText(null), '');
assert.equal(C.normalizeCellText([' a ', null, { url: ' u ' }]), 'a\nu');
assert.deepEqual([...C.splitMultilineLinks('a\nb%0Ac')], ['a', 'b', 'c']);
assert.deepEqual([...C.splitMultilineLinks('6개월 초과')], []);
assert.deepEqual([...C.decodeLinksPayload('["x","y"]')], ['x', 'y']);
assert.deepEqual([...C.decodeLinksPayload('x, y')], ['x', 'y'], '옛 쉼표 형식');
assert.equal(C.labelFromUrl('https://h/p/file.jpg?x=1', 2), 'file.jpg');
assert.equal(C.renderBadge(' 수용 '), '<span class="sr-badge sr-badge-accept">수용</span>');
assert.equal(C.renderEllipsis('<b>', 'sort', 10), '<b>', '표시가 아니면 원문');
assert.match(C.renderEllipsis('<b>', 'display', 10), /title="&lt;b&gt;">&lt;b&gt;</);
const photos = C.renderAttach('u1\nu2', 'display', '보기', 'btn-x', 'photo');
assert.match(photos, /^2장 <button/);
assert.match(photos, /data-type="photo"/);
assert.match(C.renderAttach('f1', 'display', '보기', 'btn-x', 'file'), /^1개 .*data-type="file"/);
assert.equal(C.renderAttach('6개월 초과', 'display', '보기', 'b', 'photo'), '만료');
assert.equal(C.renderMap('m1\nm2', 'display'), '<a href="m1" target="_blank">다운로드</a>');
assert.equal(C.renderMap('', 'display'), '');
console.log('data-table-cells ok');
