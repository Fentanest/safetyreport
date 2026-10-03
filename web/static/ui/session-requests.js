(function () {
    'use strict';
    function token() { return document.querySelector('meta[name="csrf-token"]')?.content || ''; }
    function mutation(url, method) {
        try {
            const target = new URL(url, location.href);
            return target.origin === location.origin && !target.pathname.startsWith('/api/v1/') &&
                !['GET', 'HEAD', 'OPTIONS'].includes(String(method || 'GET').toUpperCase());
        } catch (_) { return false; }
    }
    const originalFetch = window.fetch;
    window.fetch = function (input, options) {
        const request = input instanceof Request ? input : null;
        const url = request ? request.url : String(input);
        const method = options?.method || request?.method || 'GET';
        if (mutation(url, method)) {
            options = {...options, headers: new Headers(options?.headers || request?.headers)};
            if (!options.headers.has('X-CSRF-Token')) options.headers.set('X-CSRF-Token', token());
        }
        return originalFetch.call(this, input, options);
    };
    function protectForm(form) {
        if (!mutation(form.action, form.method)) return;
        let input = form.querySelector('input[name="_csrf_token"]');
        if (!input) {
            input = document.createElement('input');
            input.type = 'hidden'; input.name = '_csrf_token'; form.appendChild(input);
        }
        input.value = token();
    }
    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('form').forEach(protectForm);
        if (window.jQuery) window.jQuery.ajaxPrefilter(function (options, original, xhr) {
            if (mutation(options.url, options.type)) xhr.setRequestHeader('X-CSRF-Token', token());
        });
    });
    document.addEventListener('submit', function (event) { protectForm(event.target); }, true);
})();
