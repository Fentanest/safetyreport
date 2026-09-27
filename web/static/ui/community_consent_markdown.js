// Small, display-only Markdown renderer for the community consent document.
(function () {
    'use strict';

    var allowedHosts = {
        'safemap.worklazy.net': true,
        'safeauth.worklazy.net': true,
        'github.com': true
    };

    function element(tag, className) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        return node;
    }

    function plain(parent, value) {
        var span = element('span');
        span.textContent = value;
        parent.appendChild(span);
    }

    function safeHref(value) {
        try {
            var url = new URL(value);
            if (url.protocol === 'https:' &&
                    Object.prototype.hasOwnProperty.call(allowedHosts, url.hostname.toLowerCase()) &&
                    !url.username && !url.password && !url.port) return url.href;
        } catch (e) { /* Keep malformed links as text. */ }
        return null;
    }

    function inline(parent, value) {
        var token = /\*\*([^*\n]+)\*\*|`([^`\n]+)`|\[([^\]\n]+)\]\(([^)\s]+)\)/g;
        var start = 0, match;
        while ((match = token.exec(value)) !== null) {
            if (match.index > start) plain(parent, value.slice(start, match.index));
            if (match[1] !== undefined) {
                var strong = element('strong');
                inline(strong, match[1]);
                parent.appendChild(strong);
            } else if (match[2] !== undefined) {
                var code = element('code');
                code.textContent = match[2];
                parent.appendChild(code);
            } else {
                var href = safeHref(match[4]);
                if (href) {
                    var link = element('a');
                    link.href = href;
                    link.target = '_blank';
                    link.rel = 'noopener noreferrer';
                    link.textContent = match[3];
                    parent.appendChild(link);
                } else {
                    plain(parent, match[3] + ' (' + match[4] + ')');
                }
            }
            start = token.lastIndex;
        }
        if (start < value.length) plain(parent, value.slice(start));
    }

    function cells(line) {
        var trimmed = line.trim();
        if (trimmed.charAt(0) === '|') trimmed = trimmed.slice(1);
        if (trimmed.charAt(trimmed.length - 1) === '|') trimmed = trimmed.slice(0, -1);
        return trimmed.split('|').map(function (cell) { return cell.trim(); });
    }

    function tableAt(lines, index) {
        if (index + 1 >= lines.length || lines[index].indexOf('|') < 0) return false;
        var heads = cells(lines[index]);
        var separators = cells(lines[index + 1]);
        return heads.length >= 2 && heads.length === separators.length &&
            separators.every(function (cell) { return /^:?-{3,}:?$/.test(cell); });
    }

    function renderTable(root, lines, index) {
        var heads = cells(lines[index]);
        var wrapper = element('div', 'table-responsive');
        var table = element('table', 'table table-sm');
        var thead = element('thead');
        var tr = element('tr');
        heads.forEach(function (head) {
            var th = element('th');
            th.scope = 'col';
            inline(th, head);
            tr.appendChild(th);
        });
        thead.appendChild(tr);
        table.appendChild(thead);
        var tbody = element('tbody');
        index += 2; // Skip the Markdown separator row.
        while (index < lines.length && lines[index].trim() && lines[index].indexOf('|') >= 0) {
            var row = cells(lines[index]);
            if (row.length !== heads.length) break;
            tr = element('tr');
            row.forEach(function (value) {
                var td = element('td');
                inline(td, value);
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
            index += 1;
        }
        table.appendChild(tbody);
        wrapper.appendChild(table);
        root.appendChild(wrapper);
        return index;
    }

    function blockAt(lines, index) {
        return /^#{1,3} /.test(lines[index]) || /^- /.test(lines[index]) || tableAt(lines, index);
    }

    function renderCommunityConsentMarkdown(root, source) {
        root.textContent = '';
        var lines = String(source || '').replace(/\r\n?/g, '\n').split('\n');
        var index = 0;
        while (index < lines.length) {
            var line = lines[index].trim();
            if (!line) { index += 1; continue; }
            if (tableAt(lines, index)) {
                index = renderTable(root, lines, index);
                continue;
            }
            var heading = /^(#{1,3}) (.*)$/.exec(line);
            if (heading) {
                var level = heading[1].length;
                var h = element('h' + level, 'fw-bold mt-3 mb-1 fs-6');
                inline(h, heading[2]);
                root.appendChild(h);
                index += 1;
                continue;
            }
            if (/^- /.test(line)) {
                var list = element('ul', 'mb-2 ps-3');
                while (index < lines.length && /^- /.test(lines[index].trim())) {
                    var item = element('li');
                    inline(item, lines[index].trim().slice(2));
                    list.appendChild(item);
                    index += 1;
                }
                root.appendChild(list);
                continue;
            }
            var paragraph = [line];
            index += 1;
            while (index < lines.length && lines[index].trim() && !blockAt(lines, index)) {
                paragraph.push(lines[index].trim());
                index += 1;
            }
            var p = element('p', 'mb-2');
            inline(p, paragraph.join(' '));
            root.appendChild(p);
        }
    }

    window.renderCommunityConsentMarkdown = renderCommunityConsentMarkdown;
})();
