/* 통계 화면 순수 계산(EO R-16: stats.js mount 안에서 옮겼다). DOM·전역 상태를 읽지 않는다.
   - 표시 형식(esc·num·won·pct), 목록·지도 드릴다운 주소, 합계 행 가중 평균, CSV 머리말·행·셀, 법규 자연 정렬.
   ctx = { year, filters, dedupeMode, search } — stats.js 가 #statsData 와 location 에서 만든다. */
(function (root) {
    'use strict';

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function num(n) { return Number(n || 0).toLocaleString('ko-KR'); }
    function won(n) { return num(n) + '원'; }
    // 분모 0 은 계산 불가('—'), 실제 0 은 0.0%
    function pct(n, d) { return d > 0 ? (Number(n || 0) / d * 100).toFixed(1) + '%' : '—'; }
    function pctValue(n, d) { return d > 0 ? Number((Number(n || 0) / d * 100).toFixed(1)) : null; }

    function rowKey(row, person) {
        var key = row.agency_key || 'src:-:' + row.agency;
        // Jinja tojson의 공백·비ASCII escape와 무관하게 같은 키로 정규화한다.
        return JSON.stringify(person ? [key, row.person] : [key]);
    }

    // 목록(/data)으로 넘길 공통 조건. 연도 → 답변일 범위(목록에 연도 필터가 없음). 서버 템플릿 drill_base 와 같은 규칙.
    function listBaseParams(ctx) {
        var FILTERS = ctx.filters || {}, YEAR = ctx.year || 'all';
        var p = new URLSearchParams();
        p.set('dedupe', ctx.dedupeMode || 'canonical');
        var ys = YEAR !== 'all' ? YEAR + '-01-01' : '';
        var ye = YEAR !== 'all' ? YEAR + '-12-31' : '';
        var rs = [ys, FILTERS.responseDateStart || ''].sort().pop();
        var ends = [ye, FILTERS.responseDateEnd || ''].filter(Boolean).sort();
        var re = ends.length ? ends[0] : '';
        [['excludePolice', FILTERS.excludePolice ? 'true' : ''], ['onlyPolice', FILTERS.onlyPolice ? 'true' : ''], ['law', FILTERS.law], ['lawExact', FILTERS.law ? 'true' : ''], ['responseDateStart', rs], ['responseDateEnd', re],
         ['reportName', FILTERS.reportName], ['location', FILTERS.location],
         ['reportDateStart', FILTERS.reportDateStart], ['reportDateEnd', FILTERS.reportDateEnd],
         ['occurDateStart', FILTERS.occurDateStart], ['occurDateEnd', FILTERS.occurDateEnd],
         ['occurTimeStart', FILTERS.occurTimeStart], ['occurTimeEnd', FILTERS.occurTimeEnd]].forEach(function (kv) {
            if (kv[1]) p.set(kv[0], kv[1]);
        });
        return p;
    }

    function kpiListUrl(cat, extra, ctx) {
        var FILTERS = ctx.filters || {};
        var p = listBaseParams(ctx);
        var agencyQuery = String(FILTERS.agency || '');
        if (agencyQuery) {
            p.set('agency', agencyQuery);
            if (FILTERS.agencyExact) p.set('agencyExact', 'true');
        }
        Object.keys(extra || {}).forEach(function (k) { p.set(k, extra[k]); });
        var qs = p.toString();
        return '/data/' + cat + (qs ? '?' + qs : '');
    }

    // 지도(/stats/map, /stats/map/points)로 넘길 조건: 통계 공통 조건 이름 그대로
    function mapParams(cat, target, ctx) {
        var FILTERS = ctx.filters || {}, YEAR = ctx.year || 'all';
        var p = new URLSearchParams();
        if (cat) p.set('category', cat);
        if (YEAR !== 'all') p.set('year', YEAR);
        Object.keys(FILTERS).forEach(function (k) {
            var v = FILTERS[k];
            if (k === 'year' || v === null || v === undefined || v === '' || v === false) return;
            p.set(k, v === true ? 'true' : String(v));
        });
        var urlDedupe = new URLSearchParams(ctx.search || '').get('dedupe');
        if (urlDedupe) p.set('dedupe', urlDedupe); // 통계가 고른 대표건 모드를 지도도 따른다
        if (target && target.agency) p.set('targetAgency', target.agency);
        if (target && target.agencyKey) p.set('targetAgencyKey', target.agencyKey);
        if (target && target.person) p.set('targetPerson', target.person);
        if (target) p.set('completedOnly', 'true'); // 상세 표의 모집단. 요약 지도는 전체 그대로.
        return p;
    }

    function lawUrl(search, law) {
        var p = new URLSearchParams(search || '');
        if (law) p.set('law', law); else p.delete('law');
        var qs = p.toString();
        return '/stats' + (qs ? '?' + qs : '');
    }

    function naturalCompare(a, b) {
        var ax = [], bx = [];
        a.replace(/(\d+)|(\D+)/g, function (_, n, s) { ax.push(n ? [+n, ''] : [Infinity, s]); });
        b.replace(/(\d+)|(\D+)/g, function (_, n, s) { bx.push(n ? [+n, ''] : [Infinity, s]); });
        for (var i = 0; i < Math.max(ax.length, bx.length); i++) {
            if (!ax[i]) return -1;
            if (!bx[i]) return 1;
            var d = ax[i][0] !== bx[i][0] ? ax[i][0] - bx[i][0] : ax[i][1].localeCompare(bx[i][1], 'ko');
            if (d !== 0) return d;
        }
        return 0;
    }

    var COUNT_KEYS = ['fines', 'warn', 'rejects', 'dispositionUnknown', 'noPenalty', 'unclassified'];

    /* 합계 행: rows 는 검색이 적용된 행의 숫자들({total, fine, fineCount, est, estCount, avgD, avgN, rAvg, rCount, COUNT_KEYS…}).
       평균 처리기간·별점은 행 평균 × 행의 유효 표본 수로 가중한다(기관 평균의 단순 평균이 아님). */
    function summarizeRows(rows) {
        var sums = { total: 0, fine: 0, fineCount: 0, est: 0, estCount: 0 };
        COUNT_KEYS.forEach(function (k) { sums[k] = 0; });
        var wDays = 0, wBase = 0, wRating = 0, wRatingBase = 0;
        rows.forEach(function (row) {
            if (!row) return;
            sums.total += row.total; sums.fine += row.fine; sums.fineCount += row.fineCount;
            sums.est += row.est; sums.estCount += row.estCount;
            COUNT_KEYS.forEach(function (k) { sums[k] += row[k]; });
            if (!isNaN(row.avgD) && row.avgD >= 0 && row.avgN > 0) { wDays += row.avgD * row.avgN; wBase += row.avgN; }
            if (!isNaN(row.rAvg) && row.rAvg >= 0 && row.rCount > 0) { wRating += row.rAvg * row.rCount; wRatingBase += row.rCount; }
        });
        return { sums: sums, wDays: wDays, wBase: wBase, wRating: wRating, wRatingBase: wRatingBase };
    }

    // CSV 셀: 숫자는 그대로, 수식처럼 시작하면 작은따옴표로 막고, 쉼표·따옴표·줄바꿈은 따옴표로 감싼다.
    function csvCell(value) {
        if (value === null || value === undefined) return '';
        if (typeof value === 'number') return String(value);
        var text = String(value);
        if (/^[=+\-@\t\r]/.test(text)) text = "'" + text; // 스프레드시트 수식 실행 방지
        return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
    }

    // CSV 머리말·행: 확정/추정 금액은 별도 열(PROJECT_RULES §3-2). disp = 처분 분류 [{key, label}].
    function csvHeader(person, disp) {
        var header = ['처리기관'].concat(person ? ['담당자'] : []).concat([
            '총 건수', '평균 답변 소요(일)', '답변 소요 표본 수', '확정 과태료(원)', '확정 과태료 건수', '금액 미확인 과태료 건수',
            '추정 과태료(원)', '추정 과태료 건수'
        ]);
        disp.forEach(function (item) { header.push(item.label + ' 건수', item.label + ' 비율(%)'); });
        header.push('별점 평균', '평가 수', '기관 집계 키');
        return header;
    }

    function csvRow(row, person, disp) {
        var total = Number(row.total || 0);
        var cells = [row.agency].concat(person ? [row.person] : []).concat([
            total, row.avg_days == null ? null : Number(row.avg_days), row.avg_days_count == null ? null : Number(row.avg_days_count),
            Number(row.total_fine_amount || 0), Math.max(0, Number(row.fines || 0) - Number(row.fine_amount_unknown || 0)),
            Number(row.fine_amount_unknown || 0),
            row.estimated_fine_amount == null ? null : Number(row.estimated_fine_amount),
            row.estimated_fine_count == null ? null : Number(row.estimated_fine_count)
        ]);
        disp.forEach(function (item) {
            var n = row[item.key] == null ? null : Number(row[item.key]);
            cells.push(n, n == null ? null : pctValue(n, total));
        });
        cells.push(row.avg_rating == null ? null : Number(row.avg_rating), Number(row.rating_count || 0), row.agency_key || 'src:-:' + row.agency);
        return cells;
    }

    function csvText(header, rows) {
        var lines = [header.map(csvCell).join(',')].concat(rows.map(function (cells) { return cells.map(csvCell).join(','); }));
        return '﻿' + lines.join('\r\n') + '\r\n';
    }

    root.SrStatsCalc = {
        esc: esc, num: num, won: won, pct: pct, pctValue: pctValue, rowKey: rowKey, listBaseParams: listBaseParams,
        kpiListUrl: kpiListUrl, mapParams: mapParams, lawUrl: lawUrl, naturalCompare: naturalCompare,
        COUNT_KEYS: COUNT_KEYS, summarizeRows: summarizeRows, csvCell: csvCell, csvHeader: csvHeader, csvRow: csvRow,
        csvText: csvText,
    };
})(typeof window === 'undefined' ? globalThis : window);
