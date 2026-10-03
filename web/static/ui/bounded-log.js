(function () {
    'use strict';
    const limit = 256 * 1024;
    const encoder = new TextEncoder(), decoder = new TextDecoder();
    function bounded(text) {
        const bytes = encoder.encode(text);
        let start = Math.max(0, bytes.length - limit);
        while (start < bytes.length && (bytes[start] & 0xc0) === 0x80) start++;
        return decoder.decode(bytes.subarray(start)).split('\n').slice(-2000).join('\n');
    }
    window.SrBoundedLog = function (element) {
        let pending = '', timer = null, disposed = false;
        function flush() {
            timer = null;
            if (disposed) return;
            const follow = element.scrollHeight - element.clientHeight - element.scrollTop < 24;
            const top = element.scrollTop, height = element.scrollHeight;
            element.textContent = bounded(element.textContent + pending);
            pending = '';
            element.scrollTop = follow ? element.scrollHeight : Math.max(0, top + Math.min(0, element.scrollHeight - height));
        }
        return {
            append(text) {
                if (disposed) return;
                pending = bounded(pending + text);
                if (!timer) timer = setTimeout(flush, 100);
            },
            reset() { pending = ''; clearTimeout(timer); timer = null; element.textContent = ''; },
            dispose() { disposed = true; pending = ''; clearTimeout(timer); }
        };
    };
})();
