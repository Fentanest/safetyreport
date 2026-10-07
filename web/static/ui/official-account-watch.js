(function () {
    'use strict';
    var banner = document.getElementById('srCloudBanner'), text = document.getElementById('srCloudText');
    var gate = null, busy = false, deadline = 0, nextRead = 0;
    function render() {
        if (!banner || !gate) return;
        var delayed = !!(gate.cloud && gate.cloud.next_attempt_at) || gate.state === 'cloud_unavailable';
        banner.hidden = !delayed;
        if (!delayed) return;
        var seconds = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
        var clock = String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0');
        text.textContent = '서버 연결 지연 · ' + (seconds ? clock + ' 후 다시 확인' : '연결 확인 대기') +
            (gate.upload_pending == null ? ' · 업로드 상태 확인 중' : gate.upload_pending > 0 ? ' · 업로드 대기 중' : ' · 업로드 대기 없음');
    }
    async function check() {
        if (busy || document.hidden || Date.now() < nextRead) return;
        busy = true; nextRead = Date.now() + 30000;
        try {
            var response = await fetch('/settings/community/gate', {cache: 'no-store'});
            if (!response.ok) return;
            gate = (await response.json()).data || {};
            deadline = Number((gate.cloud || {}).next_attempt_at || 0) * 1000;
            render();
            if (gate.can_local || gate.can_browse) {
                if (/^\/onboarding\/(cloud|community)$/.test(location.pathname)) location.replace('/');
            } else if (['official_account_mismatch', 'official_account_taken', 'official_account_change_pending'].includes(gate.state)) {
                if (!/^\/settings\/?$/.test(location.pathname)) location.assign('/settings/');
            }
        } catch (_) { /* Existing page remains usable. */ }
        finally { busy = false; }
    }
    var clockTimer = setInterval(render, 1000);
    var readTimer = setInterval(check, 30000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) check(); });
    window.addEventListener('pagehide', function () { clearInterval(clockTimer); clearInterval(readTimer); });
    check();
}());
