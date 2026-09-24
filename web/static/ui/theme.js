/* 테마 전환(시스템/라이트/다크). 선택은 localStorage 'sr-theme' — 접근 실패해도 동작한다. */
(function () {
    var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

    function read() { try { return localStorage.getItem('sr-theme') || 'system'; } catch (e) { return 'system'; } }
    function write(v) { try { localStorage.setItem('sr-theme', v); } catch (e) {} }
    function resolve(choice) { return choice === 'dark' || (choice === 'system' && media && media.matches) ? 'dark' : 'light'; }

    function apply(choice) {
        var root = document.documentElement;
        root.setAttribute('data-bs-theme', resolve(choice));
        root.setAttribute('data-sr-theme-choice', choice);
        document.querySelectorAll('.sr-theme-switch button[data-sr-theme]').forEach(function (btn) {
            btn.setAttribute('aria-pressed', String(btn.getAttribute('data-sr-theme') === choice));
        });
        document.dispatchEvent(new CustomEvent('sr:themechange', { detail: { choice: choice, theme: resolve(choice) } }));
    }

    document.addEventListener('click', function (event) {
        var btn = event.target.closest('.sr-theme-switch button[data-sr-theme]');
        if (!btn) return;
        event.preventDefault();
        var choice = btn.getAttribute('data-sr-theme');
        write(choice);
        apply(choice);
    });
    if (media && media.addEventListener) {
        media.addEventListener('change', function () { if (read() === 'system') apply('system'); });
    }
    document.addEventListener('DOMContentLoaded', function () { apply(read()); });
})();
