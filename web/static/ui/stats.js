/* 통계 화면(/stats) 동작 — 2026-09-28 개편.
   - 수치는 모두 서버가 같은 요청에서 계산한 값(#statsData)이다. 브라우저는 표의 현재 쪽 행으로 KPI·차트를 계산하지 않는다.
   - 연도·법규·상세 조건 변경은 주소 이동(이전 요청은 브라우저가 취소)이고, 분류 전환은 미리 받은 값으로 즉시 바꾼다.
     작은 지도만 비동기로 받으므로 요청 번호·AbortController 로 늦게 온 이전 응답을 버린다.
   - 여섯 보기는 상세 영역의 집계 단위·기관 범위다. 상단 요약·차트는 바꾸지 않는다.
   DOM 계약: docs/design/pilot-dom-contracts.md, 지표 정의: docs/design/statistics-spec.md §9. */
(function ($) {
    'use strict';
    var Calc = window.SrStatsCalc; // 순수 계산(EO R-16: stats-calc.js)
    function mount() {
    if (window.SrStats.dispose) window.SrStats.dispose();
    var disposed = false, removers = [], resizeObserver = null, scrollFrame = null, sunwiWidget = null;
    function listen(target, type, callback) {
        target.addEventListener(type, callback);
        removers.push(function () { target.removeEventListener(type, callback); });
    }

    var DATA = JSON.parse(document.getElementById('statsData').textContent || '{}');
    if (DATA.pending) { window.SrStatsLoader.start(DATA); return; }
    // Transfer ownership to the category request sequence before controls become active.
    if (window.SrStatsLoader.dispose) window.SrStatsLoader.dispose(false);
    var overview = DATA.overview || {};
    var FILTERS = DATA.filters || {};
    var YEAR = DATA.year || 'all';

    var CAT_LABELS = { traffic: '교통위반', parking: '주정차위반', other: '기타위반' };
    var TYPE_LABELS = {
        agency: '기관별', person: '담당자별', 'police-agency': '경찰 기관', 'police-person': '경찰 담당자',
        'other-agency': '비경찰 기관', 'other-person': '비경찰 담당자'
    };
    // 처분 분류(표 열·차트·상세 패널 공통 순서와 의미색 data-disp)
    var DISP = [
        { key: 'fines', label: '과태료', hint: '처분 문구에 과태료' },
        { key: 'warnings', label: '경고/범칙금', hint: '처분 문구에 경고 또는 범칙금' },
        { key: 'rejects', label: '불수용/기타', hint: '처리상태 불수용·기타' },
        { key: 'disposition_unknown', label: '과태료 미확인', hint: '답변은 완료됐지만 처분(과태료 여부·금액)을 답변에서 읽지 못한 신고 — 교통위반 수용·일부수용, 주정차·버스전용차로·쓰레기 일부수용' },
        { key: 'no_penalty', label: '처분 대상 아님', hint: '과태료 대상이 아닌 유형의 완료 신고' },
        { key: 'unclassified', label: '기타·미분류', hint: '위에 해당하지 않는 신고(처분 문구 없는 완료·취하 등)' }
    ];
    var WIDE_PANEL_MIN = 1240; // stats.css @container srdetail 과 같은 값
    var TYPE_TOP_N = 6;

    // ── 공용 도우미(EO R-16: 순수 계산은 stats-calc.js — 함수 선언으로 두어 앞에서 불러도 된다) ──
    var CTX = { year: YEAR, filters: FILTERS, dedupeMode: DATA.dedupeMode, search: window.location.search };
    function esc(value) { return Calc.esc(value); }
    function num(n) { return Calc.num(n); }
    function won(n) { return Calc.won(n); }
    function pct(n, d) { return Calc.pct(n, d); }
    function storageGet(store, key) { try { return store.getItem(key); } catch (e) { return null; } }
    function storageSet(store, key, value) { try { store.setItem(key, value); } catch (e) { /* 저장 불가여도 화면은 동작 */ } }

    function listBaseParams() { return Calc.listBaseParams(CTX); }
    // 목록이 같은 조건을 재현할 수 있는가(경찰기관 제외/만, AND·OR 기관 검색은 목록 주소로 못 넘긴다)
    var agencyQuery = String(FILTERS.agency || '');
    var LIST_REPRODUCIBLE = true;
    function kpiListUrl(cat, extra) { return Calc.kpiListUrl(cat, extra, CTX); }
    function mapParams(cat, target) { return Calc.mapParams(cat, target, CTX); }

    // ── 상태 ──
    var currentCat = window.srPendingStatsCat || storageGet(sessionStorage, 'stats_cat') || 'traffic';
    delete window.srPendingStatsCat;
    var currentType = storageGet(sessionStorage, 'stats_type') || 'agency';
    if (!CAT_LABELS[currentCat]) currentCat = 'traffic';
    if (!TYPE_LABELS[currentType]) currentType = 'agency';
    var searchTerm = '';
    var pageLength = 50;
    var selected = null; // { cat, type, key }
    var VIEW_KEY = 'stats_view_state';
    var savedView = null;
    try {
        savedView = JSON.parse(storageGet(sessionStorage, VIEW_KEY) || 'null');
    } catch (e) { savedView = null; }
    // 같은 조건(주소)으로 돌아왔을 때만 보기 상태를 되살린다. 조건이 바뀌면 이전 선택은 무효.
    if (!savedView || savedView.qs !== window.location.search) savedView = null;
    if (savedView) {
        searchTerm = savedView.search || '';
        pageLength = Number(savedView.len) || 50;
    }

    // 행 자료(상세 패널·CSV): 분류별 기관/담당자 목록을 키로 찾는다
    var rowIndex = {};
    ['traffic', 'parking', 'other'].forEach(function (cat) {
        rowIndex[cat] = { agency: Object.create(null), person: Object.create(null) };
        var rows = (DATA.rows && DATA.rows[cat]) || { agency: [], person: [] };
        (rows.agency || []).forEach(function (r) { rowIndex[cat].agency[rowKey(r, false)] = r; });
        (rows.person || []).forEach(function (r) { rowIndex[cat].person[rowKey(r, true)] = r; });
    });
    function rowKey(row, person) { return Calc.rowKey(row, person); }
    $('#statsTabsContent tr.sr-drill').each(function () {
        $(this).attr('data-key', JSON.stringify(JSON.parse($(this).attr('data-key'))));
    });
    function isPersonType(type) { return /person$/.test(type); }
    function rowFor(cat, type, key) {
        return rowIndex[cat] && rowIndex[cat][isPersonType(type) ? 'person' : 'agency'][key];
    }

    // ── 요약 카드 ──
    function setText($root, key, text) { $root.find('[data-v="' + key + '"]').text(text); }

    function setKpiLink(kind, href) {
        var el = document.querySelector('.sr-kpi[data-kpi="' + kind + '"]');
        if (!el) return;
        var isLink = el.tagName === 'A';
        if (href && LIST_REPRODUCIBLE) {
            if (!isLink) {
                var a = document.createElement('a');
                a.className = el.className;
                a.setAttribute('data-kpi', kind);
                a.innerHTML = el.innerHTML;
                el.replaceWith(a);
                el = a;
                var label = el.querySelector('.sr-kpi-label');
                if (label && !label.querySelector('.fa-arrow-right')) {
                    label.insertAdjacentHTML('beforeend', '<i class="fas fa-arrow-right" aria-hidden="true"></i>');
                }
            }
            el.setAttribute('href', href);
            el.setAttribute('title', '이 조건의 신고 목록 보기');
        }
    }

    function renderOverview(cat) {
        var s = overview && overview[cat];
        var $box = $('#statsOverview');
        if (!s) {
            $box.find('[data-v]').text('—');
            return;
        }
        var total = Number(s.total || 0);
        setText($box, 'total', num(total) + '건');
        setText($box, 'processing', num(s.processing) + '건');
        setText($box, 'supplementNote', '보완요청 ' + num(s.supplement) + '건');
        setText($box, 'completed', num(s.completed) + '건');
        setText($box, 'completedNote', '총 ' + num(total) + '건 중 ' + pct(s.completed, total));
        setText($box, 'completedSplit', '수용 ' + num(s.accept) + ' · 일부 ' + num(s.partial) + ' · 불수용/기타 ' + num(s.reject));
        var d = s.disposition || null;
        if (d) {
            setText($box, 'fines', num(d.fines) + '건');
            setText($box, 'finesNote', '총 ' + num(total) + '건 중 ' + pct(d.fines, total));
            setText($box, 'warnings', num(d.warnings) + '건');
            setText($box, 'warningsNote', '총 ' + num(total) + '건 중 ' + pct(d.warnings, total));
        } else {
            setText($box, 'fines', '—');
            setText($box, 'finesNote', '서버가 처분 분포를 제공하지 않습니다');
            setText($box, 'warnings', '—');
            setText($box, 'warningsNote', '');
        }
        setText($box, 'avg', s.avg_days == null ? '—' : Number(s.avg_days).toFixed(1) + '일');
        var avgNote = '완료 신고 유효 표본 ' + num(s.avg_days_count) + '건';
        if (s.reversed_date_count) avgNote += ' · 날짜 역전 ' + num(s.reversed_date_count) + '건 제외';
        setText($box, 'avgNote', avgNote);
        var fa = s.fine_amount || null;
        if (fa) {
            setText($box, 'confirmedAmount', won(fa.confirmed_amount));
            setText($box, 'confirmedCount', num(fa.confirmed_count) + '건');
            setText($box, 'estimated', won(fa.estimated_amount) + ' · ' + num(fa.estimated_count) + '건');
            setText($box, 'unknownCount', num(fa.unknown_count) + '건');
        } else {
            ['confirmedAmount', 'confirmedCount', 'estimated', 'unknownCount'].forEach(function (k) { setText($box, k, '—'); });
            setText($box, 'confirmedAmount', '미지원');
        }
        $('#statsOverviewTitle').text('요약 · ' + (CAT_LABELS[cat] || cat));
        setKpiLink('total', kpiListUrl(cat));
        setKpiLink('completed', kpiListUrl(cat, { status: '완료' }));
        setKpiLink('fines', kpiListUrl(cat, { fine: '과태료' }));
        setKpiLink('warnings', kpiListUrl(cat, { fine: '경고' }));
    }

    // ── 월별 처리 추이(답변일 기준) ──
    function ym(date) { return date.getFullYear() + '-' + String(date.getMonth() + 1).padStart(2, '0'); }
    function nextMonth(key) {
        var y = Number(key.slice(0, 4)), m = Number(key.slice(5, 7));
        return m === 12 ? (y + 1) + '-01' : y + '-' + String(m + 1).padStart(2, '0');
    }

    function trendSvg(s) {
        var answered = {}, fine = {};
        (s.monthly_answered || []).forEach(function (m) { answered[m.month] = Number(m.count || 0); });
        (s.monthly_answered_fine || []).forEach(function (m) { fine[m.month] = Number(m.count || 0); });
        var hasFineSeries = Array.isArray(s.monthly_answered_fine);
        var dataMonths = Object.keys(answered).sort();
        var nowKey = ym(new Date());
        var start, end;
        if (YEAR !== 'all') {
            // 연도를 고르면 그해 1월부터. 끝은 12월과 이번 달 중 이른 달(미래 달을 0건으로 그리지 않는다).
            start = YEAR + '-01';
            end = YEAR + '-12' < nowKey ? YEAR + '-12' : nowKey;
            if (dataMonths.length && dataMonths[dataMonths.length - 1] > end) end = dataMonths[dataMonths.length - 1];
            if (end < start) end = start;
        } else {
            if (!dataMonths.length) return null;
            start = dataMonths[0];
            end = dataMonths[dataMonths.length - 1];
        }
        var keys = [];
        for (var k = start; keys.length < 600; k = nextMonth(k)) {
            keys.push(k);
            if (k === end) break;
        }
        if (!dataMonths.length && YEAR !== 'all') {
            // 선택 연도에 답변이 한 건도 없으면 빈 막대 대신 안내
            return null;
        }
        var max = 1;
        keys.forEach(function (key) { max = Math.max(max, answered[key] || 0, fine[key] || 0); });
        var step = Math.max(1, Math.ceil(max / 4));
        var top = Math.ceil(max / step) * step;
        var box = document.getElementById('statsMonthlyChart');
        var boxW = (box && box.clientWidth) || 360;
        var padL = 34, padR = 10, padT = 18, padB = 26, h = 220;
        var plotH = h - padT - padB;
        var colW = Math.min(72, Math.max(38, (boxW - padL - padR) / keys.length));
        var barW = Math.min(28, Math.max(10, Math.floor(colW * 0.55)));
        var w = Math.max(padL + keys.length * colW + padR, boxW - 2);
        var spansYears = keys[0].slice(0, 4) !== keys[keys.length - 1].slice(0, 4);
        function y(v) { return padT + plotH - (v / top) * plotH; }
        function cx(i) { return padL + i * colW + colW / 2; }
        var out = [];
        out.push('<svg role="img" aria-labelledby="statsTrendTitle statsTrendDesc" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '">');
        out.push('<desc id="statsTrendDesc">답변일 기준 월별 처리 건수와 그중 과태료 건수. 아래 숨은 표에 같은 값이 있습니다.</desc>');
        out.push('<defs><pattern id="srPartialHatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)"><line class="sr-hatch-line" x1="0" y1="0" x2="0" y2="6"/></pattern></defs>');
        for (var t = 0; t <= top; t += step) {
            out.push('<line class="sr-grid" x1="' + padL + '" x2="' + (w - padR) + '" y1="' + y(t) + '" y2="' + y(t) + '"/>');
            out.push('<text x="' + (padL - 6) + '" y="' + (y(t) + 4) + '" text-anchor="end">' + num(t) + '</text>');
        }
        keys.forEach(function (key, i) {
            var v = answered[key] || 0;
            var isNow = key === nowKey;
            var bx = cx(i) - barW / 2;
            var label = key + (isNow ? '(집계 중)' : '') + ' 처리 ' + num(v) + '건' + (hasFineSeries ? ' · 그중 과태료 ' + num(fine[key] || 0) + '건' : '');
            out.push('<rect class="sr-bar' + (isNow ? ' is-partial' : '') + '" x="' + bx + '" y="' + y(v) + '" width="' + barW + '" height="' + Math.max(0, y(0) - y(v)) + '" rx="2"><title>' + esc(label) + '</title></rect>');
            if (v > 0 && colW >= 34) {
                out.push('<text class="sr-bar-value" x="' + cx(i) + '" y="' + (y(v) - 4) + '" text-anchor="middle">' + num(v) + '</text>');
            }
            var mm = Number(key.slice(5, 7));
            var text = spansYears ? key.slice(2, 4) + '.' + key.slice(5, 7) : mm + '월';
            out.push('<text' + (isNow ? ' class="sr-month-now"' : '') + ' x="' + cx(i) + '" y="' + (h - 8) + '" text-anchor="middle">' + text + '</text>');
        });
        out.push('<line class="sr-axis" x1="' + padL + '" x2="' + (w - padR) + '" y1="' + y(0) + '" y2="' + y(0) + '"/>');
        if (hasFineSeries) {
            var pts = keys.map(function (key, i) { return cx(i) + ',' + y(fine[key] || 0); });
            out.push('<polyline class="sr-line" points="' + pts.join(' ') + '"/>');
            keys.forEach(function (key, i) {
                out.push('<circle class="sr-dot" cx="' + cx(i) + '" cy="' + y(fine[key] || 0) + '" r="3"><title>' + esc(key + ' 그중 과태료 ' + num(fine[key] || 0) + '건') + '</title></circle>');
            });
        }
        out.push('</svg>');
        // 스크린리더용 같은 값 표
        var table = ['<table class="visually-hidden"><caption>월별 처리 추이(답변일 기준)</caption><thead><tr><th>월</th><th>처리 건수</th>' + (hasFineSeries ? '<th>그중 과태료</th>' : '') + '</tr></thead><tbody>'];
        keys.forEach(function (key) {
            table.push('<tr><td>' + key + (key === nowKey ? ' (집계 중)' : '') + '</td><td>' + num(answered[key] || 0) + '</td>' + (hasFineSeries ? '<td>' + num(fine[key] || 0) + '</td>' : '') + '</tr>');
        });
        table.push('</tbody></table>');
        return { svg: out.join(''), table: table.join(''), keys: keys, nowKey: nowKey };
    }

    function renderTrend(cat) {
        var s = overview && overview[cat];
        var $box = $('#statsMonthlyChart');
        var $notes = $('#statsTrendNotes');
        if (!s) {
            $box.html('<div class="text-muted small py-4 text-center">요약 자료가 없습니다.</div>');
            $notes.text('');
            return;
        }
        var chart = trendSvg(s);
        if (!chart) {
            $box.html('<div class="text-muted small py-4 text-center">선택한 조건에 답변일이 있는 신고가 없습니다.</div>');
        } else {
            $box.html(chart.svg + chart.table);
            var el = $box[0];
            if (el) el.scrollLeft = el.scrollWidth; // 최근 달부터
        }
        var answeredSum = (s.monthly_answered || []).reduce(function (a, m) { return a + Number(m.count || 0); }, 0);
        var notes = [];
        var unanswered = Number(s.total || 0) - answeredSum;
        if (unanswered > 0) notes.push('답변일 없는 ' + num(unanswered) + '건(미답변 등)은 추이에 없음');
        if (chart && chart.keys.indexOf(chart.nowKey) >= 0) notes.push('이번 달은 집계 중');
        notes.push('처리율은 계산하지 않음(신고월·답변월 기준이 다름)');
        $notes.text(notes.join(' · '));
    }

    // ── 처분 분포 ──
    function renderDisposition(cat) {
        var s = overview && overview[cat];
        var d = s && s.disposition;
        var $list = $('#statsDispositionList');
        var $note = $('#statsDispositionNote');
        if (!s || !d) {
            $list.html('<li class="text-muted small">처분 분포 자료가 없습니다.</li>');
            $note.text('');
            $('#statsDispositionBase').text('');
            return;
        }
        // 처리중(답변 전)은 처분이 없으니 빼고, 답변된 신고를 분모로 한다(2026-09-28).
        var inProgress = Number(d.in_progress || 0);
        var total = Math.max(0, Number(s.total || 0) - inProgress);
        $('#statsDispositionBase').text('답변된 신고 ' + num(total) + '건 기준');
        var html = DISP.map(function (item) {
            var n = Number(d[item.key] || 0);
            var width = total > 0 ? Math.min(100, n / total * 100) : 0;
            return '<li class="sr-hbar' + (n === 0 ? ' is-zero' : '') + '" data-disp="' + item.key + '" title="' + esc(item.hint) + '">' +
                '<span class="sr-hbar-label"><i class="sr-dot-sw" aria-hidden="true"></i><span>' + esc(item.label) + '</span></span>' +
                '<span class="sr-hbar-track" aria-hidden="true"><span class="sr-hbar-fill" style="display:block;width:' + width.toFixed(2) + '%"></span></span>' +
                '<span class="sr-hbar-value">' + num(n) + '건<small>' + pct(n, total) + '</small></span></li>';
        }).join('');
        $list.html(html);
        var results = s.result_distribution || {};
        $('#statsResultList').html([['accept','수용'],['partial','일부수용'],['reject','불수용·기타'],['unknown','결과 미상']].map(function (item) {
            return '<li data-result="' + item[0] + '">' + item[1] + ' <b>' + num(results[item[0]]) + '건</b></li>';
        }).join(''));
        var overlap = Number(d.overlap || 0);
        var notes = [];
        if (inProgress > 0) notes.push('처리 중(답변 전) ' + num(inProgress) + '건은 처분이 없어 뺐습니다.');
        notes.push(overlap > 0
            ? '과태료·경고/범칙금·불수용이 함께 적힌 신고 ' + num(overlap) + '건은 두 항목에 모두 세어, 항목 합이 기준 건수보다 ' + num(overlap) + '건 많습니다.'
            : '여섯 항목은 서로 겹치지 않으며 합계가 기준 건수와 같습니다.');
        $note.text(notes.join(' '));
    }

    // ── 위반 유형 ──
    var typesExpanded = false;
    function renderTypes(cat) {
        var s = overview && overview[cat];
        var types = (s && s.violation_laws) || null;
        var $list = $('#statsTypeList');
        var $more = $('#statsTypeMore');
        if (!s || !types) {
            $list.html('<li class="text-muted small">위반법규 자료가 없습니다.</li>');
            $more.prop('hidden', true);
            $('#statsTypeBase').text('');
            return;
        }
        var total = Number(s.total || 0);
        $('#statsTypeBase').text('저장 법규 조합 ' + num(types.length) + '개 · 복수 법규도 신고당 1건');
        if (!types.length) {
            $list.html('<li class="text-muted small">표시할 신고가 없습니다.</li>');
            $more.prop('hidden', true);
            return;
        }
        var max = types.reduce(function (a, t) { return Math.max(a, Number(t.count || 0)); }, 1);
        var shown = typesExpanded ? types : types.slice(0, TYPE_TOP_N);
        $list.html(shown.map(function (t) {
            var n = Number(t.count || 0);
            var name = t.name ? t.name : '법규 정보 없음';
            var href = kpiListUrl(cat, {law: t.filter, lawExact:'true'});
            return '<li class="sr-hbar" title="' + esc(name) + '">' +
                '<span class="sr-hbar-label">' + (LIST_REPRODUCIBLE ? '<a href="' + esc(href) + '">' + esc(name) + '</a>' : esc(name)) + '</span>' +
                '<span class="sr-hbar-track" aria-hidden="true"><span class="sr-hbar-fill" style="display:block;width:' + (n / max * 100).toFixed(2) + '%"></span></span>' +
                '<span class="sr-hbar-value">' + num(n) + '건<small>' + pct(n, total) + '</small></span></li>';
        }).join(''));
        $('#statsTypeScroll').toggleClass('sr-types-scroll', typesExpanded);
        if (types.length > TYPE_TOP_N) {
            $more.prop('hidden', false).attr('aria-expanded', typesExpanded ? 'true' : 'false')
                .text(typesExpanded ? '상위 ' + TYPE_TOP_N + '개만 보기' : '전체 ' + num(types.length) + '개 법규 조합 보기');
        } else {
            $more.prop('hidden', true);
        }
    }
    $('#statsTypeMore').on('click.srStats', function () {
        typesExpanded = !typesExpanded;
        renderTypes(currentCat);
    });

    // ── 작은 신고 지도(비동기, 최신 요청만 반영) ──
    var mapSeq = 0;
    var mapAbort = null;
    var mapInstance = window.srEarlyMap || null;
    listen(document, 'sr:earlymap', function(event){mapInstance=event.detail;});
    function mapState(html) {
        $('#statsMiniMap').prop('hidden', true);
        $('#statsMapState').prop('hidden', false).html(html);
    }
    function loadMap(cat) {
        var seq = ++mapSeq;
        if (mapAbort) mapAbort.abort();
        mapAbort = window.AbortController ? new AbortController() : null;
        var params = mapParams(cat);
        $('#statsMapOpen').attr('href', '/stats/map?' + params.toString());
        mapState('<span><i class="fas fa-spinner fa-spin me-1"></i>지도를 불러오는 중입니다…</span>');
        $('#statsMapMeta').text('공식 좌표가 있는 신고만 지도에 표시합니다.');
        fetch('/stats/map/points?' + params.toString(), {
            headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
            signal: mapAbort ? mapAbort.signal : undefined,
            cache: 'no-store'
        }).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        }).then(function (payload) {
            if (disposed || seq !== mapSeq) return; // 늦게 온 이전 분류의 응답
            renderMap(cat, payload || {});
        }).catch(function (e) {
            if (disposed || (e && e.name === 'AbortError') || seq !== mapSeq) return;
            mapState('<span>지도를 불러오지 못했습니다.</span><button type="button" class="btn btn-sm btn-outline-secondary" id="statsMapRetry">다시 시도</button>');
            $('#statsMapMeta').text('요약·표는 지도와 별개로 정상 집계되었습니다.');
        });
    }
    $(document).on('click.srStats', '#statsMapRetry', function () { loadMap(currentCat); });

    function renderMap(cat, payload) {
        var meta = payload.meta || {};
        var points = payload.points || [];
        var totalReports = Number(meta.total_reports || 0);
        var geocoded = Number(meta.geocoded_reports || 0);
        if (mapInstance && mapInstance.map) {
            mapInstance.map.remove();
            mapInstance = null;
        }
        $('#statsMapMeta').html('지도 표시 <b class="sr-num">' + num(geocoded) + '</b>건 / 대상 <b class="sr-num">' + num(totalReports) + '</b>건' +
            (totalReports > geocoded ? ' · 공식 좌표 없는 ' + num(totalReports - geocoded) + '건은 지도에만 없음(통계에는 포함)' : ''));
        if (!points.length) {
            mapState('<span>이 조건에는 공식 좌표가 있는 신고가 없습니다.</span>');
            return;
        }
        $('#statsMapState').prop('hidden', true);
        var el = document.getElementById('statsMiniMap');
        el.hidden = false;
        // 팝업 '리스트 보기'에도 통계 조건(법규·답변 연도→답변일 범위·상세 조건)을 잇는다
        var listParams = {};
        listBaseParams().forEach(function (value, key) { listParams[key] = value; });
        if(agencyQuery) {listParams.agency=agencyQuery; if(FILTERS.agencyExact) listParams.agencyExact='true';}
        mapInstance = window.SrReportMap && window.SrReportMap.create(el, points, {
            category: cat,
            dedupeMode: DATA.dedupeMode,
            listParams: listParams,
            listReproducible: LIST_REPRODUCIBLE,
            scrollWheelZoom: false
            ,viewportURL: '/stats/map/points?' + mapParams(cat).toString()
        });
        performance.mark('sr-map-ready');
        if (!mapInstance) {
            mapState('<span>지도 라이브러리를 불러오지 못했습니다.</span><button type="button" class="btn btn-sm btn-outline-secondary" id="statsMapRetry">다시 시도</button>');
        }
    }
    if (window.ResizeObserver) {
        resizeObserver = new ResizeObserver(function () {
            if (mapInstance) mapInstance.invalidateSize();
        });
        resizeObserver.observe(document.getElementById('statsMapCard'));
    }

    // ── 상세 표(DataTables) ──
    var LANG_KO = {
        emptyTable: '표시할 기관·담당자가 없습니다', info: '_START_–_END_ / 전체 _TOTAL_행', infoEmpty: '0행',
        infoFiltered: '(검색 전 _MAX_행)', lengthMenu: '_MENU_행씩', loadingRecords: '불러오는 중…', processing: '처리 중…',
        search: '검색:', zeroRecords: '검색 결과가 없습니다',
        paginate: { first: '처음', last: '마지막', next: '다음', previous: '이전' },
        aria: { sortAscending: ': 오름차순 정렬', sortDescending: ': 내림차순 정렬' }
    };
    // 합계 행. 열을 숨기면 td 순서가 바뀌므로 DataTables API 로 열 번호를 지정해 읽고 쓴다(D-STAT-7).
    var COUNT_KEYS = Calc.COUNT_KEYS;
    function makeDrawCallback(cfg) {
        // 행 숫자는 표를 처음 그릴 때 한 번만 셀 속성에서 읽고, 합계는 검색 모집단이 바뀔 때만 다시 더한다.
        // 페이지 이동·정렬·열 표시 변경에서는 직전 합계를 그대로 쓴다(기술일지 B-07, 계획 P16a typed rows).
        var typedRows = null, lastKey = null, last = null;
        function readRows(api) {
            function attr(rowIdx, col, name) { return $(api.cell(rowIdx, col).node()).attr(name); }
            typedRows = [];
            api.rows().indexes().each(function (r) {
                var row = {
                    total: parseInt(attr(r, cfg.total, 'data-order'), 10) || 0,
                    fine: parseInt(attr(r, cfg.fine, 'data-order'), 10) || 0,
                    fineCount: parseInt(attr(r, cfg.fine, 'data-count'), 10) || 0,
                    est: parseInt(attr(r, cfg.est, 'data-order'), 10) || 0,
                    estCount: parseInt(attr(r, cfg.est, 'data-count'), 10) || 0,
                    avgD: parseFloat(attr(r, cfg.avg, 'data-order')),
                    avgN: parseInt(attr(r, cfg.avg, 'data-count'), 10) || 0,
                    rAvg: parseFloat(attr(r, cfg.rating, 'data-order')),
                    rCount: parseInt(attr(r, cfg.rating, 'data-count'), 10) || 0
                };
                COUNT_KEYS.forEach(function (k) { row[k] = parseInt(attr(r, cfg[k], 'data-order'), 10) || 0; });
                typedRows[r] = row;
            });
        }
        return function () {
            var api = this.api();
            if (!typedRows) readRows(api);
            var applied = api.rows({ search: 'applied' }).indexes();
            var key = String(searchTerm || '') + '\u0000' + applied.length;
            if (key !== lastKey) {
                var rows = [];
                applied.each(function (r) { rows.push(typedRows[r]); });
                last = Calc.summarizeRows(rows);
                lastKey = key;
            }
            var sums = last.sums, wDays = last.wDays, wBase = last.wBase, wRating = last.wRating, wRatingBase = last.wRatingBase;

            function foot(col) { return $(api.column(col).footer()); }
            foot(cfg.total).html('<b>' + num(sums.total) + '</b>건');
            foot(cfg.avg).text(wBase > 0 ? (wDays / wBase).toFixed(1) + '일' : '—');
            foot(cfg.fine).text(sums.fine > 0 ? won(sums.fine) : '—');
            foot(cfg.est).html(sums.estCount > 0 ? '<span class="sr-metric-n">' + won(sums.est) + '</span><span class="sr-metric-pct">' + num(sums.estCount) + '건</span>' : '—');
            COUNT_KEYS.forEach(function (k) {
                var n = k === 'fines' ? '<span class="sr-metric-n sr-fines-n">' + num(sums[k]) + '</span>' : '<span class="sr-metric-n">' + num(sums[k]) + '</span>';
                foot(cfg[k]).html(n + '<span class="sr-metric-pct">' + pct(sums[k], sums.total) + '</span>');
            });
            foot(cfg.rating).html(wRatingBase > 0
                ? '<span class="sr-metric-n"><span class="sr-rating-star" aria-hidden="true">★</span> ' + (wRating / wRatingBase).toFixed(2) + '</span><span class="sr-metric-pct">' + num(wRatingBase) + '명</span>'
                : '—');
            if (isActiveTable(api)) {
                updateScope(api);
                markSelection();
            }
        };
    }
    function columnMap(lead) {
        return { total: lead, avg: lead + 1, fine: lead + 2, est: lead + 3, fines: lead + 4, warn: lead + 5, rejects: lead + 6,
                 dispositionUnknown: lead + 7, noPenalty: lead + 8, unclassified: lead + 9, rating: lead + 10 };
    }

    // 상세표 이름 검색: 기관명(담당자 보기는 담당자명도)에만 적용. 숫자 열은 검색하지 않는다.
    function statsSearch(settings, rowData) {
        var pane = settings.nTable.closest && settings.nTable.closest('.stats-pane');
        if (!pane || !searchTerm) return true;
        var term = searchTerm.toLowerCase();
        var person = /person$/.test(pane.getAttribute('data-type') || '');
        var hay = (String(rowData[0] || '') + (person ? ' ' + String(rowData[1] || '') : '')).toLowerCase();
        return hay.indexOf(term) !== -1;
    }
    $.fn.dataTable.ext.search.push(statsSearch);

    var savedTables = (savedView && savedView.tables) || {};
    function ensureTable(element) {
        if ($.fn.dataTable.isDataTable(element)) return $(element).DataTable();
        var person = /person$/.test($(element).closest('.stats-pane').attr('data-type'));
        var lead = person ? 2 : 1;
        var saved = savedTables[element.id] || {};
        var api = $(element).DataTable({
            order: Array.isArray(saved.order) && saved.order.length ? saved.order : [[lead, 'desc']],
            pageLength: pageLength,
            language: LANG_KO,
            dom: 'rt<"sr-dt-foot"ip>',
            autoWidth: false,
            drawCallback: makeDrawCallback(columnMap(lead))
        });
        if (saved.page) {
            api.page(saved.page).draw(false);
        }
        api.on('order.dt.srStats', function () { if (isActiveTable(api)) updateSortLabel(api); });
        return api;
    }
    $('#statsPageLength').val(String(pageLength));
    $('#statsTableSearch').val(searchTerm);

    function activeTableEl() { return $('#' + currentCat + '-' + currentType).find('table').first(); }
    function getActiveTableApi() {
        var $t = activeTableEl();
        return $t.length ? ensureTable($t[0]) : null;
    }
    function isActiveTable(api) {
        var $t = activeTableEl();
        return $t.length && api.table().node() === $t[0];
    }

    function updateSortLabel(api) {
        var order = api.order();
        if (!order || !order.length) { $('#statsSortLabel').text(''); return; }
        var col = order[0][0];
        var head = String($(api.column(col).header()).text() || '').replace(/\s+/g, ' ').trim();
        if (head === '★') head = '별점';
        $('#statsSortLabel').html('정렬: <b>' + esc(head) + '</b> ' + (order[0][1] === 'asc' ? '오름차순' : '내림차순') +
            ' <span class="text-muted">· 순서는 정렬 결과이며 평가 순위가 아닙니다</span>');
    }

    function sumTotals(list) { return (list || []).reduce(function (a, r) { return a + Number(r.total || 0); }, 0); }
    function updateScope(api) {
        var shown = api.rows({ search: 'applied' }).count();
        var all = api.rows().count();
        var unit = isPersonType(currentType) ? '명' : '곳';
        var parts = ['<b>' + esc(CAT_LABELS[currentCat]) + ' · ' + esc(TYPE_LABELS[currentType]) + '</b>', '현재 조건'];
        parts.push(searchTerm ? '검색 “' + esc(searchTerm) + '” ' + num(shown) + unit + ' / 전체 ' + num(all) + unit : '전체 ' + num(all) + unit);
        parts.push('답변 완료 신고만 집계');
        var s = overview && overview[currentCat];
        var rows = DATA.rows && DATA.rows[currentCat];
        if (s && rows) {
            var base = isPersonType(currentType) ? sumTotals(rows.person) : sumTotals(rows.agency);
            var excluded = Math.max(0, Number(s.total || 0) - Number(s.completed || 0));
            if (excluded > 0) parts.push('답변 완료 전·그 외 상태 ' + num(excluded) + '건은 표에서 제외');
            var missing = Math.max(0, Number(s.completed || 0) - base);
            if (missing > 0) {
                parts.push(isPersonType(currentType)
                    ? '처리기관·담당자가 없는 ' + num(missing) + '건은 담당자 표에 없음'
                    : '처리기관이 없는 ' + num(missing) + '건은 기관 표에 없음');
            }
        }
        if (/^police/.test(currentType)) parts.push("기관명에 '경찰'이 들어간 기관만");
        if (/^other/.test(currentType)) parts.push("기관명에 '경찰'이 없는 기관만");
        $('#statsDetailScope').html(parts.join(' · '));
    }

    // 열 선택(sessionStorage stats_column_visibility — 머리글 글자가 키)
    var columnVisibilityStorageKey = 'stats_column_visibility';
    var columnVisibilityState = {};
    try {
        columnVisibilityState = JSON.parse(storageGet(sessionStorage, columnVisibilityStorageKey) || '{}') || {};
    } catch (e) { columnVisibilityState = {}; }
    function normalizeColumnText(text) { return String(text || '').replace(/\s+/g, ' ').trim(); }
    function getColumnLabel(headers, index) {
        var label = normalizeColumnText(headers[index]);
        if (label === '★') return '별점';
        if (label === '비율') {
            var prev = normalizeColumnText(headers[index - 1]);
            if (prev === '★') prev = '별점';
            return (prev || '비율') + ' 비율';
        }
        return label;
    }
    function saveColumnVisibilityState() { storageSet(sessionStorage, columnVisibilityStorageKey, JSON.stringify(columnVisibilityState)); }
    function getColumnMeta(api) {
        var headers = api.columns().header().toArray().map(function (th) { return normalizeColumnText($(th).text()); });
        return headers.map(function (_, index) {
            var label = getColumnLabel(headers, index);
            return { index: index, key: label, label: label };
        });
    }
    function isColumnVisible(meta) {
        return !Object.prototype.hasOwnProperty.call(columnVisibilityState, meta.key) || columnVisibilityState[meta.key] !== false;
    }
    function applyColumnVisibility(api) {
        var meta = getColumnMeta(api);
        meta.forEach(function (item) { api.column(item.index).visible(isColumnVisible(item), false); });
        return meta;
    }
    function renderColumnControls(meta) {
        var $box = $('#statsColumnCheckboxes');
        $box.empty();
        var shown = meta.filter(isColumnVisible).length;
        $('#statsColumnCount').text('(' + shown + '/' + meta.length + ')');
        meta.forEach(function (item) {
            var inputId = 'stats-column-' + currentType.replace(/[^a-z0-9]+/gi, '-') + '-' + item.index;
            var $item = $('<div class="form-check stats-column-item"></div>');
            var $input = $('<input type="checkbox" class="form-check-input stats-column-checkbox">')
                .attr('id', inputId).attr('data-column-key', item.key).prop('checked', isColumnVisible(item));
            var $label = $('<label class="form-check-label"></label>').attr('for', inputId).text(item.label);
            $item.append($input, $label);
            $box.append($item);
        });
        $('#statsColumnScope').text(CAT_LABELS[currentCat] + ' / ' + TYPE_LABELS[currentType]);
    }
    function syncActiveTableColumns() {
        var api = getActiveTableApi();
        if (!api) {
            $('#statsColumnCheckboxes').empty();
            $('#statsColumnScope').text('');
            return;
        }
        var meta = applyColumnVisibility(api);
        api.columns.adjust().draw(false);
        renderColumnControls(meta);
    }
    $('#statsColumnCheckboxes').on('change.srStats', '.stats-column-checkbox', function () {
        var key = $(this).attr('data-column-key');
        var hadFocus = document.activeElement === this;
        columnVisibilityState[key] = $(this).is(':checked');
        saveColumnVisibilityState();
        syncActiveTableColumns();
        // 목록을 다시 그려도 방금 바꾼 열 체크박스로 포커스를 돌린다 — 키보드로 이어서 고를 수 있게(기술일지 C09)
        if (hadFocus) {
            var again = $('#statsColumnCheckboxes .stats-column-checkbox').filter(function () {
                return $(this).attr('data-column-key') === key;
            }).get(0);
            if (again) again.focus();
        }
    });
    $('#statsColumnsSelectAll').on('click.srStats', function () {
        var api = getActiveTableApi();
        if (!api) return;
        getColumnMeta(api).forEach(function (item) { columnVisibilityState[item.key] = true; });
        saveColumnVisibilityState();
        syncActiveTableColumns();
    });
    var columnsOpenKey = 'stats_columns_open';
    function setColumnsOpen(open) {
        $('#statsColumnsToggle').attr('aria-expanded', open ? 'true' : 'false');
        $('#statsColumnBody').prop('hidden', !open);
        storageSet(sessionStorage, columnsOpenKey, open ? '1' : '0');
    }
    $('#statsColumnsToggle').on('click.srStats', function () { setColumnsOpen($(this).attr('aria-expanded') !== 'true'); });
    setColumnsOpen(storageGet(sessionStorage, columnsOpenKey) === '1');

    // 검색·행 수: 상세 표에만 적용
    var searchTimer = null;
    $('#statsTableSearch').on('input.srStats search.srStats', function () {
        var value = $(this).val();
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(function () {
            searchTerm = String(value || '').trim();
            var api = getActiveTableApi();
            if (api) api.draw();
        }, 150);
    });
    $('#statsPageLength').on('change.srStats', function () {
        pageLength = Number($(this).val()) || 50;
        var api = getActiveTableApi();
        if (api) api.page.len(pageLength).draw();
    });

    // ── 선택 항목 상세 ──
    function wideLayout() {
        var wrap = document.querySelector('.sr-detail-body-wrap');
        return wrap && wrap.clientWidth >= WIDE_PANEL_MIN;
    }
    var drawer = null;
    function getDrawer() {
        var el = document.getElementById('statsDetailDrawer');
        if (!drawer && el && window.bootstrap) drawer = window.bootstrap.Offcanvas.getOrCreateInstance(el);
        return drawer;
    }

    function panelHtml(row, cat, type, href) {
        var person = isPersonType(type);
        var total = Number(row.total || 0);
        var kind = (person ? '담당자' : '기관') + ' · ' + CAT_LABELS[cat] + ' · ' + TYPE_LABELS[type];
        var html = ['<div class="sr-panel">'];
        html.push('<div class="sr-panel-head"><div class="sr-panel-names"><div class="sr-panel-kind">' + esc(kind) + '</div>');
        html.push('<h3 class="sr-panel-name" id="statsPanelName">' + esc(person ? row.person : row.agency) + '</h3>');
        if (person) html.push('<div class="sr-panel-sub">소속 ' + esc(row.agency) + '</div>');
        html.push('</div><button type="button" class="btn-close" data-stats-close aria-label="선택 해제"></button></div>');
        html.push('<div class="sr-panel-total"><span class="sr-big">' + num(total) + '</span><span>건</span></div>');
        html.push('<div class="sr-stackbar" aria-hidden="true">' + DISP.map(function (item) {
            var n = Number(row[item.key] || 0);
            return n > 0 && total > 0 ? '<span data-disp="' + item.key + '" style="width:' + (n / total * 100).toFixed(2) + '%"></span>' : '';
        }).join('') + '</div>');
        html.push('<div class="sr-panel-grid">' + DISP.map(function (item) {
            var n = Number(row[item.key] || 0);
            return '<div class="sr-panel-cell" data-disp="' + item.key + '" title="' + esc(item.hint) + '"><span class="sr-k">' + esc(item.label) + '</span>' +
                '<span class="sr-v">' + num(n) + '건<small>' + pct(n, total) + '</small></span></div>';
        }).join('') + '</div>');
        var fines = Number(row.fines || 0), unknown = Number(row.fine_amount_unknown || 0);
        html.push('<ul class="sr-panel-list">');
        html.push('<li><span>평균 처리기간</span><b>' + (row.avg_days == null ? '—' : Number(row.avg_days).toFixed(1) + '일') +
            (row.avg_days_count != null ? ' <small class="text-muted fw-normal">(표본 ' + num(row.avg_days_count) + '건)</small>' : '') + '</b></li>');
        html.push('<li><span>별점</span><b>' + (row.avg_rating == null ? '평가 없음' : '★ ' + Number(row.avg_rating).toFixed(2) + ' <small class="text-muted fw-normal">(' + num(row.rating_count) + '명)</small>') + '</b></li>');
        html.push('<li><span>확정 과태료</span><b>' + won(row.total_fine_amount) + ' <small class="text-muted fw-normal">(' + num(Math.max(0, fines - unknown)) + '건)</small></b></li>');
        html.push('<li><span>금액 미확인 과태료</span><b>' + num(unknown) + '건</b></li>');
        html.push('<li><span>추정 과태료(법정 최저)</span><b>' + (row.estimated_fine_count ? won(row.estimated_fine_amount) + ' <small class="text-muted fw-normal">(' + num(row.estimated_fine_count) + '건)</small>' : '—') + '</b></li>');
        html.push('</ul>');
        var mapHref = '/stats/map?' + mapParams(cat, { agency: row.agency, agencyKey: row.agency_key || 'src:-:' + row.agency, person: person ? row.person : '' }).toString();
        html.push('<div class="sr-panel-actions">');
        html.push('<a class="btn btn-primary btn-sm" data-stats-nav href="' + esc(href) + '"><i class="fas fa-list-ul me-1"></i>해당 신고 내역 보기</a>');
        html.push('<a class="btn btn-outline-secondary btn-sm" data-stats-nav href="' + esc(mapHref) + '"><i class="fas fa-map-location-dot me-1"></i>지도에서 보기</a>');
        html.push('</div>');
        var note = '표와 같은 값입니다. 목록·지도에는 답변 완료·분류·' + (person ? '기관·담당자' : '기관') + '·답변 연도' + (FILTERS.law ? '·위반법규' : '') + ' 조건이 함께 넘어갑니다.';
        if (FILTERS.excludePolice || FILTERS.onlyPolice) note += ' 경찰기관 제외/만 조건은 목록 주소로 넘기지 못합니다(이 행은 이미 한 기관이라 결과는 같습니다).';
        html.push('<div class="sr-panel-note">' + esc(note) + '</div>');
        html.push('</div>');
        return html.join('');
    }

    function markSelection() {
        $('#statsTabsContent tr.sr-drill.is-selected').removeClass('is-selected').removeAttr('aria-current');
        if (!selected || selected.cat !== currentCat || selected.type !== currentType) return;
        activeTableEl().find('tbody tr.sr-drill').filter(function () {
            return $(this).attr('data-key') === selected.key;
        }).addClass('is-selected').attr('aria-current', 'true');
    }

    function clearSelection() {
        selected = null;
        markSelection();
        $('#statsDetailBody').removeClass('has-panel');
        $('#statsDetailPanel').empty();
        $('#statsDetailDrawerBody').empty();
        var d = getDrawer();
        if (d && document.getElementById('statsDetailDrawer').classList.contains('show')) d.hide();
    }

    function selectRow($tr, openDrawer) {
        var key = $tr.attr('data-key');
        var row = rowFor(currentCat, currentType, key);
        if (!row) return;
        selected = { cat: currentCat, type: currentType, key: key };
        markSelection();
        var html = panelHtml(row, currentCat, currentType, $tr.attr('data-href'));
        if (wideLayout()) {
            $('#statsDetailPanel').html(html);
            $('#statsDetailBody').addClass('has-panel');
            $('#statsDetailDrawerBody').empty();
        } else {
            $('#statsDetailBody').removeClass('has-panel');
            $('#statsDetailPanel').empty();
            $('#statsDetailDrawerBody').html(html);
            if (openDrawer !== false) {
                var d = getDrawer();
                if (d) d.show();
            }
        }
    }

    $('#statsTabsContent').on('click.srStats', 'tr.sr-drill', function () { selectRow($(this)); });
    $('#statsTabsContent').on('keydown.srStats', 'tr.sr-drill', function (e) {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            selectRow($(this));
        }
    });
    $(document).on('click.srStats', '[data-stats-close]', function () {
        var $row = activeTableEl().find('tr.sr-drill.is-selected').first();
        clearSelection();
        if ($row.length) $row.trigger('focus');
    });
    $(document).on('click.srStats', '[data-stats-nav]', function () { saveView(); });
    // 드로어를 닫으면 선택도 푼다(좁은 화면). 넓은 화면으로 바뀌어 옆 패널로 옮긴 경우는 그대로 둔다.
    listen(document.getElementById('statsDetailDrawer'), 'hidden.bs.offcanvas', function () {
        if (selected && !wideLayout()) {
            var $row = activeTableEl().find('tr.sr-drill.is-selected').first();
            selected = null;
            markSelection();
            $('#statsDetailDrawerBody').empty();
            if ($row.length) $row.trigger('focus');
        }
    });

    // ── 보기 상태 저장·복원(목록·지도에서 돌아올 때) ──
    function saveView() {
        var tables = Object.assign({}, savedTables);
        $('#statsTabsContent .stats-pane table').each(function () {
            if (!$.fn.dataTable.isDataTable(this)) return;
            var api = $(this).DataTable();
            tables[this.id] = { order: api.order(), page: api.page() };
        });
        storageSet(sessionStorage, VIEW_KEY, JSON.stringify({
            qs: window.location.search, search: searchTerm, len: pageLength, tables: tables,
            selected: selected, scrollY: window.scrollY
        }));
    }
    listen(window, 'pagehide', function () { window.SrStats.dispose(); });

    // ── 분류·보기 전환 ──
    function setPressed($btns, isOn) {
        $btns.each(function () {
            var on = isOn($(this));
            $(this).toggleClass('active', on).attr('aria-pressed', on ? 'true' : 'false');
        });
    }
    function showPane() {
        $('.stats-pane').hide();
        $('#' + currentCat + '-' + currentType).show();
        var api = getActiveTableApi();
        if (api && api.page.len() !== pageLength) api.page.len(pageLength);
        syncActiveTableColumns(); // draw 포함(검색어·합계·범위 갱신)
        if (api) updateSortLabel(api);
        storageSet(sessionStorage, 'stats_cat', currentCat);
        storageSet(sessionStorage, 'stats_type', currentType);
    }
    function updateCategory() {
        setPressed($('.stats-cat-btn'), function ($b) { return $b.data('cat') === currentCat; });
        setPressed($('.stats-type-btn'), function ($b) { return $b.data('type') === currentType; });
        renderLawButtons(currentCat);
        renderOverview(currentCat);
        renderTrend(currentCat);
        renderDisposition(currentCat);
        renderTypes(currentCat);
    }
    $('.stats-cat-btn').on('click.srStats', function () {
        var next = $(this).data('cat');
        if (next === currentCat) return;
        currentCat = next;
        clearSelection(); // 이전 분류의 선택은 새 조건의 결과가 아니다
        typesExpanded = false;
        updateCategory();
        showPane();
        loadMap(currentCat);
    });
    $('.stats-type-btn').on('click.srStats', function () {
        var next = $(this).data('type');
        if (next === currentType) return;
        currentType = next;
        clearSelection();
        setPressed($('.stats-type-btn'), function ($b) { return $b.data('type') === currentType; });
        showPane();
    });

    // ── 위반법규 선택(검색 가능한 목록, 값은 서버 available_laws 원문) ──
    var activeLaw = DATA.activeLaw || null;
    function naturalCompare(a, b) { return Calc.naturalCompare(a, b); }
    function lawUrl(law) { return Calc.lawUrl(window.location.search, law); }
    function renderLawButtons(cat) {
        var $sb = $('#statsLawSidebar');
        $sb.empty();
        var laws = ((DATA.laws && DATA.laws[cat]) || []).slice().sort(naturalCompare);
        function mkBtn(label, isActive, extraClass, law) {
            return $('<button type="button" class="btn btn-sm"></button>')
                .addClass(extraClass || '').toggleClass('active', !!isActive)
                .attr('aria-pressed', isActive ? 'true' : 'false').attr('data-law-label', label.toLowerCase())
                .text(label)
                .on('click.srStats', function () { window.location.href = lawUrl(law); });
        }
        $sb.append(mkBtn('전체', !activeLaw, '', null));
        if ((DATA.hasEmptyLaw && DATA.hasEmptyLaw[cat]) || activeLaw === '__없음__') {
            $sb.append(mkBtn('없음', activeLaw === '__없음__', 'sr-law-none', '__없음__'));
        }
        // 다른 분류에서 고른 법규는 이 분류 목록에 없어도 적용 중임을 보인다
        if (activeLaw && activeLaw !== '__없음__' && laws.indexOf(activeLaw) === -1) laws.unshift(activeLaw);
        laws.forEach(function (law) { $sb.append(mkBtn(law, activeLaw === law, '', law)); });
        if (!laws.length) $sb.append('<div class="sr-law-empty">이 분류에는 위반법규 값이 없습니다.</div>');
        $sb.append('<div class="sr-law-empty" data-law-nomatch hidden>일치하는 법규가 없습니다.</div>');
        filterLaws();
    }
    function filterLaws() {
        var q = String($('#statsLawSearch').val() || '').trim().toLowerCase();
        var any = false;
        $('#statsLawSidebar button').each(function () {
            var hit = !q || String($(this).attr('data-law-label') || '').indexOf(q) !== -1;
            $(this).prop('hidden', !hit);
            any = any || hit;
        });
        $('#statsLawSidebar [data-law-nomatch]').prop('hidden', any);
    }
    $('#statsLawSearch').on('input.srStats search.srStats', filterLaws);
    listen(document.getElementById('statsLawToggle'), 'shown.bs.dropdown', function () {
        var input = document.getElementById('statsLawSearch');
        if (input) input.focus();
    });

    // 연도: 현재 주소 조건을 유지한 채 year 만 바꾼다
    $('.stats-year-btn').on('click.srStats', function () {
        var year = String($(this).data('year'));
        var params = new URLSearchParams(window.location.search);
        if (year === 'all') params.delete('year'); else params.set('year', year);
        var qs = params.toString();
        window.location.href = '/stats' + (qs ? '?' + qs : '');
    });

    // ── CSV 내보내기: 검색·정렬이 적용된 현재 보기의 전체 행(모든 쪽). 확정/추정 금액은 별도 열 ──
    $('#statsExportCsv').on('click.srStats', function () {
        var api = getActiveTableApi();
        if (!api) return;
        var person = isPersonType(currentType);
        var rows = [];
        api.rows({ search: 'applied', order: 'applied' }).nodes().each(function (tr) {
            var row = rowFor(currentCat, currentType, $(tr).attr('data-key'));
            if (row) rows.push(Calc.csvRow(row, person, DISP));
        });
        var name = ['통계', CAT_LABELS[currentCat], TYPE_LABELS[currentType], YEAR === 'all' ? '전체연도' : YEAR + '년'].join('_').replace(/\s+/g, '') + '.csv';
        var blob = new Blob([Calc.csvText(Calc.csvHeader(person, DISP), rows)], { type: 'text/csv;charset=utf-8' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    });

    // ── 상세 조건(오프캔버스) 기존 동작 ──
    function validateTimeFormat(input) {
        var val = $(input).val().trim();
        var timeRegex = /^([01]\d|2[0-3]):?([0-5]\d)$/;
        if (val) {
            if (!timeRegex.test(val)) {
                window.alert('발생시간 형식이 올바르지 않습니다. (예: 14:30)');
                $(input).val('');
            } else if (val.length === 4 && val.indexOf(':') === -1) {
                $(input).val(val.slice(0, 2) + ':' + val.slice(2));
            }
        }
    }
    $('input[name="occurTimeStart"], input[name="occurTimeEnd"]').on('change.srStats', function () { validateTimeFormat(this); });
    $('.filter-police').on('change.srStats', function () {
        if ($(this).is(':checked')) $('.filter-police').not(this).prop('checked', false);
    });
    // 상세 조건 폼 제출 시 빈 값 제외(hidden year 제외). 다른 form(테마 전환 등)에는 걸지 않는다.
    $('#offcanvasSearch form').on('submit.srStats', function () {
        $(this).find('input').not('[name="year"]').each(function () {
            if (!$(this).val()) $(this).attr('disabled', 'disabled');
        });
        return true;
    });

    window.SrStats.dispose = function () {
        if (disposed) return;
        saveView(); disposed = true; mapSeq++;
        if (mapAbort) mapAbort.abort();
        if (resizeObserver) resizeObserver.disconnect();
        if (sunwiWidget) sunwiWidget.dispose();
        window.clearTimeout(searchTimer);
        if (scrollFrame !== null) window.cancelAnimationFrame(scrollFrame);
        removers.forEach(function (remove) { remove(); });
        $(document).off('.srStats');
        $('.sr-stats-page').find('*').addBack().off('.srStats');
        var index = $.fn.dataTable.ext.search.indexOf(statsSearch);
        if (index !== -1) $.fn.dataTable.ext.search.splice(index, 1);
        $('#statsTabsContent table').each(function () {
            if ($.fn.dataTable.isDataTable(this)) $(this).DataTable().off('.srStats').destroy();
            // The loaded DataTables build leaves info.dt handlers on a retained
            // table after destroy(). They retain the old mount's data/context.
            $(this).off('.dt .DT');
        });
        if (mapInstance && mapInstance.map) mapInstance.map.remove();
        if (window.srEarlyMap === mapInstance) window.srEarlyMap = null;
        mapInstance = null;
    };

    // ── 시작 ──
    updateCategory();
    showPane();
    if (!window.srEarlyMap && !window.srMapLoading) loadMap(currentCat);
    if (savedView && savedView.selected && savedView.selected.cat === currentCat && savedView.selected.type === currentType) {
        var $row = activeTableEl().find('tbody tr.sr-drill').filter(function () { return $(this).attr('data-key') === savedView.selected.key; }).first();
        if ($row.length) selectRow($row, false);
    }
    if (savedView && typeof savedView.scrollY === 'number') {
        scrollFrame = window.requestAnimationFrame(function () { if (!disposed) window.scrollTo(0, savedView.scrollY); });
    }
    // 다음 방문 기준을 새로 잡는다(조건이 다르면 위에서 이미 버렸다)
    storageSet(sessionStorage, VIEW_KEY, 'null');

    // 전국 안전신고 현황
    var sunwiEl = document.getElementById('sunwiData');
    if (window.SrSunwiWidget && sunwiEl) {
        sunwiWidget = window.SrSunwiWidget.init({
            contentEl: document.getElementById('sunwiContent'),
            updatedAtEl: document.getElementById('sunwiUpdatedAtLabel'),
            initialData: JSON.parse(sunwiEl.textContent || '{}')
        });
    }
    }
    window.SrStats = { mount: mount, dispose: null };
    mount();
})(window.jQuery);
