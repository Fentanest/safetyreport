/* 되돌릴 수 없는 조작(DB 초기화 크롤·카카오 로그아웃·동의 철회 등)의 공통 확인 창(기술일지 F-09, C02).
 *
 * window.srConfirm({ title, message, items, confirmLabel, cancelLabel, danger, requireText }) → Promise<boolean>
 *   message      : 본문(줄바꿈 \n 은 문단으로 나눈다)
 *   items        : [{ label, text }] — "지워지는 것 / 남는 것"처럼 묶어 보일 목록(선택)
 *   requireText  : 이 문구를 그대로 입력해야 확인 버튼이 켜진다(선택)
 * Bootstrap 모달을 쓸 수 없으면 브라우저 confirm 으로 대신한다. 모든 글자는 textContent 로 넣는다.
 */
(function () {
    'use strict';

    function el(tag, cls, text) {
        var node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    function plainText(opts) {
        var parts = [opts.title || '', opts.message || ''];
        (opts.items || []).forEach(function (item) { parts.push(item.label + ': ' + item.text); });
        return parts.filter(Boolean).join('\n\n');
    }

    window.srConfirm = function (opts) {
        opts = opts || {};
        if (!window.bootstrap || !window.bootstrap.Modal) {
            return Promise.resolve(window.confirm(plainText(opts)));
        }
        return new Promise(function (resolve) {
            var id = 'srConfirm' + Date.now();
            var modal = el('div', 'modal fade');
            modal.id = id;
            modal.tabIndex = -1;
            modal.setAttribute('role', 'dialog');
            modal.setAttribute('aria-modal', 'true');
            modal.setAttribute('aria-labelledby', id + 'Title');
            var dialog = el('div', 'modal-dialog modal-dialog-centered');
            var content = el('div', 'modal-content');
            var header = el('div', 'modal-header');
            var title = el('h5', 'modal-title', opts.title || '확인');
            title.id = id + 'Title';
            var close = el('button', 'btn-close');
            close.type = 'button';
            close.setAttribute('data-bs-dismiss', 'modal');
            close.setAttribute('aria-label', '닫기');
            header.appendChild(title);
            header.appendChild(close);

            var body = el('div', 'modal-body');
            String(opts.message || '').split('\n').filter(function (line) { return line.trim(); }).forEach(function (line) {
                body.appendChild(el('p', 'mb-2', line));
            });
            if (opts.items && opts.items.length) {
                var list = el('dl', 'sr-confirm-items mb-2');
                opts.items.forEach(function (item) {
                    list.appendChild(el('dt', 'small', item.label));
                    list.appendChild(el('dd', 'small mb-2', item.text));
                });
                body.appendChild(list);
            }
            var input = null;
            if (opts.requireText) {
                var label = el('label', 'form-label small mt-2');
                label.htmlFor = id + 'Input';
                label.appendChild(document.createTextNode('계속하려면 '));
                label.appendChild(el('strong', null, opts.requireText));
                label.appendChild(document.createTextNode(' 을(를) 그대로 입력하세요.'));
                input = el('input', 'form-control');
                input.id = id + 'Input';
                input.type = 'text';
                input.autocomplete = 'off';
                body.appendChild(label);
                body.appendChild(input);
            }

            var footer = el('div', 'modal-footer');
            var cancel = el('button', 'btn btn-outline-secondary', opts.cancelLabel || '취소');
            cancel.type = 'button';
            cancel.setAttribute('data-bs-dismiss', 'modal');
            var ok = el('button', 'btn ' + (opts.danger ? 'btn-danger' : 'btn-primary'), opts.confirmLabel || '확인');
            ok.type = 'button';
            ok.setAttribute('data-sr-confirm', 'ok');
            if (input) ok.disabled = true;
            footer.appendChild(cancel);
            footer.appendChild(ok);

            content.appendChild(header);
            content.appendChild(body);
            content.appendChild(footer);
            dialog.appendChild(content);
            modal.appendChild(dialog);
            document.body.appendChild(modal);

            var answer = false;
            var instance = new window.bootstrap.Modal(modal);
            if (input) {
                input.addEventListener('input', function () { ok.disabled = input.value.trim() !== opts.requireText; });
                input.addEventListener('keydown', function (e) {
                    if (e.key === 'Enter' && !ok.disabled) { e.preventDefault(); ok.click(); }
                });
            }
            ok.addEventListener('click', function () { answer = true; instance.hide(); });
            modal.addEventListener('shown.bs.modal', function () { (input || cancel).focus(); });
            modal.addEventListener('hidden.bs.modal', function () {
                instance.dispose();
                modal.remove();
                resolve(answer);
            });
            instance.show();
        });
    };
})();
