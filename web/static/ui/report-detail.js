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

    // 과태료/범칙금 전용: 확정 원문(value)은 그대로 두고, 추정(법정 최저)이 있으면 흐린 보조 글자로 옆에 덧붙인다
    // (statistics-spec §4-1·§4-2, PROJECT_RULES §3-2). estimateAmount·estimateBasis 는 서버가
    // services/fine_estimate.estimate_if_unknown 으로 이미 계산해 둔 값만 받는다(여기서 판정하지 않는다).
    function fineField(icon, label, value, estimateAmount, estimateBasis, correctedText) {
        var text = normalizeDisplayValue(value);
        var amountNum = Number(estimateAmount);
        var hasEstimate = estimateAmount !== null && estimateAmount !== undefined && estimateAmount !== '' && isFinite(amountNum);
        if (!hasMeaningfulValue(text) && !hasEstimate) return '';
        var originalHtml = correctedText ? ' <small class="text-muted">원문: ' + esc(text.replace(/^과태료[:\s]*/, '')) + '</small>' : '';
        if (correctedText) text = normalizeDisplayValue(correctedText);
        var estimateHtml = '';
        if (hasEstimate) {
            var basisText = normalizeDisplayValue(estimateBasis);
            var tooltip = '법정 최저 기준 추정' + (basisText ? (' · ' + basisText) : '');
            estimateHtml = ' <span class="rd-fine-estimate" title="' + esc(tooltip) + '" aria-label="' + esc(tooltip) + '">추정 ' +
                amountNum.toLocaleString('ko-KR') + '원</span>';
        }
        return '<div class="col-12 col-sm-6"><div class="d-flex align-items-start">' +
            '<i class="' + icon + ' text-muted me-2 rd-field-icon"></i>' +
            '<span class="text-muted me-2 flex-shrink-0 rd-field-label">' + label + '</span>' +
            '<span class="fw-medium rd-field-value">' + esc(text) + originalHtml + estimateHtml + '</span>' +
            '</div></div>';
    }

    /// 신고 카테고리(traffic/parking/other) 데이터 리스트로 이동하면서
    /// queryKey=value 검색 조건을 미리 적용. category 가 없으면 /data/all 로 fallback.
    function buildFilterUrl(category, queryKey, value) {
        var path = '/data/' + (category || 'all');
        // 법규 링크는 저장 키 그대로(lawExact): 키 안의 쉼표·& 를 OR/AND 로 나누지 않는다(통계 막대와 같은 판정).
        return path + '?' + queryKey + '=' + encodeURIComponent(normalizeDisplayValue(value)) +
            (queryKey === 'law' ? '&lawExact=true' : '');
    }

    function linkField(icon, label, value, category, queryKey) {
        var text = normalizeDisplayValue(value);
        if (!hasMeaningfulValue(text)) return '';
        var values = queryKey === 'law' ? window.SrLawValues.read(text) : [text];
        var links = values.map(function(value) {
            var href = buildFilterUrl(category, queryKey, value);
            return '<a class="fw-medium text-decoration-underline rd-field-value" href="' + href + '">' + esc(value) +
                ' <i class="fas fa-chevron-right ms-1 rd-field-chevron"></i></a>';
        }).join('<br>');
        return '<div class="col-12 col-sm-6"><div class="d-flex align-items-start">' +
            '<i class="' + icon + ' text-muted me-2 rd-field-icon"></i>' +
            '<span class="text-muted me-2 flex-shrink-0 rd-field-label">' + label + '</span>' +
            '<span>' + links + '</span></div></div>';
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
        var fallbackRawBtn = document.getElementById('rdRawBtn');
        if (fallbackRawBtn) {
            fallbackRawBtn.dataset.reportId = normalizeDisplayValue(record['ID']);
            fallbackRawBtn.onclick = function() {
                if (window.SrRawDetail) window.SrRawDetail.open(fallbackRawBtn.dataset.reportId);
            };
        }
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
                fineField('fas fa-coins',        '과태료/범칙금', record['범칙금_과태료'],
                          record['fine_estimate_amount'], record['fine_estimate_basis'], record['fine_corrected_text']) +
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

            // 원본 응답 버튼(2026-10-17 명세 4절): ID 만 넘기고 내용은 모달이 직접 읽는다
            var rawBtn = document.getElementById('rdRawBtn');
            if (rawBtn) {
                rawBtn.dataset.reportId = normalizeDisplayValue(record['ID']);
                rawBtn.onclick = function() {
                    if (window.SrRawDetail) window.SrRawDetail.open(rawBtn.dataset.reportId);
                };
            }

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

/* 상세 원본 보기(2026-10-17 명세 4절): 신고 상세 모달의 "원본 응답" 버튼 → 별도 모달.
   내용은 textContent/텍스트 노드로만 넣어 XSS 를 막고, 검색 일치는 <mark> 로 감싼다.
   전역 접점: SrRawDetail.open(reportId). */
(function() {
    var _rawModal = null;
    var state = { id: '', pretty: '', matchCount: 0, matchIndex: -1, returnToDetail: false };

    function el(id) { return document.getElementById(id); }

    function getRawModal() {
        if (!_rawModal) {
            _rawModal = new bootstrap.Modal(el('rawDetailModal'));
        }
        return _rawModal;
    }

    function setNotice(text, isError) {
        var notice = el('rawDetailNotice');
        if (!text) {
            notice.hidden = true;
            notice.textContent = '';
            return;
        }
        notice.hidden = false;
        notice.textContent = text;
        notice.classList.toggle('alert-danger', !!isError);
        notice.classList.toggle('alert-info', !isError);
    }

    function updateCount() {
        var count = el('rawDetailCount');
        var query = el('rawDetailSearch').value;
        if (!query) {
            count.textContent = state.matchCount ? ('총 ' + state.matchCount + '건') : '';
        } else if (!state.matchCount) {
            count.textContent = '0건';
        } else {
            count.textContent = (state.matchIndex + 1) + '/' + state.matchCount + '건';
        }
    }

    function markCurrent() {
        var code = el('rawDetailCode');
        var marks = code.querySelectorAll('mark');
        for (var i = 0; i < marks.length; i++) {
            marks[i].classList.toggle('rd-raw-current', i === state.matchIndex);
        }
        var current = marks[state.matchIndex];
        if (current && current.scrollIntoView) {
            try { current.scrollIntoView({ block: 'nearest' }); } catch (_) {}
        }
        updateCount();
    }

    // JSON 텍스트를 텍스트 노드로만 넣어 렌더한다. 검색어가 있으면 일치 구간을 <mark> 로 감싼다(대소문자 무시).
    function render() {
        var code = el('rawDetailCode');
        code.replaceChildren();
        state.matchCount = 0;
        state.matchIndex = -1;
        var query = el('rawDetailSearch').value;
        if (!query) {
            code.textContent = state.pretty;
            updateCount();
            return;
        }
        var lower = state.pretty.toLowerCase();
        var needle = query.toLowerCase();
        var positions = [];
        var from = 0;
        while (true) {
            var at = lower.indexOf(needle, from);
            if (at < 0) break;
            positions.push(at);
            from = at + needle.length;
            if (positions.length > 2000) break;
        }
        state.matchCount = positions.length;
        if (!positions.length) {
            code.textContent = state.pretty;
            updateCount();
            return;
        }
        var cursor = 0;
        positions.forEach(function(at) {
            if (at > cursor) {
                code.appendChild(document.createTextNode(state.pretty.slice(cursor, at)));
            }
            var mark = document.createElement('mark');
            mark.textContent = state.pretty.slice(at, at + needle.length);
            code.appendChild(mark);
            cursor = at + needle.length;
        });
        if (cursor < state.pretty.length) {
            code.appendChild(document.createTextNode(state.pretty.slice(cursor)));
        }
        state.matchIndex = 0;
        markCurrent();
    }

    function stepMatch(delta) {
        if (!state.matchCount) return;
        state.matchIndex = (state.matchIndex + delta + state.matchCount) % state.matchCount;
        markCurrent();
    }

    function showContent(payload) {
        state.pretty = JSON.stringify(payload.json, null, 2);
        state.matchCount = 0;
        state.matchIndex = -1;
        el('rawDetailSearch').value = '';
        var meta = el('rawDetailMeta');
        meta.textContent = 'ID ' + payload.id + ' · ' + payload.fetched_at + ' · ' + payload.source +
            ' · sha256 ' + String(payload.raw_sha256).slice(0, 12) + '…';
        el('rawDetailDownloadLabel').textContent = payload.id + '.json 내려받기';
        el('rawDetailRewrite').hidden = false;
        el('rawDetailPre').hidden = false;
        setNotice('');
        render();
    }

    function showState(message, isError, withContent) {
        setNotice(message, isError);
        el('rawDetailPre').hidden = !withContent;
        if (!withContent) {
            state.pretty = '';
            state.matchCount = 0;
            state.matchIndex = -1;
            el('rawDetailCode').replaceChildren();
            el('rawDetailMeta').textContent = '';
            updateCount();
        }
    }

    function open(reportId) {
        var id = String(reportId == null ? '' : reportId).trim();
        state.id = id;
        el('rawDetailTitle').textContent = id ? ('원본 응답 ' + id) : '원본 응답';
        el('rawDetailSearch').value = '';
        el('rawDetailRewrite').hidden = true;
        el('rawDetailDownloadLabel').textContent = '다운로드';
        // 두 Bootstrap 모달을 겹치면 먼저 열린 상세 모달의 포커스 트랩이 새 모달의 포커스를
        // 계속 빼앗는다(Firefox에서 입력 불가 확인). 원본을 보는 동안 상세 모달은 숨기고,
        // 원본 모달이 닫히면 되돌린다.
        var detailEl = el('reportDetailModal');
        var detailInstance = (window.bootstrap && bootstrap.Modal.getInstance)
            ? bootstrap.Modal.getInstance(detailEl) : null;
        state.returnToDetail = !!(detailEl && detailEl.classList.contains('show') && detailInstance);
        if (state.returnToDetail) {
            // 상세 모달 hide() 직후 원본을 바로 show() 하면 상세 hidden 전환이 나중에 끝나
            // Bootstrap 이 body 의 modal-open·스크롤 잠금을 풀어 배경이 스크롤된다.
            // hidden 이 끝난 뒤에 원본을 열어 잠금을 유지한다(복귀 흐름·동영상 처리는 그대로).
            var rawModal = getRawModal();
            var rawShown = false;
            var showRawOnce = function() {
                if (rawShown) return;
                rawShown = true;
                rawModal.show();
            };
            detailEl.addEventListener('hidden.bs.modal', showRawOnce, { once: true });
            detailInstance.hide();
            // hidden 이 어떤 이유로든 안 오면(예외적) 원본을 못 여는 것보다 겹침이 낫다.
            window.setTimeout(showRawOnce, 800);
        } else {
            getRawModal().show();
        }
        if (!id) {
            showState('신고 ID 가 없어 원본을 불러올 수 없습니다.', true, false);
            return;
        }
        showState('불러오는 중…', false, false);
        fetch('/reports/' + encodeURIComponent(id) + '/raw-detail', {
            headers: { 'Accept': 'application/json' },
            cache: 'no-store'
        }).then(function(response) {
            return response.json().then(function(body) {
                return { status: response.status, body: body };
            });
        }).then(function(result) {
            if (state.id !== id) return;
            if (result.status === 404) {
                showState('아직 원본이 없습니다(DB 최적화가 채웁니다).', false, false);
            } else if (result.status === 409) {
                showState('원본이 손상되었습니다(sha 불일치). DB 최적화가 다시 채울 때까지 기다려 주세요.', true, false);
            } else if (result.status !== 200) {
                showState('원본을 불러오지 못했습니다(오류 ' + result.status + ').', true, false);
            } else {
                showContent(result.body);
            }
        }).catch(function() {
            if (state.id !== id) return;
            showState('원본을 불러오지 못했습니다(네트워크 오류).', true, false);
        });
    }

    function copyPretty() {
        if (!state.pretty) return;
        function done(button, label) {
            var original = button.innerHTML;
            button.innerHTML = label;
            window.setTimeout(function() { button.innerHTML = original; }, 1500);
        }
        var button = el('rawDetailCopy');
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(state.pretty).then(function() {
                done(button, '<i class="fas fa-check me-1"></i>복사됨');
            }, function() {
                setNotice('복사에 실패했습니다.', true);
            });
            return;
        }
        try {
            var area = document.createElement('textarea');
            area.value = state.pretty;
            document.body.appendChild(area);
            area.select();
            document.execCommand('copy');
            document.body.removeChild(area);
            done(button, '<i class="fas fa-check me-1"></i>복사됨');
        } catch (_) {
            setNotice('복사에 실패했습니다.', true);
        }
    }

    function downloadPretty() {
        if (!state.pretty || !state.id) return;
        var blob = new Blob([state.pretty], { type: 'application/json;charset=utf-8' });
        var url = window.URL.createObjectURL(blob);
        var link = document.createElement('a');
        link.href = url;
        link.download = state.id + '.json';
        document.body.appendChild(link);
        link.click();
        window.setTimeout(function() {
            if (link.parentNode) link.parentNode.removeChild(link);
            window.URL.revokeObjectURL(url);
        }, 0);
    }

    function rewriteFile() {
        if (!state.id) return;
        var button = el('rawDetailRewrite');
        button.disabled = true;
        // session-requests.js 가 같은 출처 JSON POST 에 X-CSRF-Token 을 자동으로 붙인다
        fetch('/reports/' + encodeURIComponent(state.id) + '/raw-detail/rewrite', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: '{}'
        }).then(function(response) {
            return response.json().then(function(body) {
                return { status: response.status, body: body };
            });
        }).then(function(result) {
            if (result.status === 200) {
                setNotice('파일로 다시 썼습니다(' + (result.body.filename || (state.id + '.json')) + ').', false);
                el('rawDetailPre').hidden = false;
            } else if (result.status === 404) {
                setNotice('아직 원본이 없습니다(DB 최적화가 채웁니다).', true);
            } else if (result.status === 409) {
                setNotice('원본이 손상되었습니다(sha 불일치).', true);
            } else {
                setNotice('파일로 다시 쓰지 못했습니다(오류 ' + result.status + ').', true);
            }
        }).catch(function() {
            setNotice('파일로 다시 쓰지 못했습니다(네트워크 오류).', true);
        }).finally(function() {
            button.disabled = false;
        });
    }

    function bind() {
        el('rawDetailSearch').addEventListener('input', render);
        el('rawDetailSearch').addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                stepMatch(e.shiftKey ? -1 : 1);
            }
        });
        el('rawDetailPrev').addEventListener('click', function() { stepMatch(-1); });
        el('rawDetailNext').addEventListener('click', function() { stepMatch(1); });
        el('rawDetailCopy').addEventListener('click', copyPretty);
        el('rawDetailDownload').addEventListener('click', downloadPretty);
        el('rawDetailRewrite').addEventListener('click', rewriteFile);
        el('rawDetailModal').addEventListener('shown.bs.modal', function() {
            el('rawDetailSearch').focus();
        });
        el('rawDetailModal').addEventListener('hidden.bs.modal', function() {
            if (!state.returnToDetail) return;
            state.returnToDetail = false;
            var detailEl = el('reportDetailModal');
            var detailInstance = (window.bootstrap && bootstrap.Modal.getInstance)
                ? bootstrap.Modal.getInstance(detailEl) : null;
            if (!detailInstance) return;
            detailInstance.show();
            // 상세 모달을 숨기면 hidden 리스너(resetProxyVideos)가 proxy 동영상 src 를 끊는다.
            // 되돌아올 때 같은 스코프 규칙으로 다시 준비한다(동영상 없는 신고는 스코프가 없어 건너뜀).
            var scope = detailEl.querySelector('[data-proxy-video-scope]');
            if (scope && window.prepareProxyVideos) window.prepareProxyVideos(scope);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }

    window.SrRawDetail = { open: open, render: render };
})();
