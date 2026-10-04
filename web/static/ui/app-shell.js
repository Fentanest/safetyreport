/* 공통 셸(EO R-15: base.html 에서 옮김): 모바일 사이드바, 떠 있는 버튼, 세션 만료 이동, 안전신문고 새 창 링크. */
// 모바일 사이드바 토글
(function() {
    var sidebar = document.getElementById('mainSidebar');
    var overlay = document.getElementById('sidebarOverlay');
    var toggleBtn = document.getElementById('btnSidebarToggle');

    // 좁은 화면에서 닫힌 사이드바는 inert — 화면 밖 링크로 Tab 포커스가 가지 않게 한다. 열면 첫 링크로,
    // Esc·바깥 클릭으로 닫으면 토글 버튼으로 포커스를 돌린다(기술일지 C07).
    var narrow = window.matchMedia('(max-width: 767.98px)');
    function syncInert() {
        var hidden = narrow.matches && !sidebar.classList.contains('sidebar-open');
        if (hidden) sidebar.setAttribute('inert', ''); else sidebar.removeAttribute('inert');
        toggleBtn.setAttribute('aria-expanded', sidebar.classList.contains('sidebar-open') ? 'true' : 'false');
    }
    function openSidebar() {
        sidebar.classList.add('sidebar-open');
        overlay.classList.add('sidebar-open');
        syncInert();
        var first = sidebar.querySelector('a[href], button:not([disabled])');
        if (first) first.focus();
    }
    function closeSidebar(restoreFocus) {
        var wasOpen = sidebar.classList.contains('sidebar-open');
        sidebar.classList.remove('sidebar-open');
        overlay.classList.remove('sidebar-open');
        syncInert();
        if (wasOpen && restoreFocus === true) toggleBtn.focus();
    }

    toggleBtn.setAttribute('aria-controls', 'mainSidebar');
    toggleBtn.addEventListener('click', openSidebar);
    overlay.addEventListener('click', function () { closeSidebar(true); });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && sidebar.classList.contains('sidebar-open')) closeSidebar(true);
    });
    if (narrow.addEventListener) narrow.addEventListener('change', syncInert); else narrow.addListener(syncInert);
    syncInert();
    // 사이드바 링크 클릭 시 자동 닫기
    sidebar.querySelectorAll('a').forEach(function(a) {
        a.addEventListener('click', closeSidebar);
    });
})();

(function () {
    var fab = document.querySelector('.floating-search-btn');
    if (!fab) return;
    document.body.classList.add('has-floating-btn');
    if (!fab.getAttribute('aria-label')) fab.setAttribute('aria-label', fab.textContent.trim());
    var lastY = window.scrollY, narrow = window.matchMedia('(max-width: 767.98px)');
    window.addEventListener('scroll', function () {
        var y = window.scrollY;
        fab.classList.toggle('is-compact', narrow.matches && y > 120 && y >= lastY);
        lastY = y;
    }, { passive: true });
})();

// Offcanvas 열릴 때 떠 있는 버튼 숨기기. 휴대폰 폭의 메뉴 버튼(z-index 1046)은 offcanvas(1045) 위라 제목을 가린다.
function setFloatingButtonsHidden(hidden) {
    document.querySelectorAll('.floating-search-btn, .btn-sidebar-toggle').forEach(function (btn) {
        btn.style.visibility = hidden ? 'hidden' : '';
    });
}
document.addEventListener('show.bs.offcanvas', function() { setFloatingButtonsHidden(true); });
document.addEventListener('hidden.bs.offcanvas', function() { setFloatingButtonsHidden(false); });

// 세션 만료(401) 시 로그인 화면으로 이동
(function() {
    var _redirecting = false;
    function handleSessionExpired() {
        if (_redirecting) return;
        _redirecting = true;
        window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname);
    }

    // fetch 인터셉터
    var _origFetch = window.fetch;
    window.fetch = function() {
        return _origFetch.apply(this, arguments).then(function(response) {
            if (response.status === 401) {
                handleSessionExpired();
            }
            return response;
        });
    };

    // XMLHttpRequest 인터셉터
    var _origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function() {
        this.addEventListener('load', function() {
            if (this.status === 401) {
                handleSessionExpired();
            }
        });
        return _origOpen.apply(this, arguments);
    };
})();

// 안전신문고 링크 클릭 시 첫 TCP 연결 RST 오류 우회
// fetch로 도메인 연결을 먼저 워밍업한 뒤 탭을 엽니다.
document.addEventListener('click', function(e) {
    var anchor = e.target.closest('a[target="_blank"]');
    if (!anchor) return;
    var href = anchor.getAttribute('href') || '';
    if (!href.includes('safetyreport.go.kr')) return;

    e.preventDefault();
    fetch('https://www.safetyreport.go.kr/', { mode: 'no-cors', cache: 'no-store' })
        .catch(function() {})
        .finally(function() {
            window.open(href, '_blank');
        });
});
