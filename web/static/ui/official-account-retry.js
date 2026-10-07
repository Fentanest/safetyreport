(function () {
    'use strict';
    var button = document.getElementById('cloudRetry');
    var status = document.getElementById('cloudRetryStatus');
    var delays = [2000, 5000, 10000], attempt = 0, busy = false, timer;
    async function retry() {
        if (busy) return;
        clearTimeout(timer);
        busy = true; button.disabled = true;
        status.textContent = '연결을 확인하고 있습니다. 응답까지 잠시 기다려 주세요.';
        try {
            var r = await fetch('/settings/official-account/retry', {method: 'POST',
                headers: {'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content}, cache: 'no-store'});
            if (!r.ok) throw new Error('retry');
            var g = (await r.json()).data;
            if (g.can_enter || g.can_browse) { location.replace('/'); return; }
            if (['official_account_mismatch', 'official_account_taken', 'official_account_change_pending'].includes(g.state)) {
                location.replace('/settings/'); return;
            }
            if (!['cloud_unavailable', 'official_account_protocol_required', 'verification_required'].includes(g.state)) {
                location.replace('/onboarding/community'); return;
            }
        } catch (_) { /* Keep the retry page visible. */ }
        finally { busy = false; button.disabled = false; }
        status.textContent = attempt < delays.length ? '연결되지 않았습니다. 잠시 후 다시 시도합니다.' : '자동 재시도를 마쳤습니다. 다시 시도 버튼을 눌러 주세요.';
        schedule();
    }
    function schedule() { if (attempt < delays.length) timer = setTimeout(retry, delays[attempt++]); }
    button.addEventListener('click', function () { attempt = 0; retry(); });
    window.addEventListener('pagehide', function () { clearTimeout(timer); });
    schedule();
}());
