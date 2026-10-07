(function () {
    'use strict';
    var busy = false;
    async function check(fresh) {
        if (busy || document.hidden || location.pathname === '/onboarding/cloud') return;
        busy = true;
        try {
            var r = await fetch(fresh ? '/settings/official-account/retry' : '/settings/community/gate', {
                method: fresh ? 'POST' : 'GET', cache: 'no-store',
                headers: fresh ? {'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content} : {}
            });
            if (!r.ok) return;
            var gate = (await r.json()).data || {};
            // Supabase 무료 플랜 장애: 조회 가능하면 클라우드 확인 화면에 가두지 않는다.
            if (gate.can_browse) return;
            var state = gate.state;
            if (['official_account_mismatch', 'official_account_taken', 'official_account_change_pending'].includes(state)) {
                if (!/^\/settings\/?$/.test(location.pathname)) location.assign('/settings/');
            } else if (['cloud_unavailable', 'official_account_protocol_required'].includes(state)) {
                location.assign('/onboarding/cloud');
            }
        } finally { busy = false; }
    }
    document.addEventListener('visibilitychange', function () { if (!document.hidden) check(true).catch(function () {}); });
    var timer = setInterval(function () { check().catch(function () {}); }, 300000);
    window.addEventListener('pagehide', function () { clearInterval(timer); });
}());
