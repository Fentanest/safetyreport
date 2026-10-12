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

    function formatWon(amount) {
        return Number(amount).toLocaleString('ko-KR') + '원';
    }

    // 금액 없는 과태료의 법정 최저 기준 추정 보조 표기(statistics-spec §4-1·§4-2, PROJECT_RULES §3-2).
    // estimateAmount 는 services/fine_estimate.estimate_if_unknown 의 결과만 받는다(여기서 판정하지 않는다).
    // 값이 없으면(해당 행이 추정 대상이 아니면) '' — 확정 '범칙금_과태료' 원문은 호출자가 그대로 둔다.
    function renderFineEstimateBadge(estimateAmount, estimateBasis) {
        if (estimateAmount === null || estimateAmount === undefined || estimateAmount === '') return '';
        const amount = Number(estimateAmount);
        if (!Number.isFinite(amount)) return '';
        const basisText = normalizeCellText(estimateBasis);
        const tooltip = '법정 최저 기준 추정' + (basisText ? (' · ' + basisText) : '');
        return `<span class="sr-fine-estimate" title="${esc(tooltip)}" aria-label="${esc(tooltip)}">추정 ${esc(formatWon(amount))}</span>`;
    }

    // 목록 '과태료/범칙금' 열: 원래 값은 그대로 두고(정렬·검색은 원문 d 기준) 추정이 있으면 옆에 흐리게 덧붙인다.
    function renderFineCell(d, type, estimateAmount, estimateBasis, correctedText) {
        const raw = normalizeCellText(d);
        if (type !== 'display') return raw;
        const badge = renderFineEstimateBadge(estimateAmount, estimateBasis);
        if (!badge) return esc(correctedText || raw);
        return `<span class="sr-fine-value">${esc(raw)}</span>${badge}`;
    }


    root.SrDataTableCells = {
        esc: esc, normalizeCellText: normalizeCellText, splitMultilineLinks: splitMultilineLinks,
        encodeLinksPayload: encodeLinksPayload, decodeLinksPayload: decodeLinksPayload, labelFromUrl: labelFromUrl,
        renderBadge: renderBadge, renderEllipsis: renderEllipsis, renderAttach: renderAttach, renderMap: renderMap,
        formatWon: formatWon, renderFineEstimateBadge: renderFineEstimateBadge, renderFineCell: renderFineCell,
    };
})(typeof window === 'undefined' ? globalThis : window);
