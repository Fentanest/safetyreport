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

// 추정 과태료 보조 표기(statistics-spec §4-1·§4-2) — 목록 '과태료/범칙금' 열, 상세 모달과 같은 입력 모양.
assert.equal(C.formatWon(50000), '50,000원');
assert.equal(C.renderFineEstimateBadge(null, null), '', '추정이 없으면 표시 없음');
assert.equal(C.renderFineEstimateBadge(undefined, ''), '');
assert.equal(C.renderFineEstimateBadge('', ''), '');
const badge = C.renderFineEstimateBadge(50000, '도로교통법 시행령 별표6 제3호(일반도로 전용차로) · 승용등');
assert.match(badge, /^<span class="sr-fine-estimate" /);
assert.match(badge, /추정 50,000원<\/span>$/);
assert.match(badge, /title="법정 최저 기준 추정 · 도로교통법 시행령 별표6 제3호\(일반도로 전용차로\) · 승용등"/);
assert.match(badge, /aria-label="법정 최저 기준 추정 · 도로교통법 시행령 별표6 제3호\(일반도로 전용차로\) · 승용등"/);
assert.equal(C.renderFineCell('과태료', 'sort', 50000, '근거'), '과태료', '정렬·검색 값은 추정과 무관하게 원문 그대로');
assert.equal(C.renderFineCell('과태료', 'filter', 50000, '근거'), '과태료');
assert.equal(C.renderFineCell('과태료', 'display', null, null), '과태료', '추정 없으면 원문만');
const cell = C.renderFineCell('과태료', 'display', 50000, '근거');
assert.match(cell, /^<span class="sr-fine-value">과태료<\/span><span class="sr-fine-estimate"/);
assert.equal(C.renderFineCell('<b>', 'display', null, null), '&lt;b&gt;', '추정 없어도 원문은 escape 한다');
console.log('data-table-cells ok');

assert.equal(C.renderFineCell('과태료: 40,0000원', 'display', null, null, '과태료: 40,000원'), '과태료: 40,000원');
assert.equal(C.renderFineCell('과태료: 40,0000원', 'filter', null, null, '과태료: 40,000원'), '과태료: 40,0000원');
