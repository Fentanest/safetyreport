/* 공통 셸의 상태 확인(EO R-15: base.html 인라인 스크립트를 옮겼다): 업데이트 뒤 작업 진행 바, 최신 버전 표시, 초기화 크롤링 안내. */
(function () {
    var bar = document.getElementById('srJobBar');
    var text = document.getElementById('srJobText');
    var toggle = document.getElementById('srJobToggle');
    var logList = document.getElementById('srJobLog');
    if (!bar || !document.getElementById('mainSidebar')) return; // 로그인 전 화면에서는 부르지 않는다
    var wasActive = false, timer = null;
    function line(job) {
        var parts = [job.label];
        if (job.total) parts.push(job.done + '/' + job.total);
        if (job.current) parts.push(job.current);
        if (job.message) parts.push(job.message);
        return parts.join(' · ');
    }
    function optimizeLine(job) {
        // DB 최적화 작업(services/db_optimize.py): 라벨·진행률·안내 문구만 보인다(stage 영문 키는 숨김).
        var parts = [job.label];
        if (job.total) parts.push(job.done + '/' + job.total);
        if (job.message) parts.push(job.message);
        return parts.join(' · ');
    }
    function isOptimize(job) { return job && job.key === 'db_optimize'; }
    function renderLog(lines) {
        if (!logList) return;
        while (logList.firstChild) logList.removeChild(logList.firstChild);
        (lines || []).forEach(function (msg) {
            var li = document.createElement('li');
            li.textContent = msg;
            logList.appendChild(li);
        });
    }
    function setLogVisible(job) {
        if (!toggle || !logList) return;
        if (job && job.log && job.log.length) {
            renderLog(job.log);
            toggle.hidden = false;
        } else {
            toggle.hidden = true;
            logList.hidden = true;
            toggle.setAttribute('aria-expanded', 'false');
            bar.classList.remove('is-open');
        }
    }
    if (toggle && logList) {
        toggle.addEventListener('click', function () {
            var open = logList.hidden;
            logList.hidden = !open;
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            bar.classList.toggle('is-open', open);
        });
    }
    function schedule(ms) { clearTimeout(timer); timer = setTimeout(poll, ms); }
    function poll() {
        fetch('/maintenance/status', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) { schedule(60000); return; }
                if (data.active) {
                    var running = data.jobs.filter(function (j) { return j.state === 'running' || j.state === 'paused'; });
                    var others = running.filter(function (j) { return !isOptimize(j); });
                    var opts = running.filter(isOptimize);
                    text.textContent = others.map(line).concat(opts.map(optimizeLine)).join('   |   ');
                    bar.classList.toggle('is-paused', running.length > 0 && running.every(function (j) { return j.state === 'paused'; }));
                    bar.classList.remove('is-done');
                    bar.hidden = false;
                    setLogVisible(opts[0]);
                    wasActive = true;
                    schedule(2000);
                } else {
                    var done = data.jobs.filter(function (j) { return j.state === 'completed'; });
                    var failed = data.jobs.filter(function (j) { return j.state === 'error'; });
                    var partials = data.jobs.filter(function (j) { return j.state === 'partial'; });
                    var doneOthers = done.filter(function (j) { return !isOptimize(j); });
                    var doneOpts = done.filter(isOptimize);
                    var failedOthers = failed.filter(function (j) { return !isOptimize(j); });
                    var failedOpts = failed.filter(isOptimize);
                    var partialOpts = partials.filter(isOptimize);
                    if (failed.length) {
                        // 오류는 첫 poll에서도 표시줄에 '오류'와 로그를 보이고,
                        // 다음 상태 변화(재시도 시작·완료) 전까지 유지한다(자동으로 6초 뒤 숨기지 않음).
                        text.textContent = doneOthers.map(function (j) { return j.label + ' 완료' + (j.message ? ' · ' + j.message : ''); })
                            .concat(doneOpts.map(function (j) { return j.label + ' 완료' + (j.message ? ' · ' + j.message : ''); }))
                            .concat(failedOthers.map(function (j) { return j.label + ' 오류' + (j.message ? ' · ' + j.message : ''); }))
                            .concat(failedOpts.map(function (j) { return j.label + ' 오류' + (j.message ? ' · ' + j.message : ''); }))
                            .join('   |   ') || '작업 오류';
                        bar.classList.add('is-done');
                        setLogVisible(failedOpts[0] || doneOpts[0] || failedOthers[0]);
                        bar.hidden = false;
                        wasActive = true;
                        schedule(30000);
                    } else if (partialOpts.length) {
                        // 보류 partial은 실행 중이 아니다(active=false). 조용히 보류 건수·다음 재시도만
                        // 보이고 무기한 실행 중처럼 보이지 않게 한다(Opus P3).
                        text.textContent = partialOpts.map(optimizeLine).join('   |   ');
                        bar.classList.add('is-done');
                        bar.classList.add('is-paused');
                        setLogVisible(partialOpts[0]);
                        bar.hidden = false;
                        wasActive = false;
                        schedule(30000);
                    } else if (wasActive) {
                        text.textContent = doneOthers.map(function (j) { return j.label + ' 완료' + (j.message ? ' · ' + j.message : ''); })
                            .concat(doneOpts.map(function (j) { return j.label + ' 완료' + (j.message ? ' · ' + j.message : ''); }))
                            .join('   |   ') || '작업 완료';
                        bar.classList.add('is-done');
                        setLogVisible(doneOpts[0]);
                        wasActive = false;
                        setTimeout(function () { bar.hidden = true; }, 6000);
                        schedule(30000);
                    } else {
                        bar.hidden = true;
                        schedule(30000);
                    }
                }
            })
            .catch(function () { schedule(60000); });
    }
    poll();
})();

// ── 최신 버전 상태 표시 ──
(function() {
    var el = document.getElementById('sidebarVersionStatus');
    if (!el) return;
    function checkVersion() {
        fetch('/version/latest')
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (d.status === 'up_to_date') {
                    el.innerHTML = '<span class="text-success">&#10003; 최신 버전</span>';
                } else if (d.status === 'outdated') {
                    el.innerHTML = '<span class="text-warning">&#9650; 최신: v' + d.latest + '</span>';
                } else {
                    el.innerHTML = '<span class="text-body-secondary">버전 확인 불가</span>';
                }
            })
            .catch(function() {
                el.innerHTML = '<span class="text-body-secondary">버전 확인 불가</span>';
            });
    }
    checkVersion();
    setInterval(checkVersion, 5 * 60 * 1000);
})();


(function () {
    'use strict';
    var banner = document.getElementById('srRebuildBanner');
    if (!banner || window.location.pathname.indexOf('/onboarding/') === 0) return;
    var ACTIVE = { preparing_backup: 1, running: 1, validating: 1, committing: 1, paused: 1, awaiting_confirmation: 1 };
    var timer, disposed = false;
    function refresh() {
    fetch('/settings/community/rebuild', { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (res) {
            var d = res && res.data;
            if (!d || disposed) return;
            if (ACTIVE[d.state]) {
                var labels = {preparing_backup:'백업 중', running:'진행 중', validating:'검증 중', committing:'마무리 중', paused:'일시정지', awaiting_confirmation:'확인 대기'};
                var c = d.counts || {};
                document.getElementById('srRebuildText').textContent = '초기화 크롤링 ' + labels[d.state] + ' · 읽음 ' + (c.fetched || 0) + '건 · 남음 ' + ((c.pending || 0) + (c.failed_retryable || 0)) + '건. 일반 수집은 완료 후 가능합니다.';
            } else if (d.required && d.legacy_reset) {
                document.getElementById('srRebuildText').textContent = '이전 버전 DB 는 이번 업데이트에서 옮기지 않고 백업한 뒤 비웠습니다. 초기화 크롤링으로 신고를 다시 수집해 주세요.';
            }
            var done = d.state === 'completed' || d.state === 'completed_with_gaps' || d.state === 'not_required';
            banner.hidden = done || !(d.required || ACTIVE[d.state]);
            banner.style.display = banner.hidden ? '' : 'flex';
            document.dispatchEvent(new CustomEvent('sr:rebuild-state', {detail:d}));
        }).catch(function () {}).finally(function () { if (!disposed) timer = setTimeout(refresh, 5000); });
    }
    refresh();
    window.addEventListener('pagehide', function () { disposed = true; clearTimeout(timer); });
})();

// Server-configured update guidance uses textContent; never interpret notice HTML.
// The server only returns a notice when this app is older than min_version (or after HTTP 426).
// info/warning: banner only. required: banner plus one dialog per notice content per day.
(function () {
    var banner = document.getElementById('srClientNotice');
    if (!banner) return;
    var LEVEL_CLASS = {info: 'alert-info', warning: 'alert-warning', required: 'alert-danger'};
    function hashOf(text) {
        var h = 5381;
        for (var i = 0; i < text.length; i++) { h = ((h << 5) + h + text.charCodeAt(i)) | 0; }
        return String(h >>> 0);
    }
    function shownToday(key) {
        try {
            // 하루 기준은 한국 시간(KST, UTC+9) 자정이다.
            var today = new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
            var seen = JSON.parse(window.localStorage.getItem('srNoticeSeen') || '{}');
            if (seen[key] === today) return true;
            seen[key] = today;
            window.localStorage.setItem('srNoticeSeen', JSON.stringify(seen));
        } catch (e) { /* storage unavailable: show again next time */ }
        return false;
    }
    function safeUrl(notice) {
        var url = notice.url && notice.url.pc;
        return (typeof url === 'string' && /^https:\/\//.test(url)) ? url : null;
    }
    function render(notice) {
        var valid = notice && typeof notice.message === 'string' && LEVEL_CLASS[notice.level];
        banner.hidden = !valid;
        if (!valid) return;
        banner.className = 'alert small ' + LEVEL_CLASS[notice.level];
        banner.querySelector('span').textContent = notice.message;
        var link = banner.querySelector('a'), url = safeUrl(notice);
        // 안내에 링크가 없으면 이전 안내의 링크가 남지 않게 기본 다운로드 주소(템플릿 값)로 되돌린다.
        if (!link.dataset.defaultHref) link.dataset.defaultHref = link.getAttribute('href') || '';
        link.href = url || link.dataset.defaultHref;
        if (notice.level !== 'required' || shownToday(hashOf(notice.level + '|' + notice.message))) return;
        if (window.srConfirm) {
            window.srConfirm({title: '업데이트 필요', message: notice.message, confirmLabel: url ? '업데이트 다운로드' : '확인', cancelLabel: '나중에'})
                .then(function (ok) { if (ok && url) window.open(url, '_blank', 'noopener'); });
        }
    }
    // 업로드 일시 중지(서버 점검, 2-9): 서버가 만든 문구와 운영자 안내를 평문으로만 보인다.
    var pauseBanner = document.getElementById('srUploadPauseBanner');
    function renderPause(pause) {
        if (!pauseBanner) return;
        var on = !!(pause && pause.paused && typeof pause.text === 'string');
        pauseBanner.hidden = !on;
        if (!on) return;
        document.getElementById('srUploadPauseText').textContent = pause.text;
        var extra = document.getElementById('srUploadPauseMessage');
        var message = typeof pause.message === 'string' ? pause.message : '';
        extra.textContent = message;
        extra.hidden = !message;
    }
    function refresh() {
        fetch('/settings/community/gate', {credentials: 'same-origin', headers: {'Accept': 'application/json'}})
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                render(d && d.data && d.data.client_notice);
                renderPause(d && d.data && d.data.upload_pause);
            })
            .catch(function () {});
    }
    refresh(); setInterval(refresh, 60000);
})();
