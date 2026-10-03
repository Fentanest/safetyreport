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
    root.SrListPredicates = { inRange: inRange };
})(typeof window === 'undefined' ? globalThis : window);
