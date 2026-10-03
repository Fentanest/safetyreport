/* 전국 안전신고 현황(Sunwi) 위젯 — 2026-09-28 대시보드(index.html)에서 통계 화면 하단으로 옮겼다.
   동작은 예전과 같다: 서버가 준 첫 자료로 그리고 /sunwi/payload 를 주기적으로 다시 받는다(수집 전 3초, 수집 뒤 30초),
   대분류·소분류는 5초마다 넘긴다. 추가: 자동 넘김 일시정지 버튼(WCAG 2.2.2), 상위 5곳을 가로로 배치.

   SrSunwiWidget.init({ contentEl, updatedAtEl, initialData }) */
window.SrSunwiWidget = (function () {
    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function mod(index, size) {
        return (index % size + size) % size;
    }

    function normalize(data) {
        var normalized = data && typeof data === 'object' ? data : {};
        normalized.categories = Array.isArray(normalized.categories) ? normalized.categories : [];
        normalized.available = Boolean(normalized.available && normalized.categories.length);
        normalized.updated_at = normalized.updated_at || '';
        normalized.error = normalized.error || '';
        normalized.failed_count = Number(normalized.failed_count || 0);
        normalized.csv_download_url = normalized.csv_download_url || '/sunwi/download/top5';
        return normalized;
    }

    function init(opts) {
        var contentEl = opts.contentEl;
        var updatedAtEl = opts.updatedAtEl;
        if (!contentEl || !updatedAtEl) {
            return null;
        }
        var disposed = false, fetchAbort = null;
        var state = {
            data: normalize(opts.initialData),
            categoryIndex: 0,
            childIndex: 0,
            carouselTimerId: null,
            pollTimerId: null,
            isFetching: false,
            paused: false,
            dom: null
        };
        var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        state.paused = Boolean(reduceMotion);

        function group() { return state.data.categories[state.categoryIndex]; }
        function children() {
            var g = group();
            return g && Array.isArray(g.children) ? g.children : [];
        }
        function current() {
            var list = children();
            return list.length ? list[state.childIndex] : { name: '', full_name: '', items: [] };
        }

        function renderUnavailable() {
            contentEl.innerHTML =
                '<div class="sr-sunwi-empty" role="status">' +
                '<div class="mb-1"><i class="fas fa-spinner fa-spin me-1"></i>행정구역 통계를 수집 중입니다.</div>' +
                (state.data.error ? '<div class="small text-danger">' + escapeHtml(state.data.error) + '</div>' : '') +
                '</div>';
            state.dom = null;
        }

        function renderShell() {
            var failedText = state.data.failed_count
                ? '일부 재시도 실패 지역 ' + Number(state.data.failed_count).toLocaleString() + '건이 있습니다.'
                : '';
            contentEl.innerHTML =
                '<div class="sr-sunwi-nav">' +
                '  <div class="sr-sunwi-step">' +
                '    <button type="button" class="btn btn-sm" id="sunwiPrevParentCategory" aria-label="이전 대분류"><i class="fas fa-chevron-left"></i></button>' +
                '    <div class="sr-step-text"><span class="sr-step-label">대분류</span><span class="sr-step-name" id="sunwiParentCategoryName"></span></div>' +
                '    <button type="button" class="btn btn-sm" id="sunwiNextParentCategory" aria-label="다음 대분류"><i class="fas fa-chevron-right"></i></button>' +
                '  </div>' +
                '  <div class="sr-sunwi-step is-child">' +
                '    <button type="button" class="btn btn-sm" id="sunwiPrevChildCategory" aria-label="이전 소분류"><i class="fas fa-chevron-left"></i></button>' +
                '    <div class="sr-step-text"><span class="sr-step-label">소분류</span><span class="sr-step-name" id="sunwiChildCategoryName"></span></div>' +
                '    <button type="button" class="btn btn-sm" id="sunwiNextChildCategory" aria-label="다음 소분류"><i class="fas fa-chevron-right"></i></button>' +
                '  </div>' +
                '  <button type="button" class="btn btn-sm btn-outline-secondary ms-auto" id="sunwiPauseBtn" aria-pressed="false"></button>' +
                '</div>' +
                '<div class="sr-sunwi-items mt-2" id="sunwiItems" aria-live="off"></div>' +
                '<div class="sr-sunwi-foot mt-2">' +
                '  <span>대분류와 소분류는 5초마다 자동으로 넘어갑니다.' + (failedText ? ' ' + escapeHtml(failedText) : '') + '</span>' +
                '  <a href="' + escapeHtml(state.data.csv_download_url) + '" class="text-decoration-none"><i class="fas fa-file-csv me-1"></i>CSV 다운로드</a>' +
                '</div>';
            state.dom = {
                parent: document.getElementById('sunwiParentCategoryName'),
                child: document.getElementById('sunwiChildCategoryName'),
                items: document.getElementById('sunwiItems'),
                prevParent: document.getElementById('sunwiPrevParentCategory'),
                nextParent: document.getElementById('sunwiNextParentCategory'),
                prevChild: document.getElementById('sunwiPrevChildCategory'),
                nextChild: document.getElementById('sunwiNextChildCategory'),
                pause: document.getElementById('sunwiPauseBtn')
            };
            state.dom.prevParent.addEventListener('click', function () { stepParent(-1); });
            state.dom.nextParent.addEventListener('click', function () { stepParent(1); });
            state.dom.prevChild.addEventListener('click', function () { stepChild(-1); });
            state.dom.nextChild.addEventListener('click', function () { stepChild(1); });
            state.dom.pause.addEventListener('click', function () {
                state.paused = !state.paused;
                renderPause();
                resetCarousel();
            });
            renderPause();
        }

        function renderPause() {
            if (!state.dom) return;
            state.dom.pause.setAttribute('aria-pressed', state.paused ? 'true' : 'false');
            state.dom.pause.innerHTML = state.paused
                ? '<i class="fas fa-play me-1"></i>자동 넘김 켜기'
                : '<i class="fas fa-pause me-1"></i>자동 넘김 멈춤';
        }

        function stepParent(delta) {
            if (state.data.categories.length <= 1) return;
            state.categoryIndex = mod(state.categoryIndex + delta, state.data.categories.length);
            state.childIndex = 0;
            render();
            resetCarousel();
        }

        function stepChild(delta) {
            var list = children();
            if (list.length <= 1) return;
            state.childIndex = mod(state.childIndex + delta, list.length);
            render();
            resetCarousel();
        }

        function renderItems(items) {
            var html = '';
            (items || []).forEach(function (item) {
                html +=
                    '<div class="sr-sunwi-item">' +
                    '  <span class="sunwi-rank-badge rank-' + escapeHtml(item.rank) + '">' + escapeHtml(item.rank) + '위</span>' +
                    '  <div class="sunwi-region">' + escapeHtml(item.region) + '</div>' +
                    '  <div class="sunwi-count">' + Number(item.count || 0).toLocaleString() + '<span>건</span></div>' +
                    '</div>';
            });
            state.dom.items.innerHTML = html || '<div class="sr-sunwi-empty">이 분류에는 자료가 없습니다.</div>';
        }

        function render() {
            if (!state.data.available) {
                renderUnavailable();
                return;
            }
            var list = children();
            if (list.length && state.childIndex >= list.length) state.childIndex = 0;
            var g = group();
            var c = current();
            state.dom.parent.textContent = g.name + ' (' + (state.categoryIndex + 1) + '/' + state.data.categories.length + ')';
            state.dom.child.textContent = c.name ? c.name + ' (' + (state.childIndex + 1) + '/' + list.length + ')' : '데이터 없음';
            renderItems(c.items || []);
            state.dom.prevParent.disabled = state.data.categories.length <= 1;
            state.dom.nextParent.disabled = state.data.categories.length <= 1;
            state.dom.prevChild.disabled = list.length <= 1;
            state.dom.nextChild.disabled = list.length <= 1;
        }

        function resetCarousel() {
            if (state.carouselTimerId) {
                window.clearInterval(state.carouselTimerId);
                state.carouselTimerId = null;
            }
            if (!state.data.available || state.paused) return;
            state.carouselTimerId = window.setInterval(function () {
                var list = children();
                if (list.length > 1) {
                    state.childIndex = mod(state.childIndex + 1, list.length);
                    if (state.childIndex === 0 && state.data.categories.length > 1) {
                        state.categoryIndex = mod(state.categoryIndex + 1, state.data.categories.length);
                    }
                } else if (state.data.categories.length > 1) {
                    state.categoryIndex = mod(state.categoryIndex + 1, state.data.categories.length);
                    state.childIndex = 0;
                }
                render();
            }, 5000);
        }

        function signature(data) {
            return JSON.stringify({
                available: data.available, updated_at: data.updated_at, period: data.period || '',
                failed_count: data.failed_count, error: data.error, categories: data.categories
            });
        }

        function updateHeader() {
            updatedAtEl.textContent = state.data.updated_at
                ? '전일기준 · 수집시간 ' + state.data.updated_at
                : '전일기준 · 수집 준비 중';
        }

        function update(nextData) {
            if (disposed) return;
            var normalized = normalize(nextData);
            var previousAvailable = state.data.available;
            var previousSignature = signature(state.data);
            var nextSignature = signature(normalized);
            state.data = normalized;
            updateHeader();
            if (!normalized.available) {
                if (previousAvailable || previousSignature !== nextSignature || !state.dom) renderUnavailable();
                resetCarousel();
                schedulePoll();
                return;
            }
            if (!previousAvailable) {
                state.categoryIndex = 0;
                state.childIndex = 0;
                renderShell();
            } else if (previousSignature !== nextSignature) {
                state.categoryIndex = Math.min(state.categoryIndex, Math.max(normalized.categories.length - 1, 0));
                state.childIndex = 0;
            }
            if (!state.dom) renderShell();
            render();
            resetCarousel();
            schedulePoll();
        }

        function schedulePoll() {
            if (disposed) return;
            if (state.pollTimerId) window.clearTimeout(state.pollTimerId);
            state.pollTimerId = window.setTimeout(fetchLatest, state.data.available ? 30000 : 3000);
        }

        function fetchLatest() {
            if (disposed) return;
            if (state.isFetching) {
                schedulePoll();
                return;
            }
            state.isFetching = true;
            fetchAbort = new AbortController();
            fetch('/sunwi/payload', { cache: 'no-store', signal: fetchAbort.signal })
                .then(function (response) {
                    if (!response.ok) throw new Error('sunwi payload fetch failed');
                    return response.json();
                })
                .then(update)
                .catch(function () { schedulePoll(); })
                .finally(function () { state.isFetching = false; });
        }

        update(opts.initialData);
        return { update: update, dispose: function () {
            disposed = true;
            window.clearInterval(state.carouselTimerId);
            window.clearTimeout(state.pollTimerId);
            if (fetchAbort) fetchAbort.abort();
            contentEl.replaceChildren(); state.dom = null;
        } };
    }

    return { init: init };
})();
