/* 신고 목록 표 셀 렌더러(EO R-14: data_table.html 에서 옮긴 순수 함수). DataTables render 와 첨부 모달이 쓴다. */
(function (root) {
    function esc(v) {
        return String(v == null ? '' : v)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function normalizeCellText(value) {
        if (value == null) return '';
        if (Array.isArray(value)) {
            return value.map(normalizeCellText).filter(Boolean).join('\n');
        }
        if (typeof value === 'object') {
            if (typeof value.url === 'string') return value.url.trim();
            if (typeof value.href === 'string') return value.href.trim();
            try { return JSON.stringify(value); } catch (_) { return String(value); }
        }
        return String(value).trim();
    }

    function splitMultilineLinks(value) {
        const text = normalizeCellText(value);
        if (!text || text === '6개월 초과') return [];
        return text.split(/\n|%0A|%0a/).map(link => link.trim()).filter(Boolean);
    }

    function encodeLinksPayload(links) {
        try {
            return esc(JSON.stringify(Array.isArray(links) ? links : []));
        } catch (_) {
            return '[]';
        }
    }

    function decodeLinksPayload(raw) {
        if (!raw) return [];
        try {
            const parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed.map(normalizeCellText).filter(Boolean) : [];
        } catch (_) {
            return String(raw).split(',').map(part => part.trim()).filter(Boolean);
        }
    }

    function labelFromUrl(url, index) {
        const text = normalizeCellText(url);
        return text.split('?')[0].split('/').filter(Boolean).pop() || ('파일 ' + index);
    }

    function renderBadge(d) {
        const v = normalizeCellText(d);
        const cls = 'sr-badge-' + window.SrReportPolicy.badgeKey(v);
        return `<span class="sr-badge ${cls}">${esc(v)}</span>`;
    }

    function renderEllipsis(d, type, maxWidth) {
        const raw = normalizeCellText(d);
        if (type !== 'display') return raw;
        const v = esc(raw);
        return `<span class="ellipsis-cell" style="max-width:${maxWidth}px;display:block" title="${v}">${v}</span>`;
    }

    // kind: 'photo'(첨부사진) | 'file'(첨부파일). 예전에는 버튼 문구로 유형을 정해 첨부파일도 사진·'장'으로 보였다(기술일지 C13).
    function renderAttach(d, type, btnLabel, btnClass, kind) {
        const text = normalizeCellText(d);
        if (type !== 'display') return text;
        if (!text) return '';
        if (text === '6개월 초과') return '만료';
        const links = splitMultilineLinks(text);
        const dataType = kind === 'file' ? 'file' : 'photo';
        const unit = dataType === 'photo' ? '장' : '개';
        return `${links.length}${unit} <button class="btn btn-sm ${btnClass} py-0 px-1 view-all-btn" data-links="${encodeLinksPayload(links)}" data-type="${dataType}">${btnLabel}</button>`;
    }

    function renderMap(d, type) {
        const text = normalizeCellText(d);
        if (type !== 'display') return text;
        if (!text) return '';
        if (text === '6개월 초과') return '만료';
        const links = splitMultilineLinks(text);
        if (!links.length) return '';
        const first = esc(links[0]);
        return `<a href="${first}" target="_blank">다운로드</a>`;
    }


    root.SrDataTableCells = {
        esc: esc, normalizeCellText: normalizeCellText, splitMultilineLinks: splitMultilineLinks,
        encodeLinksPayload: encodeLinksPayload, decodeLinksPayload: decodeLinksPayload, labelFromUrl: labelFromUrl,
        renderBadge: renderBadge, renderEllipsis: renderEllipsis, renderAttach: renderAttach, renderMap: renderMap,
    };
})(typeof window === 'undefined' ? globalThis : window);
