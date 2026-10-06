(function () {
    'use strict';
    var form = document.getElementById('mainSettingsForm');
    var username = document.getElementById('fld_username');
    var confirmField = document.getElementById('officialAccountConfirm');
    var confirmed = false;
    function normalize(s) { return (s || '').trim().toLowerCase(); }
    form.addEventListener('submit', async function (event) {
        if (confirmed) return;
        event.preventDefault();
        var response;
        try {
            response = await fetch('/settings/community/gate', {cache: 'no-store'});
            if (!response.ok) throw new Error('gate');
            var body = await response.json();
            var state = (body.data || {}).state;
            if (state === 'cloud_unavailable' || state === 'official_account_protocol_required') {
                window.location.assign('/onboarding/cloud'); return;
            }
            var changed = normalize(username.value) !== normalize(username.dataset.originalUsername);
            if (changed || state === 'official_account_mismatch' || state === 'official_account_change_pending' ||
                    new URLSearchParams(location.search).get('binding_error') === 'official_account_change_confirmation') {
                var ok = await window.srConfirm({title: '안전신문고 계정 변경', danger: true,
                    message: '다른 계정으로 변경하면 기존 데이터가 지워집니다. 개인 DB를 백업한 뒤 기존 공유자료와 신고 내역을 초기화하고 새 계정으로 시작합니다. 바인딩된 계정으로 되돌리는 경우에는 데이터를 유지합니다.',
                    items: [{label: '남는 항목', text: '관리자 계정, API 키, 감시목록, 지오코딩 캐시, 개인 DB 백업'}],
                    confirmLabel: '백업 후 초기화하고 저장'});
                if (!ok) return;
                confirmField.value = 'DELETE_OLD_OFFICIAL_ACCOUNT_DATA';
            }
            confirmed = true;
            form.requestSubmit();
        } catch (_) { window.location.assign('/onboarding/cloud'); }
    });
}());
