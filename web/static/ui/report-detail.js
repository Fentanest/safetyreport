/* 신고 상세 팝업(EO R-15: base.html 에서 옮김): 상세 렌더러·보완 이력·첨부 미디어(proxy) 제어.
   전역 접점: showReportDetail, proxyMediaUrl, prepareProxyVideos, resetProxyVideos, srDisplayDateTime. 모달 마크업은 components/report_detail_modal.html. */
// ── 신고 상세 팝업 ─────────────────────────────────────────────
(function() {
    var _modal = null;
    function getModal() {
        if (!_modal) {
            var el = document.getElementById('reportDetailModal');
            _modal = new bootstrap.Modal(el);
            el.addEventListener('hidden.bs.modal', function() {
                resetProxyVideos(el);
            });
        }
        return _modal;
    }

    function esc(v) {
        return String(v == null ? '' : v)
            .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function normalizeDisplayValue(value) {
        if (value == null) return '';
        if (Array.isArray(value)) {
            return value.map(normalizeDisplayValue).filter(Boolean).join('\n');
        }
        if (typeof value === 'object') {
            if (typeof value.url === 'string') return value.url.trim();
            if (typeof value.href === 'string') return value.href.trim();
            try { return JSON.stringify(value); } catch (_) { return String(value); }
        }
        return String(value).trim();
    }

    function hasMeaningfulValue(value) {
        var text = normalizeDisplayValue(value);
        return !!text && text !== '미확인';
    }

    function splitUrls(raw) {
        var text = normalizeDisplayValue(raw);
        if (!text || text === '6개월 초과') return [];
        return text.split(/\n|%0A|%0a/).map(function(s){return s.trim();}).filter(function(value) {
            if (!value) return false;
            try { return ['http:', 'https:'].includes(new URL(value, location.href).protocol); }
            catch (_) { return false; }
        });
    }

    function isVideo(url) {
        try { var p = new URL(url, location.href).pathname.toLowerCase(); return /\.(mp4|mov|avi|webm|mkv)$/.test(p); } catch(e) { return false; }
    }
    function isImage(url) {
        try { var p = new URL(url, location.href).pathname.toLowerCase(); return /\.(jpg|jpeg|png|gif|webp|bmp)$/.test(p); } catch(e) { return false; }
    }
    function proxyMediaUrl(url) {
        return '/media/proxy?url=' + encodeURIComponent(normalizeDisplayValue(url));
    }
    window.proxyMediaUrl = proxyMediaUrl;

    function setProxyVideoStatus(video, message, isError) {
        var statusEl = video && video.parentElement ? video.parentElement.querySelector('[data-proxy-video-status]') : null;
        if (!statusEl) return;
        if (!message) {
            statusEl.style.display = 'none';
            return;
        }
        statusEl.textContent = message;
        statusEl.style.display = '';
        statusEl.classList.toggle('text-danger', !!isError);
        statusEl.classList.toggle('text-muted', !isError);
    }

    function activateProxyVideo(video, proxyUrl) {
        if (video.getAttribute('src') === proxyUrl) return;
        if (!video.hasAttribute('data-proxy-error-bound')) {
            video.setAttribute('data-proxy-error-bound', '1');
            video.addEventListener('error', function() {
                if (video.getAttribute('src')) {
                    setProxyVideoStatus(video, '동영상 준비 실패', true);
                }
            });
            video.addEventListener('loadeddata', function() {
                setProxyVideoStatus(video, '');
            });
        }
        video.setAttribute('src', proxyUrl);
        video.load();
        setProxyVideoStatus(video, '');
    }

    // /media/proxy 가 캐시 완성을 기다리지 않고 받는 즉시 흘려보내므로(tail-follow),
    // 준비 완료 폴링 없이 곧바로 src 를 붙인다. prepare 는 백그라운드 캐시 워머를
    // 조금 먼저 깨우는 용도라 실패해도 재생 자체에는 영향이 없다.
    function prepareSingleProxyVideo(video, token) {
        var sourceUrl = normalizeDisplayValue(video.getAttribute('data-source-url'));
        var proxyUrl = normalizeDisplayValue(video.getAttribute('data-proxy-src'));
        if (!sourceUrl || !proxyUrl) return Promise.resolve();

        setProxyVideoStatus(video, '동영상 준비 중...');
        try {
            fetch('/media/prepare?url=' + encodeURIComponent(sourceUrl), {
                method: 'POST',
                headers: { 'Accept': 'application/json' },
                cache: 'no-store'
            }).catch(function() {});
        } catch (_) {}

        var scope = video.closest('[data-proxy-video-scope]');
        if (scope && scope.getAttribute('data-proxy-video-token') !== token) {
            return Promise.resolve();
        }
        activateProxyVideo(video, proxyUrl);
        return Promise.resolve();
    }

    function prepareProxyVideos(scope) {
        if (!scope || !scope.querySelectorAll) return;
        var token = String(Date.now()) + Math.random().toString(16).slice(2);
        scope.setAttribute('data-proxy-video-token', token);
        Array.prototype.forEach.call(scope.querySelectorAll('video[data-proxy-src]'), function(video) {
            try { video.removeAttribute('src'); video.load(); } catch (_) {}
            setProxyVideoStatus(video, '동영상 준비 중...');
            prepareSingleProxyVideo(video, token).catch(function(error) {
                console.error('proxy video prepare failed', error);
                setProxyVideoStatus(video, '동영상 준비 실패', true);
            });
        });
    }
    window.prepareProxyVideos = prepareProxyVideos;

    function resetProxyVideos(scope) {
        if (!scope || !scope.querySelectorAll) return;
        scope.setAttribute('data-proxy-video-token', 'closed-' + Date.now());
        Array.prototype.forEach.call(scope.querySelectorAll('video'), function(video) {
            try { video.pause(); video.removeAttribute('src'); video.load(); } catch(_) {}
        });
        Array.prototype.forEach.call(scope.querySelectorAll('[data-proxy-video-status]'), function(statusEl) {
            statusEl.style.display = '';
            statusEl.textContent = '동영상 준비 중...';
            statusEl.classList.remove('text-danger');
            statusEl.classList.add('text-muted');
        });
    }
    window.resetProxyVideos = resetProxyVideos;

    function statusBadge(s) {
        s = normalizeDisplayValue(s);
        if (!s) return '';
        var cls = 'sr-badge-' + window.SrReportPolicy.badgeKey(s);
        return '<span class="sr-badge ' + cls + '">' + esc(s) + '</span>';
    }

    function field(icon, label, value) {
        var text = normalizeDisplayValue(value);
        if (!hasMeaningfulValue(text)) return '';
        return '<div class="col-12 col-sm-6"><div class="d-flex align-items-start">' +
            '<i class="' + icon + ' text-muted me-2 rd-field-icon"></i>' +
            '<span class="text-muted me-2 flex-shrink-0 rd-field-label">' + label + '</span>' +
            '<span class="fw-medium rd-field-value">' + esc(text) + '</span>' +
            '</div></div>';
    }

    /// 신고 카테고리(traffic/parking/other) 데이터 리스트로 이동하면서
    /// queryKey=value 검색 조건을 미리 적용. category 가 없으면 /data/all 로 fallback.
    function buildFilterUrl(category, queryKey, value) {
        var path = '/data/' + (category || 'all');
        return path + '?' + queryKey + '=' + encodeURIComponent(normalizeDisplayValue(value));
    }

    function linkField(icon, label, value, category, queryKey) {
        var text = normalizeDisplayValue(value);
        if (!hasMeaningfulValue(text)) return '';
        var href = buildFilterUrl(category, queryKey, text);
        return '<div class="col-12 col-sm-6"><div class="d-flex align-items-start">' +
            '<i class="' + icon + ' text-muted me-2 rd-field-icon"></i>' +
            '<span class="text-muted me-2 flex-shrink-0 rd-field-label">' + label + '</span>' +
            '<a class="fw-medium text-decoration-underline rd-field-value" href="' + href + '">' + esc(text) +
            ' <i class="fas fa-chevron-right ms-1 rd-field-chevron"></i></a>' +
            '</div></div>';
    }

    function safetyWebUrl(id) {
        return 'https://www.safetyreport.go.kr/#mypage/mysafereport/' + encodeURIComponent(id || '');
    }

    function safetyAppUrl(id) {
        return 'appsafetyreport://view?c_no=' + encodeURIComponent(id || '') +
            '&ext_path=' + encodeURIComponent('M_MY_01_S0002.html') +
            '&mem_yn=Y';
    }

    function openSafetyReport(id) {
        var targetId = normalizeDisplayValue(id);
        if (!targetId) return;

        var appUrl = safetyAppUrl(targetId);
        var webUrl = safetyWebUrl(targetId);
        var fallbackTimer = null;
        var finished = false;

        function cleanup() {
            document.removeEventListener('visibilitychange', onVisibilityChange);
            window.removeEventListener('pagehide', cancelFallback);
            window.removeEventListener('blur', cancelFallback);
        }

        function cancelFallback() {
            if (finished) return;
            finished = true;
            if (fallbackTimer) window.clearTimeout(fallbackTimer);
            cleanup();
        }

        function onVisibilityChange() {
            if (document.hidden) {
                cancelFallback();
            }
        }

        document.addEventListener('visibilitychange', onVisibilityChange);
        window.addEventListener('pagehide', cancelFallback);
        window.addEventListener('blur', cancelFallback);

        fallbackTimer = window.setTimeout(function() {
            if (finished) return;
            finished = true;
            cleanup();
            window.open(webUrl, '_blank');
        }, 1200);

        try {
            var link = document.createElement('a');
            link.href = appUrl;
            link.style.display = 'none';
            document.body.appendChild(link);
            link.click();
            window.setTimeout(function() {
                if (link.parentNode) {
                    link.parentNode.removeChild(link);
                }
            }, 0);
        } catch (error) {
            cancelFallback();
            window.open(webUrl, '_blank');
        }
    }

    function renderSupplementHistory(record) {
        var section = document.getElementById('rdSupplements');
        var bodyEl = document.getElementById('rdSupplementsBody');
        var badgeEl = document.getElementById('rdSupplementsBadge');
        if (!section || !bodyEl) return;

        var count = parseInt(record['보완횟수'], 10);
        var requester = normalizeDisplayValue(record['보완_요청자']);
        var requestedAt = normalizeDisplayValue(record['보완_요청일시']);
        var completedAt = normalizeDisplayValue(record['보완_완료일시']);
        var requestText = normalizeDisplayValue(record['보완_요청_내용']);
        var opinion = normalizeDisplayValue(record['보완_신고자_의견']);
        var isOpen = String(record['보완_미응답'] || 'N') === 'Y';

        if ((!Number.isFinite(count) || count <= 0) && !requester && !requestedAt && !completedAt && !requestText && !opinion) {
            section.style.display = 'none';
            return;
        }
        section.style.display = '';
        if (badgeEl) {
            badgeEl.textContent = (Number.isFinite(count) && count > 0 ? (count + '회') : '');
            badgeEl.style.display = (Number.isFinite(count) && count > 0) ? '' : 'none';
        }

        var statusBadge = isOpen
            ? '<span class="sr-badge sr-badge-reject ms-1">미응답</span>'
            : '<span class="sr-badge sr-badge-withdraw ms-1">응답 완료</span>';

        var html = '';
        html += '<div class="fw-bold small mb-2">마지막 보완 요청' + statusBadge + '</div>';
        if (requester || requestedAt || completedAt) {
            html += '<div class="bg-white rounded p-2 border mb-2 rd-round">';
            if (requester) {
                html += '<div><span class="text-muted">보완 요청자</span> <span class="fw-semibold ms-1">' + esc(requester) + '</span></div>';
            }
            if (requestedAt) {
                html += '<div><span class="text-muted">요청 일시</span> <span class="ms-1">' + esc(requestedAt) + '</span></div>';
            }
            if (completedAt) {
                html += '<div><span class="text-muted">완료 일시</span> <span class="ms-1">' + esc(completedAt) + '</span></div>';
            }
            html += '</div>';
        }
        if (requestText) {
            html += '<div class="bg-light rounded p-2 border rd-text-block">' + esc(requestText) + '</div>';
        }
        if (opinion) {
            html += '<div class="fw-bold text-muted small mt-2 mb-1">신고자 의견</div>';
            html += '<div class="bg-light rounded p-2 border rd-text-block">' + esc(opinion) + '</div>';
        }
        bodyEl.innerHTML = html;
    }

    function fileLabel(url, index) {
        var text = normalizeDisplayValue(url);
        return text.split('?')[0].split('/').filter(Boolean).pop() || ('파일 ' + index);
    }

    function showDetailRenderFallback(record, error) {
        console.error('report detail render failed', error, record);
        document.getElementById('rdName').textContent = normalizeDisplayValue(record['신고명'] || record['신고번호']) || '상세 정보를 불러올 수 없습니다.';
        document.getElementById('rdStatusBadge').innerHTML = '';
        document.getElementById('rdFields').innerHTML =
            field('fas fa-tag', '신고번호', record['신고번호']) +
            field('fas fa-calendar', '신고일', window.srDisplayDateTime(record['신고일'])) +
            field('fas fa-check-circle', '답변일', window.srDisplayDateTime(record['답변일'])) +
            '<div class="col-12"><div class="alert alert-warning py-2 mb-0 rd-small-alert">일부 필드 형식이 예상과 달라 상세 화면을 완전히 그리지 못했습니다.</div></div>';
        document.getElementById('rdReportContent').style.display = 'none';
        document.getElementById('rdProcessContent').style.display = 'none';
        document.getElementById('rdMedia').style.display = 'none';
        document.getElementById('rdVideos').style.display = 'none';
        document.getElementById('rdFiles').style.display = 'none';
        document.getElementById('rdSupplements').style.display = 'none';
        document.getElementById('rdViewBtn').onclick = function() {
            openSafetyReport(record['ID']);
        };
        getModal().show();
    }

    // 날짜·시각 표시는 분까지로 맞춘다(발생일자와 같은 형식). 정렬·내보내기 원문은 그대로(기술일지 O-04).
    window.srDisplayDateTime = function (value) {
        if (typeof value !== 'string') return value;
        var m = value.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}):\d{2}(?:\.\d+)?$/);
        return m ? m[1] + ' ' + m[2] : value;
    };

    window.showReportDetail = function(d) {
        var record = (d && typeof d === 'object') ? d : {};
        try {
            // 헤더
            document.getElementById('rdName').textContent = normalizeDisplayValue(record['신고명']);
            var status = record['처리상태'] || record['결과'] || record['상태'] || '';
            document.getElementById('rdStatusBadge').innerHTML = statusBadge(status);

            // 필드 목록
            var occur = normalizeDisplayValue(record['발생일자']);
            var occurTime = normalizeDisplayValue(record['발생시각']);
            if (occur && occurTime) occur += '  ' + occurTime;
            var rating = record['별점'];
            var ratingDisplay = '';
            if (rating !== null && rating !== undefined && normalizeDisplayValue(rating) !== '') {
                var ratingNum = parseInt(rating, 10);
                ratingDisplay = isNaN(ratingNum) ? normalizeDisplayValue(rating) : ('★ ' + ratingNum + '점');
            }

            // 카테고리 추론: 모바일 호환을 위해 'category' 키를 우선 사용.
            // 없으면 현재 페이지 URL 의 /data/<cat> 로 추정 (data_table.html).
            var category = normalizeDisplayValue(record['category']);
            if (!category) {
                var pathMatch = window.location.pathname.match(/^\/data\/(traffic|parking|other)/);
                if (pathMatch) category = pathMatch[1];
            }

            document.getElementById('rdFields').innerHTML =
                field('fas fa-tag',              '신고번호',    record['신고번호']) +
                field('fas fa-calendar',         '신고일',      window.srDisplayDateTime(record['신고일'])) +
                field('fas fa-check-circle',     '답변일',      window.srDisplayDateTime(record['답변일'])) +
                field('fas fa-building',         '처리기관',    record['처리기관']) +
                linkField('fas fa-user',         '담당자',      record['담당자'],     category, 'person') +
                field('fas fa-coins',            '과태료/범칙금', record['범칙금_과태료']) +
                field('fas fa-exclamation-triangle','벌점',     record['벌점']) +
                linkField('fas fa-car',          '차량번호',    record['차량번호'],   category, 'car') +
                linkField('fas fa-gavel',        '위반법규',    record['위반법규'],   category, 'law') +
                linkField('fas fa-map-marker-alt','위반장소',   record['위반장소'],   category, 'location') +
                field('fas fa-calendar-day',     '발생일자',    occur) +
                field('fas fa-star',             '별점',        ratingDisplay) +
                field('fas fa-comment-dots',     '별점사유',    record['별점사유']);

            // 보완 요약
            renderSupplementHistory(record);

            // 신고내용
            var rc = document.getElementById('rdReportContent');
            var reportContent = normalizeDisplayValue(record['신고내용']);
            if (reportContent) {
                document.getElementById('rdReportContentText').textContent = reportContent;
                rc.style.display = '';
            } else { rc.style.display = 'none'; }

            // 처리내용
            var pc = document.getElementById('rdProcessContent');
            var processContent = normalizeDisplayValue(record['처리내용']);
            if (processContent) {
                document.getElementById('rdProcessContentText').textContent = processContent;
                pc.style.display = '';
            } else { pc.style.display = 'none'; }

            // 미디어 분류
            var mapUrls   = splitUrls(record['지도']);
            var photoUrls = splitUrls(record['첨부사진']);
            var fileUrls  = splitUrls(record['첨부파일']);

            var imageUrls = [], videoUrls = [], otherFiles = [];
            mapUrls.forEach(function(u){ if(imageUrls.indexOf(u)<0) imageUrls.push(u); });
            photoUrls.forEach(function(u){
                if(isVideo(u)){ if(videoUrls.indexOf(u)<0) videoUrls.push(u); }
                else { if(imageUrls.indexOf(u)<0) imageUrls.push(u); }
            });
            fileUrls.forEach(function(u){
                if(isImage(u) && imageUrls.indexOf(u)<0) imageUrls.push(u);
                else if(isVideo(u) && videoUrls.indexOf(u)<0) videoUrls.push(u);
                else otherFiles.push(u);
            });

            function box(className) {
                var el = document.createElement('div');
                el.className = className || 'mb-3 border rounded p-2 bg-light';
                return el;
            }
            function downloadLink(url, label, className) {
                var link = document.createElement('a');
                link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
                link.className = className; link.textContent = label;
                return link;
            }
            var mediaDiv = document.getElementById('rdMedia');
            var mediaList = document.getElementById('rdMediaList');
            mediaList.replaceChildren();
            imageUrls.forEach(function(url) {
                var item = box(), image = document.createElement('img'), error = document.createElement('div');
                image.className = 'img-fluid rounded mb-2';
                image.classList.add('rd-image');
                image.src = url;
                error.className = 'text-muted small'; error.hidden = true;
                error.textContent = '이미지를 불러올 수 없습니다.';
                image.onerror = function() { image.hidden = true; error.hidden = false; };
                item.append(image, error, downloadLink(url, '다운로드', 'btn btn-sm btn-outline-secondary mt-1'));
                mediaList.appendChild(item);
            });
            mediaDiv.style.display = imageUrls.length ? '' : 'none';

            var videoDiv = document.getElementById('rdVideos');
            var videoList = document.getElementById('rdVideoList');
            videoList.replaceChildren();
            videoUrls.forEach(function(url) {
                var item = box(), video = document.createElement('video'), state = document.createElement('div');
                video.controls = true; video.preload = 'metadata';
                video.className = 'w-100 rounded mb-1 rd-video';
                video.dataset.sourceUrl = url; video.dataset.proxySrc = proxyMediaUrl(url);
                video.textContent = '브라우저가 동영상 재생을 지원하지 않습니다.';
                state.className = 'text-muted small mb-2'; state.setAttribute('data-proxy-video-status', '');
                state.textContent = '동영상 준비 중...';
                item.append(video, state, downloadLink(url, '다운로드', 'btn btn-sm btn-outline-secondary'));
                videoList.appendChild(item);
            });
            videoDiv.style.display = videoUrls.length ? '' : 'none';
            if (videoUrls.length) {
                videoDiv.setAttribute('data-proxy-video-scope', 'detail');
                prepareProxyVideos(videoDiv);
            }

            var filesDiv = document.getElementById('rdFiles');
            var fileList = document.getElementById('rdFileList');
            fileList.replaceChildren();
            otherFiles.forEach(function(url, i) {
                var item = box('mb-2 border rounded p-2 bg-light d-flex align-items-center gap-2');
                var icon = document.createElement('i'), label = document.createElement('span');
                icon.className = 'fas fa-file text-muted'; label.className = 'text-truncate flex-grow-1 small';
                label.textContent = fileLabel(url, i + 1);
                item.append(icon, label, downloadLink(url, '열기', 'btn btn-sm btn-outline-primary flex-shrink-0'));
                fileList.appendChild(item);
            });
            filesDiv.style.display = otherFiles.length ? '' : 'none';

            // 안전신문고 버튼
            document.getElementById('rdViewBtn').onclick = function() {
                openSafetyReport(record['ID']);
            };

            getModal().show();
        } catch (error) {
            showDetailRenderFallback(record, error);
        }
    };

    // index.html / watchlist.html 등 Jinja2 렌더 링크 처리
    $(document).on('click', 'a.report-detail-link[data-report]', function(e) {
        e.preventDefault();
        try { window.showReportDetail(JSON.parse($(this).attr('data-report'))); } catch(_) {}
    });
})();
