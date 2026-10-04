// web/static/ui/report-policy.js 가 contracts/report-policy-vectors.json 과 같은 결과를 내는지(EO R-01).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const rootDir = path.resolve(__dirname, '../..');
const sandbox = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(rootDir, 'web/static/ui/report-policy.js'), 'utf8'), sandbox);
const policy = sandbox.window.SrReportPolicy;
const vectors = JSON.parse(fs.readFileSync(path.join(rootDir, 'contracts/report-policy-vectors.json'), 'utf8'));

for (const c of vectors.status_cases) {
    const label = JSON.stringify(c.status);
    assert.equal(policy.displayStatus(c.status), c.display, `display ${label}`);
    assert.equal(policy.breakdownStatus(c.status), c.breakdown, `breakdown ${label}`);
    assert.equal(policy.badgeKey(c.status), c.badge, `badge ${label}`);
    assert.equal(policy.isCompleted(c.status), c.completed, `completed ${label}`);
    assert.equal(policy.isProcessing(c.status), c.processing, `processing ${label}`);
    assert.equal(policy.isReject(c.status), c.reject, `reject ${label}`);
    assert.equal(policy.isWithdrawn(c.status), c.withdrawn, `withdrawn ${label}`);
}
for (const c of vectors.filter_cases) {
    const got = vectors.filter_rows.map((r) => c.kind === 'status'
        ? policy.listStatusFilter(c.name, r['처리상태'])
        : policy.listFineFilter(c.name, r['범칙금_과태료'], r['처리상태']));
    assert.deepEqual(got, c.matches, `${c.kind} ${c.name}`);
}
assert.equal(policy.norm(vectors.trim_chars + 'x' + vectors.trim_chars), 'x', 'trim chars');
console.log('report-policy ok');
