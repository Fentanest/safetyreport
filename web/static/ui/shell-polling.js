/* 공통 셸의 상태 확인(EO R-15: base.html 인라인 스크립트를 옮겼다): 업데이트 뒤 작업 진행 바, 최신 버전 표시, 초기화 크롤링 안내. */
(function () {
    var bar = document.getElementById('srJobBar');
    var text = document.getElementById('srJobText');
    if (!bar || !document.getElementById('mainSidebar')) return; // 로그인 전 화면에서는 부르지 않는다
    var wasActive = false, timer = null;
    function line(job) {
        var parts = [job.label];
        if (job.total) parts.push(job.done + '/' + job.total);
        if (job.current) parts.push(job.current);
        if (job.message) parts.push(job.message);
        return parts.join(' · ');
    }
    function schedule(ms) { clearTimeout(timer); timer = setTimeout(poll, ms); }
    function poll() {
        fetch('/maintenance/status', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) { schedule(60000); return; }
                if (data.active) {
                    var running = data.jobs.filter(function (j) { return j.state === 'running' || j.state === 'paused'; });
                    text.textContent = running.map(line).join('   |   ');
                    bar.classList.toggle('is-paused', running.every(function (j) { return j.state === 'paused'; }));
                    bar.classList.remove('is-done');
                    bar.hidden = false;
                    wasActive = true;
                    schedule(2000);
                } else if (wasActive) {
                    var done = data.jobs.filter(function (j) { return j.state === 'completed'; });
                    text.textContent = done.length ? done.map(function (j) { return j.label + ' 완료' + (j.message ? ' · ' + j.message : ''); }).join('   |   ') : '작업 완료';
                    bar.classList.add('is-done');
                    wasActive = false;
                    setTimeout(function () { bar.hidden = true; }, 6000);
                    schedule(30000);
                } else {
                    bar.hidden = true;
                    schedule(30000);
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
