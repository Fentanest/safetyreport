/* 신고 목록 표(data_table.html) 화면 컨트롤러(EO R-14: 템플릿 인라인 스크립트를 옮겼다).
   서버 값은 템플릿의 JSON bootstrap(#srDataTableBootstrap: records·tableId)으로만 받는다. 행 판정은 list-predicates.js,
   셀 렌더러는 data-table-cells.js, 상태 규칙은 report-policy.js. DOM ID·전역(_tableData 등)은 예전 그대로다. */
var SR_DATA_TABLE_BOOT = JSON.parse(document.getElementById('srDataTableBootstrap').textContent);
var SR_TABLE_ID = SR_DATA_TABLE_BOOT.tableId;
// 서버에서 JSON으로만 전달 — Jinja2 HTML 행 렌더링 없음
var _tableData = SR_DATA_TABLE_BOOT.records;

$(document).ready(function() {
    const urlParams = new URLSearchParams(window.location.search);
    const qAgency = urlParams.get('agency');
    const qPerson = urlParams.get('person');
    const qCar = urlParams.get('car');
    const qLaw = urlParams.get('law');
    const qLocation = urlParams.get('location');

    if (qAgency) $('#searchAgency').val(qAgency);
    if (qPerson) $('#searchPerson').val(qPerson);
    if (qCar) $('#searchCar').val(qCar);
    // '__없음__'(법규 없는 신고)은 서버가 이미 걸렀다. 검색칸에 넣으면 글자 그대로 다시 걸러 0건이 된다(D-STAT-8).
    if (qLaw && qLaw !== '__없음__') $('#searchLaw').val(qLaw);
    if (qLocation) $('#searchLocation').val(qLocation);
    ['excludePolice', 'onlyPolice'].forEach(key => { if (urlParams.get(key) === 'true') $('#' + key).prop('checked', true); });
    // 통계 화면에서 넘어온 조건(2026-09-28): 답변 연도는 답변일 범위로, 상세 조건은 같은 이름의 검색칸으로.
    [['reportName', '#searchReportName'],
     ['reportDateStart', '#searchReportDateStart'], ['reportDateEnd', '#searchReportDateEnd'],
     ['occurDateStart', '#searchOccurDateStart'], ['occurDateEnd', '#searchOccurDateEnd'],
     ['responseDateStart', '#searchResponseDateStart'], ['responseDateEnd', '#searchResponseDateEnd'],
     ['occurTimeStart', '#searchOccurTimeStart'], ['occurTimeEnd', '#searchOccurTimeEnd']].forEach(function (pair) {
        const value = urlParams.get(pair[0]);
        if (value) $(pair[1]).val(value);
    });

    const isDuplicate = SR_TABLE_ID === 'duplicateTable';
    const offset = isDuplicate ? 1 : 0;
    const searchState = { statuses: [], ratings: [] };
    var tableReady = false;
    const preferredStatusOrder = ['수용', '일부수용', '불수용', '처리중', '보완요청', '취하', '기타', '답변완료'];
    const ratingOptions = [
        { value: '__none__', label: '없음' },
        { value: '1', label: '1점' },
        { value: '2', label: '2점' },
        { value: '3', label: '3점' },
        { value: '4', label: '4점' },
        { value: '5', label: '5점' }
    ];

    function canonicalStatus(value) {
        return window.SrReportPolicy.displayStatus(value);
    }

    function summarizeSelections(options, values) {
        const selectedSet = new Set(values);
        const labels = options.filter(option => selectedSet.has(option.value)).map(option => option.label);
        if (!labels.length) return '전체';
        if (labels.length <= 2) return labels.join(', ');
        return labels[0] + ' 외 ' + (labels.length - 1) + '개';
    }

    function sortSelections(values, options) {
        const orderMap = new Map(options.map((option, index) => [option.value, index]));
        values.sort((a, b) => (orderMap.get(a) ?? 999) - (orderMap.get(b) ?? 999));
    }

    function renderMultiSelect($root, options, values) {
        const selectedSet = new Set(values);
        const $menu = $root.find('.multi-select-menu');
        $root.find('.selection-text').text(summarizeSelections(options, values));
        $menu.empty();

        const $allButton = $('<button type="button" class="dropdown-item multi-select-option"></button>');
        if (!selectedSet.size) $allButton.addClass('active');
        $allButton.append('<span>전체</span><span class="multi-select-check">v</span>');
        $allButton.on('click', function(e) {
            e.preventDefault();
            values.length = 0;
            renderMultiSelect($root, options, values);
            $root.find('.multi-select-toggle')[0].focus();
        });
        $menu.append($allButton);
        $menu.append('<div class="dropdown-divider my-1"></div>');

        options.forEach(function(option) {
            const $item = $('<button type="button" class="dropdown-item multi-select-option"></button>');
            if (selectedSet.has(option.value)) $item.addClass('active');
            $item.append('<span>' + esc(option.label) + '</span><span class="multi-select-check">v</span>');
            $item.on('click', function(e) {
                e.preventDefault();
                const idx = values.indexOf(option.value);
                if (idx >= 0) {
                    values.splice(idx, 1);
                } else {
                    values.push(option.value);
                    sortSelections(values, options);
                }
                renderMultiSelect($root, options, values);
                // 다시 그린 옵션 버튼은 제거되므로 토글에 포커스를 돌려 Enter 검색을 받는다.
                $root.find('.multi-select-toggle')[0].focus();
            });
            $menu.append($item);
        });
    }

    const textFilters = {
        searchCar: '차량번호', searchReport: '신고번호', searchId: 'ID',
        searchReportName: '신고명', searchLaw: '위반법규', searchPerson: '담당자',
        searchLocation: '위반장소', searchAgency: '처리기관', searchFine: '범칙금_과태료',
        searchSupplementCount: '보완횟수', searchRatingCause: '별점사유',
        searchReportContent: '신고내용', searchProcessContent: '처리내용'
    };
    function captureSearch() {
        const text = Object.entries(textFilters).map(([id, field]) =>
            Object.freeze({field, groups: SrListPredicates.parseGroups(document.getElementById(id).value)}));
        const ranges = ['ReportDate', 'ResponseDate', 'OccurDate', 'OccurTime'].map(name =>
            Object.freeze({min: document.getElementById('search' + name + 'Start').value,
                           max: document.getElementById('search' + name + 'End').value,
                           time: name === 'OccurTime'}));
        return Object.freeze({text, ranges, statuses: [...searchState.statuses],
            ratings: [...searchState.ratings], poll: document.getElementById('searchPollStatus').value,
            excludePolice: document.getElementById('excludePolice').checked,
            onlyPolice: document.getElementById('onlyPolice').checked});
    }
    let appliedSearch = captureSearch();

    function runAdvancedSearch() {
        if (!tableReady) return; // 초기 입력은 initComplete가 한 번 적용한다.
        appliedSearch = captureSearch();
        table.search('').columns().search('');
        table.draw();
    }

    function submitFromMultiSelect($button) {
        const $dropdown = $button.hasClass('dropdown') ? $button : $button.closest('.dropdown');
        const toggleEl = $dropdown.find('.multi-select-toggle')[0];
        if (toggleEl && window.bootstrap && window.bootstrap.Dropdown) {
            window.bootstrap.Dropdown.getOrCreateInstance(toggleEl).hide();
            toggleEl.focus();
        }
        runAdvancedSearch();
    }

    function getStatusOptions() {
        const seen = new Set();
        const discovered = [];
        _tableData.forEach(function(row) {
            const status = canonicalStatus(row['처리상태']);
            if (!status || seen.has(status)) return;
            seen.add(status);
            discovered.push(status);
        });

        const ordered = preferredStatusOrder.slice();
        const extras = discovered
            .filter(status => !preferredStatusOrder.includes(status))
            .sort((a, b) => a.localeCompare(b, 'ko'));
        return ordered.concat(extras).map(status => ({ value: status, label: status }));
    }

    // ── 렌더 헬퍼(EO R-14: web/static/ui/data-table-cells.js, 순수 함수) ──────────────
    const { esc, normalizeCellText, splitMultilineLinks, encodeLinksPayload, decodeLinksPayload, labelFromUrl,
            renderBadge, renderEllipsis, renderAttach, renderMap } = window.SrDataTableCells;

    // ── 컬럼 정의 ──────────────────────────────────────────────────
    const columns = [
        { data: '신고번호', orderable: false, searchable: false, className: 'text-center',
          render: (d) => `<input type="checkbox" class="row-checkbox" value="${esc(d)}" aria-label="신고 ${esc(d)} 선택">` },
        { data: 'ID', className: 'text-center',
          render: (d, t, r) => t === 'display'
              ? `<a href="https://www.safetyreport.go.kr/#mypage/mysafereport/${esc(d)}" target="_blank">${esc(d)}</a>`
              : (d || '') },
        { data: '상태', className: 'text-center' },
        { data: '신고번호', className: 'text-center',
          render: (d, t) => t === 'display'
              ? `<a href="#" class="report-detail-link">${esc(d)}</a>`
              : (d || '') },
        { data: '신고명',
          render: (d, t) => t === 'display' ? renderEllipsis(d, t, 225) : (d || '') },
        { data: '신고일', render: (d, t) => t === 'display' ? esc(window.srDisplayDateTime(normalizeCellText(d))) : d },
        { data: '처리상태', className: 'text-center',
          render: (d, t) => t === 'display' ? renderBadge(d) : (d || '') },
        { data: '차량번호', className: 'text-center' },
        ...(isDuplicate ? [{
            data: 'valid_count',
            render: (d, t, r) => t === 'display'
                ? `<span class="badge bg-info text-dark" title="취하 제외">유효 ${d}</span> <span class="badge bg-secondary" title="취하 포함">전체 ${r.total_count}</span>`
                : String(d || 0)
        }] : []),
        { data: '위반법규' },
        { data: '범칙금_과태료' },
        { data: '벌점' },
        { data: '처리기관' },
        { data: '담당자' },
        { data: '답변일', render: (d, t) => t === 'display' ? esc(window.srDisplayDateTime(normalizeCellText(d))) : d },
        { data: '보완횟수', className: 'text-center',
          render: (d, t) => {
              const n = parseInt(d, 10);
              if (t !== 'display') return Number.isFinite(n) ? n : 0;
              if (!Number.isFinite(n) || n <= 0) return '';
              return `<span class="sr-badge sr-badge-supplement">${n}회</span>`;
          } },
        { data: '발생일자' },
        { data: '발생시각', className: 'text-center' },
        { data: '위반장소',
          render: (d, t) => t === 'display' ? renderEllipsis(d, t, 310) : (d || '') },
        { data: '위도', className: 'text-center', render: (d) => d == null ? '' : String(d) },
        { data: '경도', className: 'text-center', render: (d) => d == null ? '' : String(d) },
        { data: '종결여부', className: 'text-center' },
        { data: '신고내용',
          render: (d, t) => t === 'display' ? renderEllipsis(d, t, 182) : (d || '') },
        { data: '처리내용',
          render: (d, t) => t === 'display' ? renderEllipsis(d, t, 182) : (d || '') },
        { data: '지도',       render: renderMap },
        { data: '첨부사진',
          render: (d, t) => renderAttach(d, t, '일괄확인', 'btn-outline-secondary', 'photo') },
        { data: '첨부파일',
          render: (d, t) => renderAttach(d, t, '일괄확인', 'btn-outline-primary', 'file') },
        { data: '만족도조사여부' },
        { data: '별점', className: 'text-center',
          render: (d, t) => {
              if (t !== 'display') return d || '';
              if (d === null || d === undefined || d === '') return '';
              return `<span class="text-warning">★</span> ${esc(d)}점`;
          } },
        { data: '별점사유',
          render: (d, t) => t === 'display' ? renderEllipsis(d, t, 182) : (d || '') },
        { data: '감시목록' },
    ];

    // 엑셀 내보내기용 컬럼→필드 매핑
    const colFields = [
        null, 'ID', '상태', '신고번호', '신고명', '신고일', '처리상태', '차량번호',
        ...(isDuplicate ? ['valid_count'] : []),
        '위반법규', '범칙금_과태료', '벌점', '처리기관', '담당자', '답변일', '보완횟수', '발생일자',
        '발생시각', '위반장소', '위도', '경도', '종결여부', '신고내용', '처리내용', '지도',
        '첨부사진', '첨부파일', '만족도조사여부', '별점', '별점사유', '감시목록'
    ];

    // 빈 결과 안내. 표가 가로로 매우 넓어(scrollX) 칸 가운데에 두면 화면 밖에 놓이므로, 보이는 폭에 맞춰 왼쪽에 붙인다.
    var SR_EMPTY_STATE_HTML = '<div class="sr-empty-state text-center py-5 text-muted"><i class="fas fa-search fa-3x mb-3" style="color: var(--sr-border);"></i><br><h5>조건에 맞는 신고 내역이 없습니다</h5><p>상세 검색 필터를 조정해 보세요.</p></div>';
    function pinEmptyState(api) {
        var body = $(api.table().container()).find('.dataTables_scrollBody')[0];
        if (body) $(body).find('.sr-empty-state').css('width', body.clientWidth + 'px');
    }

    $('#' + SR_TABLE_ID).attr('aria-busy', 'true');
    var $listActions = $('.sr-list-toolbar button, #btnSearch, #btnReset').prop('disabled', true);
    var table = $('#' + SR_TABLE_ID).DataTable({
        initComplete: function () {
            // 언어 파일이 늦게 도착해도 초기화 완료 뒤 실행한다. 동기 초기화일 때도
            // 아래 필터·선택·스크롤 핸들러 등록이 끝난 다음 microtask에서 처리한다.
            var api = this.api();
            Promise.resolve().then(function () {
                var openReport = urlParams.get('open');
                if (openReport) $('#searchReport').val(openReport);
                tableReady = true;
                api.columns.adjust();
                runAdvancedSearch();
                syncScroll();
                $('#' + SR_TABLE_ID).attr('aria-busy', 'false');
                $listActions.prop('disabled', false);
                if (openReport) {
                    var rows = api.rows({search: 'applied'});
                    if (rows.count() > 0) window.showReportDetail(rows.data()[0]);
                }
            });
        },
        drawCallback: function () { pinEmptyState(this.api()); },
        data: _tableData,
        columns: columns,
        deferRender: true,   // 현재 페이지(50행)만 DOM 생성 — 핵심 최적화
        processing: true,
        scrollX: true,
        autoWidth: false,
        pageLength: 50,
        order: [],
        columnDefs: [
            { targets: 0, width: '13px' },
            { targets: 1, width: '53.05px' },
            { targets: 2, width: '40.89px' },
            { targets: 3, width: '120.61px' },
            { targets: 4, width: '225.19px' },
            { targets: 5, width: '135px', className: 'tabular-nums' },
            { targets: 6, width: '80px' },
            { targets: 7, width: '89.45px' },
            ...(isDuplicate ? [{ targets: 8, width: '100px' }] : []),
            { targets: 8 + offset, width: '141.13px' },
            { targets: 9 + offset, width: '98.7px' },
            { targets: 10 + offset, width: '29.45px' },
            { targets: 11 + offset, width: '270.64px' },
            { targets: 12 + offset, width: '44.17px' },
            { targets: 13 + offset, width: '135px', className: 'tabular-nums' },
            { targets: 14 + offset, width: '48px' },
            { targets: 15 + offset, width: '85px', className: 'tabular-nums' },
            { targets: 16 + offset, width: '75px', className: 'tabular-nums' },
            { targets: 17 + offset, width: '310.45px' },
            { targets: 18 + offset, width: '130px', className: 'tabular-nums' },
            { targets: 19 + offset, width: '130px', className: 'tabular-nums' },
            { targets: 20 + offset, width: '58.89px' },
            { targets: 21 + offset, width: '182px' },
            { targets: 22 + offset, width: '182px' },
            { targets: 23 + offset, width: '40.89px' },
            { targets: 24 + offset, width: '71.63px' },
            { targets: 25 + offset, width: '71.63px' },
            { targets: 26 + offset, width: '73.61px' },
            { targets: 27 + offset, width: '58.89px' },
            { targets: 28 + offset, width: '182px' },
            { targets: 29 + offset, width: '58.89px' }
        ],
        language: {
            url: 'https://cdn.datatables.net/plug-ins/1.13.6/i18n/ko.json',
            emptyTable: SR_EMPTY_STATE_HTML,
            zeroRecords: SR_EMPTY_STATE_HTML
        }
    });

    const statusOptions = getStatusOptions();
    renderMultiSelect($('#searchStatusDropdown'), statusOptions, searchState.statuses);
    renderMultiSelect($('#searchRatingDropdown'), ratingOptions, searchState.ratings);

    // 선택된 신고번호 추적 Set (페이지 전환에도 유지)
    var selectedRows = new Set();

    function applyRowHighlights() {
        table.rows({ page: 'current' }).every(function () {
            var d = this.data();
            if (!d) return;
            var rnum = String(d['신고번호'] || '');
            var node = this.node();
            var $tr = $(node);
            var $cb = $tr.find('.row-checkbox');
            if (rnum && selectedRows.has(rnum)) {
                $cb.prop('checked', true);
                $tr.addClass('row-selected');
            } else {
                $cb.prop('checked', false);
                $tr.removeClass('row-selected');
            }
        });
        var pageTotal = table.rows({ page: 'current' }).count();
        var pageChecked = table.rows({ page: 'current' }).nodes().toArray()
            .filter(function (n) { return $(n).find('.row-checkbox').is(':checked'); }).length;
        $('#selectAll').prop('indeterminate', pageChecked > 0 && pageChecked < pageTotal);
        $('#selectAll').prop('checked', pageTotal > 0 && pageChecked === pageTotal);
    }

    // 아래 작업 바는 표가 한 화면보다 길 때만 보인다. 짧은 목록에서 같은 버튼 묶음이 두 번 나와 화면을 차지했다(기술일지 O-08)
    function syncBottomToolbar() {
        var bottom = document.querySelector('.sr-list-toolbar-bottom');
        var wrapper = document.querySelector('#' + SR_TABLE_ID + '_wrapper');
        if (!bottom || !wrapper) return;
        bottom.classList.toggle('is-redundant', wrapper.getBoundingClientRect().height < window.innerHeight * 0.9);
    }
    $(window).on('resize', function () { pinEmptyState(table); syncBottomToolbar(); });

    table.on('draw', function () {
        syncScroll();
        applyRowHighlights();
        syncBottomToolbar();
    });
    syncBottomToolbar();

    // 개별 체크박스 토글
    $('#' + SR_TABLE_ID + ' tbody').on('change', '.row-checkbox', function () {
        var rnum = String($(this).val() || '');
        var $tr = $(this).closest('tr');
        if ($(this).is(':checked')) {
            selectedRows.add(rnum);
            $tr.addClass('row-selected');
        } else {
            selectedRows.delete(rnum);
            $tr.removeClass('row-selected');
        }
        var pageTotal = table.rows({ page: 'current' }).count();
        var pageChecked = table.rows({ page: 'current' }).nodes().toArray()
            .filter(function (n) { return $(n).find('.row-checkbox').is(':checked'); }).length;
        $('#selectAll').prop('indeterminate', pageChecked > 0 && pageChecked < pageTotal);
        $('#selectAll').prop('checked', pageTotal > 0 && pageChecked === pageTotal);
    });

    // 경찰기관 필터 상호 배타적 선택
    $('.filter-police').on('change', function() {
        if ($(this).is(':checked')) {
            $('.filter-police').not(this).prop('checked', false);
        }
    });

    // 상단/하단 스크롤 동기화 로직
    const $scrollBody = $('.dataTables_scrollBody');
    const $topScroll = $('#topScrollWrapper');
    const $topDummy = $('#topScrollDummy');

    function syncScroll() {
        const $scrollBodyElem = $('.dataTables_scrollBody');
        const $scrollHeadElem = $('.dataTables_scrollHead');
        if ($scrollBodyElem.length === 0) return;

        const tableWidth = $scrollBodyElem[0].scrollWidth;
        $topDummy.width(tableWidth);

        // 동기화 이벤트 (중복 등록 방지를 위해 off() 선행)
        // 세 요소(상단바, 헤더, 바디)를 항상 함께 동기화
        $topScroll.off('scroll').on('scroll', function() {
            const left = $topScroll.scrollLeft();
            $scrollBodyElem.scrollLeft(left);
            $scrollHeadElem.scrollLeft(left);
        });
        $scrollBodyElem.off('scroll').on('scroll', function() {
            const left = $scrollBodyElem.scrollLeft();
            $topScroll.scrollLeft(left);
            $scrollHeadElem.scrollLeft(left);
        });
    }

    // 최초 검색·자동 상세 열기는 위 initComplete에서 처리한다.

    // draw 핸들러는 selectedRows 블록 내에서 등록하므로 여기선 제거

    $.fn.dataTable.ext.search.push(
        function(settings, data, dataIndex) {
            if (settings.nTable !== table.table().node()) return true;
            const row = settings.aoData[dataIndex] && settings.aoData[dataIndex]._aData
                ? settings.aoData[dataIndex]._aData
                : {};

            return SrListPredicates.matchesSearch(appliedSearch, row);
        }
    );

    $('#btnSearch').on('click', function() {
        runAdvancedSearch();
    });

    function validateAndWarn(startId, endId, label) {
        var startElem = $(startId)[0];
        var endElem = $(endId)[0];
        
        if (label === '발생시간') {
            const timeRegex = /^([01]\d|2[0-3]):?([0-5]\d)$/;
            [startId, endId].forEach(id => {
                let val = $(id).val().trim();
                if (val && !timeRegex.test(val)) {
                    alert("발생시간 형식이 올바르지 않습니다. (예: 14:30)");
                    $(id).val('');
                } else if (val && val.length === 4 && !val.includes(':')) {
                    // Auto-insert colon if 4 digits entered
                    $(id).val(val.slice(0, 2) + ':' + val.slice(2));
                }
            });
        } else {
            if(startElem && startElem.validity && startElem.validity.badInput) {
                alert(label + " 시작 시점: 유효하지 않은 날짜/시간 포맷이 입력되었습니다.");
                $(startId).val(''); return;
            }
            if(endElem && endElem.validity && endElem.validity.badInput) {
                alert(label + " 종료 시점: 유효하지 않은 날짜/시간 포맷이 입력되었습니다.");
                $(endId).val(''); return;
            }
        }

        var s = $(startId).val();
        var e = $(endId).val();
        if(s && e && s > e) {
            alert(label + " 범위 설정 오류: (종료 시점이 시작 시점보다 더 과거입니다)");
            $(endId).val(''); 
        }
    }

    $('#searchReportDateEnd, #searchReportDateStart').on('change', () => validateAndWarn('#searchReportDateStart', '#searchReportDateEnd', '신고일'));
    $('#searchOccurDateEnd, #searchOccurDateStart').on('change', () => validateAndWarn('#searchOccurDateStart', '#searchOccurDateEnd', '발생일'));
    $('#searchResponseDateEnd, #searchResponseDateStart').on('change', () => validateAndWarn('#searchResponseDateStart', '#searchResponseDateEnd', '답변일'));
    $('#searchOccurTimeEnd, #searchOccurTimeStart').on('change', () => validateAndWarn('#searchOccurTimeStart', '#searchOccurTimeEnd', '발생시간'));

    // 연도 4자리 제한 로직
    $('input[type="date"]').on('input', function() {
        if (this.value) {
            let year = this.value.split('-')[0];
            if (year.length > 4) {
                this.value = '2099' + this.value.slice(year.length);
            }
        }
    });

    $('#advSearchForm').on('keydown', function(e) {
        if (e.key !== 'Enter') return;
        if (e.shiftKey || e.ctrlKey || e.altKey || e.metaKey) return;
        const $target = $(e.target);
        if ($target.is('textarea')) return;
        e.preventDefault();
        e.stopPropagation();

        const $optionButton = $target.closest('.multi-select-option');
        if ($optionButton.length) {
            const $dropdown = $optionButton.closest('.dropdown');
            $optionButton.trigger('click');
            setTimeout(function() {
                submitFromMultiSelect($dropdown);
            }, 0);
            return;
        }

        const $toggleButton = $target.closest('.multi-select-toggle');
        if ($toggleButton.length) {
            submitFromMultiSelect($toggleButton);
            return;
        }

        runAdvancedSearch();
    });

    $('#btnReset').on('click', function() {
        selectedRows.clear();
        searchState.statuses.length = 0;
        searchState.ratings.length = 0;
        renderMultiSelect($('#searchStatusDropdown'), statusOptions, searchState.statuses);
        renderMultiSelect($('#searchRatingDropdown'), ratingOptions, searchState.ratings);
        setTimeout(function() {
            appliedSearch = captureSearch();
            table.search('').columns().search('').draw();
            $('#selectAll').prop('checked', false).prop('indeterminate', false);
        }, 10);
    });

    $(document).on('click', '.view-all-btn', function() {
        var isPhoto = $(this).data('type') === 'photo';
        var links = decodeLinksPayload($(this).attr('data-links'));
        var title = isPhoto ? '첨부사진' : '첨부파일';
        $('#attachModalLabel').text(title);

        var $body = $('#attachModalBody').empty();
        links.forEach(function(url, idx) {
            url = normalizeCellText(url);
            var ext = url.split('?')[0].toLowerCase();
            var isImg = /\.(jpg|jpeg|png|gif|webp|bmp)$/.test(ext);
            var isVid = /\.(mp4|mov|avi|webm|mkv)$/.test(ext);
            var $item = $('<div class="mb-4 border rounded p-2 bg-light"></div>');
            var num = idx + 1;

            try {
                const parsed = new URL(url, location.href);
                if (!['http:', 'https:'].includes(parsed.protocol)) return;
            } catch (_) { return; }
            const $download = $('<a class="btn btn-sm btn-outline-primary" download></a>')
                .attr('href', url).text('다운로드');
            if (isImg) {
                const $error = $('<div class="attach-err text-muted small"></div>')
                    .text('이미지를 불러올 수 없습니다.').hide();
                const $image = $('<img class="img-fluid rounded mb-2">')
                    .css({maxHeight: '480px', width: '100%', objectFit: 'contain', background: '#000'})
                    .on('error', function() { $error.show(); }).attr('src', url);
                $item.append($image, $error, $('<div class="d-flex gap-2 mt-1"></div>').append($download));
            } else if (isVid) {
                const mediaUrl = window.proxyMediaUrl ? window.proxyMediaUrl(url) : url;
                const $video = $('<video controls preload="metadata" class="w-100 rounded mb-2"></video>')
                    .css({maxHeight: '480px', background: '#000'})
                    .attr({'data-source-url': url, 'data-proxy-src': mediaUrl})
                    .text('브라우저가 동영상 재생을 지원하지 않습니다.');
                $item.append($video,
                    $('<div class="text-muted small mb-2" data-proxy-video-status></div>').text('동영상 준비 중...'),
                    $('<div class="d-flex gap-2 mt-1"></div>').append($download));
            } else {
                $item.append($('<div class="d-flex align-items-center gap-2 p-2"></div>').append(
                    $('<i class="fas fa-file me-1 text-muted"></i>'),
                    $('<span class="text-truncate flex-grow-1 small"></span>').text(labelFromUrl(url, num)),
                    $download));
            }
            $body.append($item);
        });

        $body.attr('data-proxy-video-scope', 'attach');
        if (window.prepareProxyVideos) {
            window.prepareProxyVideos($body[0]);
        }
        _attachModal.show();
    });

    // 엑셀 다운로드 (BOM 포함 CSV)
    $('.btn-export-excel').on('click', function() {
        let resultData = [];
        let maxLinksLength = { "첨부사진": 0, "첨부파일": 0 };

        // JSON 데이터 소스: row는 원본 객체이므로 필드명으로 직접 접근
        table.rows({search:'applied'}).data().each(function(row) {
            let rowObj = { raw: row, photos: [], files: [] };

            let photos = normalizeCellText(row['첨부사진']);
            if (photos === '6개월 초과') {
                rowObj.photos = ['만료'];
            } else if (photos) {
                rowObj.photos = splitMultilineLinks(photos);
            }
            if (rowObj.photos.length > maxLinksLength["첨부사진"]) {
                maxLinksLength["첨부사진"] = rowObj.photos.length;
            }

            let files = normalizeCellText(row['첨부파일']);
            if (files === '6개월 초과') {
                rowObj.files = ['만료'];
            } else if (files) {
                rowObj.files = splitMultilineLinks(files);
            }
            if (rowObj.files.length > maxLinksLength["첨부파일"]) {
                maxLinksLength["첨부파일"] = rowObj.files.length;
            }

            resultData.push(rowObj);
        });

        let csvContent = "\uFEFF"; // 한글 깨짐 방지 BOM
        let headers = [];
        table.columns().every(function() {
            if (this.index() !== 0) { 
                // 화면 머리말을 바꿔도 CSV 열 이름은 그대로 둔다(data-csv-title, 기술일지 O-05)
                let title = ($(this.header()).attr('data-csv-title') || $(this.header()).text()).trim();
                if (title === "첨부사진" || title === "첨부파일") {
                    let maxCount = maxLinksLength[title];
                    if (maxCount === 0) {
                        headers.push('"' + title + '"');
                    } else {
                        for (let i = 1; i <= maxCount; i++) {
                            headers.push('"' + title + i + '"');
                        }
                    }
                } else {
                    headers.push('"' + title.replace(/"/g, '""') + '"');
                }
            }
        });
        csvContent += headers.join(',') + '\r\n';

        resultData.forEach(function(rowObj) {
            let rowArray = [];
            table.columns().every(function() {
                let idx = this.index();
                if (idx !== 0) {
                    let title = $(this.header()).text().trim();
                    if (title === "첨부사진" || title === "첨부파일") {
                        let links = title === "첨부사진" ? rowObj.photos : rowObj.files;
                        let maxCount = maxLinksLength[title];
                        if (maxCount === 0) {
                            rowArray.push('""');
                        } else {
                            for (let i = 0; i < maxCount; i++) {
                                let linkStr = i < links.length ? normalizeCellText(links[i]) : '';
                                rowArray.push('"' + linkStr.replace(/"/g, '""') + '"');
                            }
                        }
                    } else {
                        // JSON 소스: colFields 매핑으로 필드명 조회
                        let field = colFields[idx];
                        let cellText = field != null ? normalizeCellText(rowObj.raw[field]) : '';
                        // 엑셀에서 긴 숫자가 지수표기법으로 바뀌거나 잘리는 것을 방지
                        if (title === "신고번호" || title === "차량번호" || title === "연락처") {
                            rowArray.push('="' + cellText.replace(/"/g, '""') + '"');
                        } else {
                            rowArray.push('"' + cellText.replace(/"/g, '""') + '"');
                        }
                    }
                }
            });
            csvContent += rowArray.join(',') + '\r\n';
        });

        let blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        let url = URL.createObjectURL(blob);
        let link = document.createElement("a");
        link.setAttribute("href", url);
        link.setAttribute("download", "safetyreport_data.csv");
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    });

    // 선택된 신고번호 클립보드 복사
    $('.btn-copy-selected-ids').on('click', function() {
        let text = [...selectedRows].filter(Boolean);

        if(text.length === 0) {
             alert('먼저 복사할 신고번호를 체크박스로 선택해주세요.');
             return;
        }
        
        let resultString = text.join('\n');
        
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(resultString).then(function() {
                alert(text.length + "개의 선택된 신고번호가 클립보드에 복사되었습니다!");
            }).catch(function(err) {
                alert("복사 실패: " + err);
            });
        } else {
            let textArea = document.createElement("textarea");
            textArea.value = resultString;
            textArea.style.position = "fixed";
            textArea.style.left = "-999999px";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            try {
                document.execCommand('copy');
                alert(text.length + "개의 선택된 신고번호가 클립보드에 복사되었습니다!");
            } catch (err) {
                alert("복사 실패: " + err);
            }
            document.body.removeChild(textArea);
        }
    });

    // 현재 페이지 신고번호 클립보드 복사
    $('.btn-copy-page-ids').on('click', function() {
        let text = [];
        table.rows({page: 'current'}).data().each(function(row) {
            let rnum = row['신고번호'];
            if (rnum) text.push(String(rnum).trim());
        });

        if (text.length === 0) {
            alert('복사할 신고번호가 없습니다.');
            return;
        }

        let resultString = text.join('\n');

        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(resultString).then(function() {
                alert(text.length + "개의 현재 페이지 신고번호가 클립보드에 복사되었습니다!");
            }).catch(function(err) {
                alert("복사 실패: " + err);
            });
        } else {
            let textArea = document.createElement("textarea");
            textArea.value = resultString;
            textArea.style.position = "fixed";
            textArea.style.left = "-999999px";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            try {
                document.execCommand('copy');
                alert(text.length + "개의 현재 페이지 신고번호가 클립보드에 복사되었습니다!");
            } catch (err) {
                alert("복사 실패: " + err);
            }
            document.body.removeChild(textArea);
        }
    });

    // 화면의 신고번호 일괄 클립보드 복사
    $('.btn-copy-ids').on('click', function() {
        let text = [];
        table.rows({search:'applied'}).data().each(function(row) {
            // JSON 소스: 신고번호 필드 직접 접근
            let rnum = row['신고번호'];
            if (rnum) text.push(String(rnum).trim());
        });
        
        if(text.length === 0) {
             alert('복사할 신고번호가 없습니다.');
             return;
        }
        
        let resultString = text.join('\n');
        
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(resultString).then(function() {
                alert(text.length + "개의 신고번호가 클립보드에 복사되었습니다!");
            }).catch(function(err) {
                alert("복사 실패: " + err);
            });
        } else {
            // HTTP 등 비보안 컨텍스트에서 Fallback 동작
            let textArea = document.createElement("textarea");
            textArea.value = resultString;
            textArea.style.position = "fixed";
            textArea.style.left = "-999999px";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            try {
                document.execCommand('copy');
                alert(text.length + "개의 신고번호가 안전하게 클립보드에 복사되었습니다!");
            } catch (err) {
                alert("복사 실패: " + err);
            }
            document.body.removeChild(textArea);
        }
    });

    // 긴 텍스트 모달 팝업 추가
    $('body').append(`
        <div class="modal fade" id="textModal" tabindex="-1" aria-hidden="true">
          <div class="modal-dialog modal-dialog-centered modal-lg flex-column justify-content-center">
            <div class="modal-content">
              <div class="modal-header py-2">
                <h6 class="modal-title fw-bold" id="textModalTitle">상세 내용</h6>
                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
              </div>
              <div class="modal-body">
                <p id="textModalContent" style="white-space: pre-wrap; font-size: 1.05em; word-break: break-all;"></p>
              </div>
            </div>
          </div>
        </div>
    `);

    var _textModal = new bootstrap.Modal(document.getElementById('textModal'));

    // 첨부사진/파일 인라인 미디어 모달
    $('body').append(`
        <div class="modal fade" id="attachModal" tabindex="-1" aria-hidden="true">
          <div class="modal-dialog modal-dialog-centered modal-lg">
            <div class="modal-content">
              <div class="modal-header py-2">
                <h6 class="modal-title fw-bold" id="attachModalLabel">첨부파일</h6>
                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
              </div>
              <div class="modal-body" id="attachModalBody" style="max-height:70vh;overflow-y:auto;"></div>
            </div>
          </div>
        </div>
    `);

    var _attachModalEl = document.getElementById('attachModal');
    var _attachModal = new bootstrap.Modal(_attachModalEl);
    _attachModalEl.addEventListener('hidden.bs.modal', function() {
        if (window.resetProxyVideos) {
            window.resetProxyVideos(_attachModalEl);
        }
    });

    // 신고번호 클릭 → 상세 팝업
    $('#' + SR_TABLE_ID).on('click', 'a.report-detail-link', function(e) {
        e.preventDefault();
        var rowData = table.row($(this).closest('tr')).data();
        if (rowData) showReportDetail(rowData);
    });

    $('#' + SR_TABLE_ID + ' tbody').on('dblclick', 'td', function() {
        var cellIdx = table.cell(this).index();
        if (!cellIdx) return;
        var colIdx = cellIdx.column;
        if (colIdx === 0) return; // 체크박스 열 제외
        var text = String(table.cell(this).data() || '');
        if (!text) return;
        var colName = $(table.column(colIdx).header()).text().trim();
        $('#textModalTitle').text(colName);
        $('#textModalContent').text(text);
        _textModal.show();
    });

    $('#selectAll').on('change', function() {
        var isChecked = $(this).prop('checked');
        $(this).prop('indeterminate', false);
        table.rows({ page: 'current' }).every(function () {
            var d = this.data();
            if (!d) return;
            var rnum = String(d['신고번호'] || '');
            var node = this.node();
            var $tr = $(node);
            var $cb = $tr.find('.row-checkbox');
            $cb.prop('checked', isChecked);
            if (isChecked) {
                selectedRows.add(rnum);
                $tr.addClass('row-selected');
            } else {
                selectedRows.delete(rnum);
                $tr.removeClass('row-selected');
            }
        });
    });

    $('.btn-add-watchlist').on('click', function() {
        var selected = [...selectedRows].filter(Boolean);

        if(selected.length === 0) {
            alert('감시목록에 추가할 항목을 선택해주세요.');
            return;
        }
        
        if(confirm(selected.length + '건을 감시목록에 추가하시겠습니까?')) {
            $.ajax({
                url: '/watchlist/add',
                type: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({ rnums: selected }),
                success: function(res) {
                    alert(res.message);
                },
                error: function() {
                    alert('요청 처리 중 오류가 발생했습니다.');
                }
            });
        }
    });

    $('.btn-enqueue-crawl').on('click', function() {
        var selected = [...selectedRows].filter(Boolean);

        if (selected.length === 0) {
            alert('크롤링할 항목을 체크박스로 선택해주세요.');
            return;
        }

        if (!confirm(selected.length + '건을 큐 크롤링에 추가하시겠습니까?')) {
            return;
        }

        $.ajax({
            url: '/crawl/enqueue-selected',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ report_numbers: selected }),
            success: function(res) {
                alert(res.message || '요청이 처리되었습니다.');
            },
            error: function(xhr) {
                var message = '요청 처리 중 오류가 발생했습니다.';
                if (xhr.responseJSON && xhr.responseJSON.message) {
                    message = xhr.responseJSON.message;
                }
                alert(message);
            }
        });
    });

});
