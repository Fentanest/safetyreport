/* 날짜/시각은 저장 문자열을 바꾸지 않고 입력 범위의 정밀도로 비교한다. */
(function (root) {
    'use strict';
    function inRange(value, min, max, time) {
        min = String(min || ''); max = String(max || '');
        if (!min && !max) return true;
        var current = String(value == null ? '' : value);
        if (time) {
            var match = /^(\d{2}):(\d{2})(?::\d{2})?/.exec(current);
            if (!match || Number(match[1]) > 23 || Number(match[2]) > 59) return false;
            current = match[1] + ':' + match[2];
        } else {
            var date = /^(\d{4})-(\d{2})-(\d{2})(?:$|[ T])/.exec(current);
            if (!date) return false;
            var year = Number(date[1]), month = Number(date[2]), day = Number(date[3]);
            var parsed = new Date(0); parsed.setUTCFullYear(year, month - 1, day); parsed.setUTCHours(0, 0, 0, 0);
            if (parsed.getUTCFullYear() !== year || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) return false;
            current = date[1] + '-' + date[2] + '-' + date[3];
        }
        return (!min || min <= current) && (!max || current <= max);
    }
    // 아래는 목록 검색(data_table)의 행 판정. 서버 services/report_filter_spec.py·모바일 report_query.dart 와 같은 의미이며
    // contracts/report-filter-vectors.json 으로 함께 검사한다(EO R-02).
    function normalizeText(value) {
        return String(value == null ? '' : value).trim().toLowerCase();
    }

    function parseGroups(query) {
        return String(query == null ? '' : query)
            .split(',')
            .map(function (group) { return group.split('&').map(normalizeText).filter(Boolean); })
            .filter(function (group) { return group.length > 0; });
    }

    function matchesGroups(value, groups) {
        if (!groups.length) return true;
        var haystack = normalizeText(value);
        return groups.some(function (group) {
            return group.every(function (term) { return haystack.indexOf(term) >= 0; });
        });
    }

    function ratingKey(value) {
        var text = String(value == null ? '' : value).trim();
        if (!text) return '__none__';
        var numeric = Number(text);
        if (!Number.isFinite(numeric) || numeric <= 0) return '__none__';
        return String(Math.trunc(numeric));
    }

    var RANGE_FIELDS = ['신고일', '답변일', '발생일자', '발생시각'];

    /* search: {text:[{field, groups}], ranges:[{min,max,time}] (RANGE_FIELDS 순서), statuses, ratings, poll,
       excludePolice, onlyPolice} */
    function matchesSearch(search, row) {
        if (search.text.some(function (item) { return !matchesGroups(row[item.field], item.groups); })) return false;
        if (search.poll && String(row['만족도조사여부'] || '').trim() !== search.poll) return false;
        if (search.statuses.length && search.statuses.indexOf(root.SrReportPolicy.displayStatus(row['처리상태'])) < 0) return false;
        if (search.ratings.length && search.ratings.indexOf(ratingKey(row['별점'])) < 0) return false;
        for (var i = 0; i < search.ranges.length; i++) {
            var range = search.ranges[i];
            if (!inRange(row[RANGE_FIELDS[i]], range.min, range.max, range.time)) return false;
        }
        var agency = String(row['처리기관'] || '');
        if (search.excludePolice && agency.indexOf('경찰') >= 0) return false;
        if (search.onlyPolice && agency.indexOf('경찰') < 0) return false;
        return true;
    }

    root.SrListPredicates = {
        inRange: inRange, parseGroups: parseGroups, matchesGroups: matchesGroups, ratingKey: ratingKey,
        matchesSearch: matchesSearch, RANGE_FIELDS: RANGE_FIELDS,
    };
})(typeof window === 'undefined' ? globalThis : window);
