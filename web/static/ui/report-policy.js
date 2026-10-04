/* 신고 상태 규칙(EO R-01). 서버 services/report_policy.py·모바일 lib/services/report_policy.dart 와 같은 규칙이며
   contracts/report-policy-vectors.json 으로 세 구현을 함께 검사한다(tests/js/report_policy_check.js). */
(function (root) {
    'use strict';
    var TRIM = /^[ \t\n\v\f\r\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+|[ \t\n\v\f\r\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+$/g;
    var PROCESSING_LABEL = '처리중';
    var COMPLETED = ['수용', '불수용', '일부수용', '기타', '답변완료'];
    var PROCESSING = ['처리중', '진행', '진행중', '검토중'];
    var REJECT = ['불수용', '기타'];

    function norm(value) {
        return String(value == null ? '' : value).replace(TRIM, '');
    }

    function displayStatus(value) {
        var text = norm(value);
        return PROCESSING.indexOf(text) >= 0 ? PROCESSING_LABEL : text;
    }

    function breakdownStatus(value) {
        return displayStatus(value) || PROCESSING_LABEL;
    }

    function badgeKey(value) {
        var text = norm(value);
        if (text === '수용') return 'accept';
        if (text === '일부수용') return 'partial';
        if (REJECT.indexOf(text) >= 0) return 'reject';
        if (PROCESSING.indexOf(text) >= 0) return 'processing';
        if (text === '보완요청') return 'supplement';
        if (text === '취하') return 'withdraw';
        return 'default';
    }

    function isCompleted(value) { return COMPLETED.indexOf(norm(value)) >= 0; }
    function isProcessing(value) { return PROCESSING.indexOf(norm(value)) >= 0; }
    function isReject(value) { return REJECT.indexOf(norm(value)) >= 0; }
    function isWithdrawn(value) { return norm(value) === '취하'; }

    function listStatusFilter(name, value) {
        var text = norm(value);
        if (name === PROCESSING_LABEL) return PROCESSING.indexOf(text) >= 0;
        if (name === '완료') return COMPLETED.indexOf(text) >= 0;
        if (name === '불수용') return REJECT.indexOf(text) >= 0;
        return text === norm(name);
    }

    function listFineFilter(name, fine, status) {
        var text = norm(fine);
        if (name === '과태료') return text.indexOf('과태료') >= 0;
        if (name === '경고') return text.indexOf('경고') >= 0 || text.indexOf('범칙금') >= 0;
        if (name === '미확인') return text === '미확인' && !isReject(status);
        return true;
    }

    root.SrReportPolicy = {
        norm: norm, displayStatus: displayStatus, breakdownStatus: breakdownStatus, badgeKey: badgeKey,
        isCompleted: isCompleted, isProcessing: isProcessing, isReject: isReject, isWithdrawn: isWithdrawn,
        listStatusFilter: listStatusFilter, listFineFilter: listFineFilter,
    };
})(typeof window === 'undefined' ? globalThis : window);
