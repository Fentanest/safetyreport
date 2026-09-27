const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const rootDir = path.resolve(__dirname, '../..');

class Node {
    constructor(tag) {
        this.tag = tag;
        this.children = [];
        this.ownText = '';
    }
    set textContent(value) {
        this.ownText = String(value);
        this.children = [];
    }
    get textContent() {
        return this.ownText + this.children.map((child) => child.textContent).join('');
    }
    appendChild(child) {
        this.children.push(child);
        return child;
    }
}

const document = { createElement: (tag) => new Node(tag) };
const window = {};
vm.runInNewContext(
    fs.readFileSync(path.join(rootDir, 'web/static/ui/community_consent_markdown.js'), 'utf8'),
    { document, window, URL }
);

function descendants(node, tag) {
    return node.children.flatMap((child) => [
        ...(child.tag === tag ? [child] : []),
        ...descendants(child, tag),
    ]);
}

const consent = fs.readFileSync(
    path.join(rootDir, 'tests/fixtures/share-consent-2026-09-28.1.txt'), 'utf8'
);
const root = new Node('div');
window.renderCommunityConsentMarkdown(root, consent);
assert.equal(descendants(root, 'h1').length, 1);
assert.ok(descendants(root, 'h2').length > 0);
assert.equal(descendants(root, 'table').length, 1);
assert.equal(descendants(root, 'thead').length, 1);
assert.equal(descendants(root, 'tbody')[0].children.length, 5);
assert.equal(descendants(root, 'table')[0].className, 'table table-sm');
assert.ok(descendants(root, 'div').some((node) => node.className === 'table-responsive'));
assert.ok(descendants(root, 'code').some((node) => node.textContent === '경기76자3623'));
assert.ok(descendants(root, 'strong').length > 0);
assert.ok(!root.textContent.includes('**'));
assert.ok(!root.textContent.includes(']('));
assert.ok(!root.textContent.includes('| --- |'));
assert.ok(!root.textContent.includes('|'));
assert.ok(!root.textContent.includes('`'));
assert.equal(descendants(root, 'a').length, 4);
for (const link of descendants(root, 'a')) {
    assert.equal(link.target, '_blank');
    assert.equal(link.rel, 'noopener noreferrer');
    assert.ok(['safemap.worklazy.net', 'safeauth.worklazy.net', 'github.com'].includes(new URL(link.href).hostname));
}

root.textContent = 'old';
window.renderCommunityConsentMarkdown(root,
    '### Third\n- **Bold** and `code`\n- item\n\n' +
    '| **Head** | Link |\n| --- | :---: |\n| `cell` | [ok](https://github.com/a) |\n\n' +
    '[js](javascript:alert) [data](data:text/plain,x) ' +
    '[http](http://github.com/) [fake](https://github.com.evil.test/) ' +
    '[proto](https://__proto__/) ' +
    '[user](https://evil.test@github.com/) <img src=x> ~~unknown~~');
assert.equal(descendants(root, 'h3').length, 1);
assert.equal(descendants(root, 'ul')[0].children.length, 2);
assert.ok(descendants(root, 'th').some((node) => node.textContent === 'Head'));
assert.ok(descendants(root, 'td').some((node) => node.textContent === 'cell'));
assert.equal(descendants(root, 'a').length, 1);
assert.ok(root.textContent.includes('js (javascript:alert)'));
assert.ok(root.textContent.includes('data (data:text/plain,x)'));
assert.ok(root.textContent.includes('http (http://github.com/)'));
assert.ok(root.textContent.includes('fake (https://github.com.evil.test/)'));
assert.ok(root.textContent.includes('proto (https://__proto__/)'));
assert.ok(root.textContent.includes('user (https://evil.test@github.com/)'));
assert.ok(root.textContent.includes('<img src=x> ~~unknown~~'));
assert.equal(descendants(root, 'img').length, 0);

console.log('consent Markdown rendering OK');
