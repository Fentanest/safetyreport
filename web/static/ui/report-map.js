/* 신고 지도(마커·클러스터·툴팁·팝업). `/stats/map` 전체 지도와 `/stats` 작은 지도가 함께 쓴다(2026-09-28 분리).
   Leaflet 1.9.4 + markercluster 1.5.3 을 먼저 불러야 한다. 지도 엔진·타일 공급자는 예전 그대로(OpenStreetMap).

   SrReportMap.create(element, points, { category, dedupeMode, listParams, scrollWheelZoom })
     → { map, clusterGroup, invalidateSize() } | null(점이 없을 때) */
window.SrReportMap = (function () {
function create(element, mapPoints, options) {
    options = options || {};
    var selectedCategory = options.category || 'all';
    var dedupeMode = options.dedupeMode || '';
    var listParams = options.listParams || {};
    if (!element || !Array.isArray(mapPoints) || mapPoints.length === 0 || typeof L === 'undefined') {
        return null;
    }

    function markerSize(total) {
        return Math.max(38, Math.min(84, 34 + Math.log(total + 1) * 11));
    }

    function clusterSize(total) {
        return Math.max(46, Math.min(92, 40 + Math.log(total + 1) * 10));
    }

    function formatPct(value) {
        var pct = Number(value || 0);
        return Number.isFinite(pct) ? pct.toFixed(1) : '0.0';
    }

    function getMarkerReportTotal(marker) {
        if (!marker) {
            return 0;
        }
        return Number(marker.reportTotal || (marker.options && marker.options.reportTotal) || 0);
    }

    function getPointFineRate(point) {
        if (!point || !Array.isArray(point.disposition_breakdown)) {
            return 0;
        }
        var fineItem = point.disposition_breakdown.find(function (item) {
            return String(item && item.label || '').trim() === '과태료';
        });
        var pct = Number(fineItem && fineItem.pct || 0);
        return Number.isFinite(pct) ? pct : 0;
    }

    // 과태료 비율 구간 색 = 앱 상태 토큰(60% 이상 수용색, 50% 이상 일부수용색, 그 밖 불수용색). 테마를 따라간다.
    function markerTone(token) {
        return {
            top: 'color-mix(in srgb, ' + token + ' 22%, #ffffff)',
            mid: 'color-mix(in srgb, ' + token + ' 70%, #ffffff)',
            bottom: token,
            shadow: 'color-mix(in srgb, ' + token + ' 30%, transparent)',
            accent: token,
            accentSoft: 'color-mix(in srgb, ' + token + ' 12%, transparent)'
        };
    }

    function getPointMarkerTheme(fineRate) {
        if (fineRate >= 60) {
            return markerTone('var(--sr-status-accept)');
        }
        if (fineRate >= 50) {
            return markerTone('var(--sr-status-partial)');
        }
        return markerTone('var(--sr-status-reject)');
    }

    function buildAddressListUrl(address) {
        var route = '/data/all';
        if (selectedCategory === 'traffic') {
            route = '/data/traffic';
        } else if (selectedCategory === 'parking') {
            route = '/data/parking';
        } else if (selectedCategory === 'other') {
            route = '/data/other';
        }

        var params = new URLSearchParams();
        // 통계에서 넘어온 조건(법규·기관·담당자 등)을 목록에도 이어 준다(목록이 읽는 이름만).
        Object.keys(listParams).forEach(function (key) {
            if (listParams[key]) { params.set(key, listParams[key]); }
        });
        var normalizedAddress = String(address || '').trim();
        if (normalizedAddress) {
            params.set('location', normalizedAddress);
        }
        if (dedupeMode) {
            params.set('dedupe', dedupeMode);
        }
        var queryString = params.toString();
        return route + (queryString ? '?' + queryString : '');
    }

    function createTextElement(tagName, className, text) {
        var element = document.createElement(tagName);
        if (className) {
            element.className = className;
        }
        element.textContent = text === null || text === undefined ? '' : String(text);
        return element;
    }

    function appendMultilineText(parent, text, className) {
        var content = String(text || '');
        var lines = content.split('\n');
        var container = document.createElement('div');
        if (className) {
            container.className = className;
        }
        lines.forEach(function (line, index) {
            if (index > 0) {
                container.appendChild(document.createElement('br'));
            }
            container.appendChild(document.createTextNode(line));
        });
        parent.appendChild(container);
    }

    function buildRows(items) {
        if (!Array.isArray(items) || items.length === 0) {
            return null;
        }
        var fragment = document.createDocumentFragment();
        items.forEach(function (item) {
            var label = String(item.label || '').trim();
            if (!label) {
                return;
            }
            var count = Number(item.count || 0);
            var pct = Number(item.pct || 0);
            var pctRounded = Number(pct.toFixed(1));
            var row = document.createElement('div');
            row.className = 'map-tooltip-row';

            var labelCell = createTextElement('div', 'map-tooltip-label', label);
            var barCell = document.createElement('div');
            barCell.className = 'map-tooltip-bar';
            var fill = document.createElement('div');
            fill.className = 'map-tooltip-fill';
            fill.style.width = Math.min(100, Math.max(0, pctRounded)) + '%';
            barCell.appendChild(fill);
            var valueCell = createTextElement('div', 'map-tooltip-value', count + '건 · ' + pctRounded.toFixed(1) + '%');

            row.appendChild(labelCell);
            row.appendChild(barCell);
            row.appendChild(valueCell);
            fragment.appendChild(row);
        });
        return fragment.firstChild ? fragment : null;
    }

    function buildAgencySummary(items) {
        if (!Array.isArray(items) || items.length === 0) {
            return null;
        }
        var validItems = items.filter(function (item) {
            return String(item.name || '').trim();
        });
        if (validItems.length === 0) {
            return null;
        }
        var container = document.createElement('div');
        container.className = 'map-tooltip-agencies';
        var title = document.createElement('b');
        title.textContent = '처리기관:';
        container.appendChild(title);
        container.appendChild(document.createElement('br'));

        validItems.forEach(function (item, index) {
            if (index > 0) {
                container.appendChild(document.createElement('br'));
            }
            container.appendChild(document.createTextNode(item.name + ' (' + Number(item.count || 0) + '건)'));
        });
        return container;
    }

    function addTooltipSection(container, title, rows) {
        if (!rows || !rows.firstChild) {
            return;
        }
        var section = document.createElement('div');
        section.className = 'map-tooltip-section';
        section.appendChild(createTextElement('div', 'map-tooltip-section-title', title));
        section.appendChild(rows);
        container.appendChild(section);
    }

    function buildTooltipKey(payload) {
        return JSON.stringify({
            region: String(payload.region || ''),
            addressLines: payload.addressLines || [],
            total: Number(payload.total || 0),
            status: (payload.status_breakdown || []).map(function (item) {
                return [item.label, Number(item.count || 0), Number(item.pct || 0).toFixed(1)].join(':');
            }).join('|'),
            disposition: (payload.disposition_breakdown || []).map(function (item) {
                return [item.label, Number(item.count || 0), Number(item.pct || 0).toFixed(1)].join(':');
            }).join('|'),
            agency: (payload.agency_breakdown || []).map(function (item) {
                return [item.name, Number(item.count || 0), Number(item.pct || 0).toFixed(1)].join(':');
            }).join('|'),
            category: (payload.category_breakdown || []).map(function (item) {
                return [item.label, Number(item.count || 0), Number(item.pct || 0).toFixed(1)].join(':');
            }).join('|'),
        });
    }

    function buildTooltip(point) {
        var statusRows = buildRows(point.status_breakdown || []);
        var dispositionRows = buildRows(point.disposition_breakdown || []);
        var categoryRows = buildRows((point.category_breakdown || []).filter(function (item) { return item.count > 0; }));
        var agencySummary = buildAgencySummary(point.agency_breakdown || []);
        var addressLines = String(point.address || '주소 정보 없음').split('\n');
        var payload = {
            region: String(point.region || '행정구역 미상'),
            addressLines: addressLines,
            total: Number(point.total || 0),
            agency_breakdown: point.agency_breakdown || [],
            status_breakdown: point.status_breakdown || [],
            disposition_breakdown: point.disposition_breakdown || [],
            category_breakdown: (point.category_breakdown || []).filter(function (item) { return item.count > 0; }),
        };

        var root = document.createElement('div');
        root.className = 'map-tooltip-card';
        root.appendChild(createTextElement('div', 'map-tooltip-title', payload.region));
        appendMultilineText(root, payload.addressLines.join('\n'), 'map-tooltip-address');

        var totalContainer = document.createElement('div');
        totalContainer.className = 'map-tooltip-total';
        var icon = document.createElement('i');
        icon.className = 'fas fa-layer-group';
        var totalCount = document.createElement('span');
        totalCount.textContent = payload.total + '건';
        totalContainer.appendChild(icon);
        totalContainer.appendChild(totalCount);
        root.appendChild(totalContainer);

        if (agencySummary) {
            root.appendChild(agencySummary);
        }
        addTooltipSection(root, '처리상태 비중', statusRows);
        addTooltipSection(root, '처분 현황 비중', dispositionRows);
        addTooltipSection(root, '카테고리 비중', categoryRows);

        return {
            node: root,
            cacheKey: buildTooltipKey(payload)
        };
    }

    function summarizePopupAgencies(items) {
        if (!Array.isArray(items) || items.length === 0) {
            return '';
        }
        var validItems = items.filter(function (item) {
            return String(item && item.name || '').trim();
        });
        if (validItems.length === 0) {
            return '';
        }
        var labels = validItems.slice(0, 3).map(function (item) {
            return String(item.name || '').trim() + ' (' + Number(item.count || 0) + '건)';
        });
        if (validItems.length > 3) {
            labels.push('외 ' + (validItems.length - 3) + '곳');
        }
        return labels.join(', ');
    }

    function summarizePopupBreakdown(items) {
        if (!Array.isArray(items) || items.length === 0) {
            return '';
        }
        var labels = items.filter(function (item) {
            return String(item && item.label || '').trim() && Number(item.count || 0) > 0;
        }).map(function (item) {
            return String(item.label || '').trim() + ' ' + Number(item.count || 0) + '건 (' + formatPct(item.pct) + '%)';
        });
        return labels.join(' · ');
    }

    function buildPointPopup(point) {
        var address = String(point.address || '').trim() || String(point.region || '').trim() || '주소 정보 없음';
        var title = String(point.region || '').trim() || address;
        var fineRate = getPointFineRate(point);
        var theme = getPointMarkerTheme(fineRate);

        var root = document.createElement('div');
        root.className = 'map-popup-card';
        root.style.setProperty('--popup-accent', theme.accent);
        root.style.setProperty('--popup-accent-soft', theme.accentSoft);

        root.appendChild(createTextElement('div', 'map-popup-title', title));
        if (address !== title) {
            appendMultilineText(root, address, 'map-popup-address');
        }

        var totalContainer = document.createElement('div');
        totalContainer.className = 'map-popup-total';
        var icon = document.createElement('i');
        icon.className = 'fas fa-location-dot';
        var totalText = document.createElement('span');
        totalText.textContent = '신고 ' + Number(point.total || 0) + '건';
        totalContainer.appendChild(icon);
        totalContainer.appendChild(totalText);
        root.appendChild(totalContainer);

        var agencySummary = summarizePopupAgencies(point.agency_breakdown || []);
        if (agencySummary) {
            var agencyLine = document.createElement('div');
            agencyLine.className = 'map-popup-summary';
            var agencyTitle = document.createElement('b');
            agencyTitle.textContent = '처리기관';
            agencyLine.appendChild(agencyTitle);
            agencyLine.appendChild(document.createTextNode(' ' + agencySummary));
            root.appendChild(agencyLine);
        }

        var dispositionSummary = summarizePopupBreakdown(point.disposition_breakdown || []);
        if (dispositionSummary) {
            var dispositionLine = document.createElement('div');
            dispositionLine.className = 'map-popup-summary';
            var dispositionTitle = document.createElement('b');
            dispositionTitle.textContent = '처분 현황';
            dispositionLine.appendChild(dispositionTitle);
            dispositionLine.appendChild(document.createTextNode(' ' + dispositionSummary));
            root.appendChild(dispositionLine);
        }

        var link = document.createElement('a');
        link.className = 'map-popup-link';
        link.href = buildAddressListUrl(address);
        link.target = '_blank';
        link.rel = 'noopener';
        link.appendChild(createTextElement('i', 'fas fa-list-ul', ''));
        link.appendChild(document.createTextNode('리스트 보기'));
        if (!point.cluster && options.listReproducible !== false) root.appendChild(link);
        else if (!point.cluster) root.appendChild(createTextElement('div','small','현재 기관 조건은 목록에서 재현할 수 없어 링크를 제공하지 않습니다.'));
        else root.appendChild(createTextElement('div','small','확대하면 이 영역의 주소별 신고를 볼 수 있습니다.'));

        return root;
    }

    function sumBreakdownCounts(points, fieldName, labelOrder) {
        var counts = {};
        points.forEach(function (point) {
            (point[fieldName] || []).forEach(function (item) {
                var key = String(item.label || '');
                counts[key] = (counts[key] || 0) + Number(item.count || 0);
            });
        });
        return labelOrder.map(function (label) {
            var count = counts[label] || 0;
            return {
                label: label,
                count: count
            };
        }).filter(function (item) { return item.count > 0; });
    }

    function addPercent(items, total) {
        return items.map(function (item) {
            return {
                label: item.label,
                count: item.count,
                pct: total > 0 ? Number((item.count / total * 100).toFixed(1)) : 0
            };
        });
    }

    function aggregateAgencies(points, total) {
        var counts = {};
        points.forEach(function (point) {
            (point.agency_breakdown || []).forEach(function (item) {
                var key = String(item.name || '').trim();
                if (!key) {
                    return;
                }
                counts[key] = (counts[key] || 0) + Number(item.count || 0);
            });
        });
        return Object.keys(counts).sort(function (a, b) {
            if (counts[b] !== counts[a]) {
                return counts[b] - counts[a];
            }
            return a.localeCompare(b, 'ko');
        }).map(function (name) {
            return {
                name: name,
                count: counts[name],
                pct: total > 0 ? Number((counts[name] / total * 100).toFixed(1)) : 0
            };
        });
    }

    function summarizeClusterRegions(points) {
        var regions = points.map(function (point) {
            return String(point.region || '').trim();
        }).filter(Boolean);
        if (regions.length === 0) {
            return {
                title: '복수 행정구역',
                addressLines: ['행정구역 정보 없음']
            };
        }

        var tokenGroups = regions.map(function (region) {
            return region.split(/\s+/).filter(Boolean);
        });
        var prefix = tokenGroups[0].slice();
        tokenGroups.slice(1).forEach(function (tokens) {
            var next = [];
            for (var i = 0; i < Math.min(prefix.length, tokens.length); i += 1) {
                if (prefix[i] !== tokens[i]) {
                    break;
                }
                next.push(prefix[i]);
            }
            prefix = next;
        });

        var regionCounts = {};
        points.forEach(function (point) {
            var region = String(point.region || '').trim();
            if (!region) {
                return;
            }
            regionCounts[region] = (regionCounts[region] || 0) + Number(point.total || 0);
        });
        var majorRegions = Object.keys(regionCounts).sort(function (a, b) {
            if (regionCounts[b] !== regionCounts[a]) {
                return regionCounts[b] - regionCounts[a];
            }
            return a.localeCompare(b, 'ko');
        });

        var title = prefix.length >= 2 ? prefix.join(' ') : majorRegions[0];
        var addressLines = ['주요 구역'].concat(majorRegions.slice(0, 4).map(function (region) {
            return region + ' (' + regionCounts[region] + '건)';
        }));

        return {
            title: title || '복수 행정구역',
            addressLines: addressLines,
        };
    }

    function buildClusterTooltip(points) {
        var total = points.reduce(function (sum, point) {
            return sum + Number(point.total || 0);
        }, 0);
        var regionSummary = summarizeClusterRegions(points);
        var agencies = aggregateAgencies(points, total);

        return buildTooltip({
            region: regionSummary.title,
            address: regionSummary.addressLines.join('\n'),
            total: total,
            agency_breakdown: agencies,
            status_breakdown: addPercent(
                sumBreakdownCounts(points, 'status_breakdown', ['수용', '일부수용', '불수용', '기타', '답변완료', '보완요청', '처리중', '취하', '이송']),
                total
            ),
            disposition_breakdown: addPercent(
                sumBreakdownCounts(points, 'disposition_breakdown', ['과태료', '경고/범칙금', '불수용/기타', '미확인']),
                total
            ),
            category_breakdown: addPercent(
                sumBreakdownCounts(points, 'category_breakdown', ['교통위반', '주정차위반', '기타위반']),
                total
            )
        });
    }

    var tooltipMeasureElement = null;
    var tooltipMeasureCache = new Map();
    var tooltipMeasureCacheLimit = 120;

    function getTooltipMeasureElement() {
        if (tooltipMeasureElement) {
            return tooltipMeasureElement;
        }
        tooltipMeasureElement = document.createElement('div');
        tooltipMeasureElement.className = 'leaflet-tooltip report-map-tooltip leaflet-tooltip-top';
        tooltipMeasureElement.style.position = 'absolute';
        tooltipMeasureElement.style.visibility = 'hidden';
        tooltipMeasureElement.style.pointerEvents = 'none';
        tooltipMeasureElement.style.left = '-9999px';
        tooltipMeasureElement.style.top = '-9999px';
        tooltipMeasureElement.style.zIndex = '-1';
        document.body.appendChild(tooltipMeasureElement);
        return tooltipMeasureElement;
    }

    function measureTooltipSize(content, cacheKey) {
        if (cacheKey && tooltipMeasureCache.has(cacheKey)) {
            return tooltipMeasureCache.get(cacheKey);
        }
        var element = getTooltipMeasureElement();
        while (element.firstChild) {
            element.removeChild(element.firstChild);
        }
        if (content && content.nodeType === 1) {
            element.appendChild(content.cloneNode(true));
        } else {
            element.textContent = '';
            element.innerHTML = String(content || '');
        }
        var rect = element.getBoundingClientRect();
        var size = {
            width: Math.ceil(rect.width || 0),
            height: Math.ceil(rect.height || 0)
        };
        if (cacheKey) {
            tooltipMeasureCache.set(cacheKey, size);
            while (tooltipMeasureCache.size > tooltipMeasureCacheLimit) {
                tooltipMeasureCache.delete(tooltipMeasureCache.keys().next().value);
            }
        }
        return size;
    }

    function getTooltipPlacement(latlng, content, cacheKey) {
        var point = map.latLngToContainerPoint(latlng);
        var size = map.getSize();
        var tooltipSize = measureTooltipSize(content, cacheKey);
        var viewportMargin = 16;
        var connectorGap = 18;
        var halfWidth = tooltipSize.width / 2;
        var halfHeight = tooltipSize.height / 2;
        var topRoom = point.y - viewportMargin;
        var bottomRoom = size.y - point.y - viewportMargin;
        var leftRoom = point.x - viewportMargin;
        var rightRoom = size.x - point.x - viewportMargin;
        var canCenterHorizontally = point.x >= (halfWidth + viewportMargin)
            && (size.x - point.x) >= (halfWidth + viewportMargin);
        var canCenterVertically = point.y >= (halfHeight + viewportMargin)
            && (size.y - point.y) >= (halfHeight + viewportMargin);
        var canTop = canCenterHorizontally && topRoom >= (tooltipSize.height + connectorGap);
        var canBottom = canCenterHorizontally && bottomRoom >= (tooltipSize.height + connectorGap);
        var canLeft = canCenterVertically && leftRoom >= (tooltipSize.width + connectorGap);
        var canRight = canCenterVertically && rightRoom >= (tooltipSize.width + connectorGap);

        if (canTop || canBottom) {
            if (canTop && (!canBottom || topRoom >= bottomRoom)) {
                return { direction: 'top', offset: [0, -10] };
            }
            return { direction: 'bottom', offset: [0, 10] };
        }
        if (canRight || canLeft) {
            if (canRight && (!canLeft || rightRoom >= leftRoom)) {
                return { direction: 'right', offset: [10, 0] };
            }
            return { direction: 'left', offset: [-10, 0] };
        }

        if (topRoom < (tooltipSize.height + connectorGap) && bottomRoom < (tooltipSize.height + connectorGap)) {
            return rightRoom >= leftRoom
                ? { direction: 'right', offset: [10, 0] }
                : { direction: 'left', offset: [-10, 0] };
        }
        if (leftRoom < (tooltipSize.width + connectorGap) && rightRoom < (tooltipSize.width + connectorGap)) {
            return bottomRoom >= topRoom
                ? { direction: 'bottom', offset: [0, 10] }
                : { direction: 'top', offset: [0, -10] };
        }

        var candidates = [
            { direction: 'top', offset: [0, -10], score: Math.min(topRoom, tooltipSize.height) + (canCenterHorizontally ? 36 : 0) },
            { direction: 'bottom', offset: [0, 10], score: Math.min(bottomRoom, tooltipSize.height) + (canCenterHorizontally ? 36 : 0) },
            { direction: 'left', offset: [-10, 0], score: Math.min(leftRoom, tooltipSize.width) + (canCenterVertically ? 36 : 0) },
            { direction: 'right', offset: [10, 0], score: Math.min(rightRoom, tooltipSize.width) + (canCenterVertically ? 36 : 0) }
        ];
        candidates.sort(function (a, b) {
            return b.score - a.score;
        });
        return { direction: candidates[0].direction, offset: candidates[0].offset };
    }

    function showAdaptiveTooltip(layer, content) {
        var tooltipNode = content && content.node ? content.node : content;
        var cacheKey = content && content.cacheKey ? content.cacheKey : '';
        var placement = getTooltipPlacement(layer.getLatLng(), tooltipNode, cacheKey);
        layer.unbindTooltip();
        layer.bindTooltip(tooltipNode, {
            direction: placement.direction,
            offset: placement.offset,
            sticky: false,
            opacity: 1,
            className: 'report-map-tooltip'
        });
        layer.openTooltip();
    }

    function createPointIcon(total, fineRate) {
        var size = markerSize(total);
        var theme = getPointMarkerTheme(fineRate);
        return L.divIcon({
            className: '',
            html:
                '<div class="report-map-point" style="' +
                '--marker-size:' + size + 'px;' +
                '--marker-top:' + theme.top + ';' +
                '--marker-mid:' + theme.mid + ';' +
                '--marker-bottom:' + theme.bottom + ';' +
                '--marker-shadow:' + theme.shadow + ';' +
                '"><span>' + total + '</span></div>',
            iconSize: [size, size],
            iconAnchor: [size / 2, size / 2],
            popupAnchor: [0, -size / 2],
            tooltipAnchor: [0, -size / 2]
        });
    }

    function createPointPopup(point) {
        return L.popup({
            className: 'report-map-popup',
            maxWidth: 320,
            minWidth: 220,
            autoPanPadding: [28, 28]
        }).setContent(buildPointPopup(point));
    }

    function showPointPopup(marker, popup) {
        popup.setLatLng(marker.getLatLng());
        popup.openOn(map);
    }

    var map = L.map(element, {
        zoomControl: true,
        scrollWheelZoom: options.scrollWheelZoom !== false
    }).setView([36.5, 127.8], 7);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);

    var clusterGroup = L.markerClusterGroup({
        showCoverageOnHover: false,
        spiderfyOnMaxZoom: true,
        disableClusteringAtZoom: 12,
        maxClusterRadius: function (zoom) {
            if (zoom >= 11) {
                return 34;
            }
            if (zoom >= 9) {
                return 44;
            }
            return 58;
        },
        iconCreateFunction: function (cluster) {
            var count = cluster.getAllChildMarkers().reduce(function (sum, marker) {
                return sum + getMarkerReportTotal(marker);
            }, 0);
            var size = clusterSize(count);
            return L.divIcon({
                className: '',
                html: '<div class="report-map-cluster" style="--cluster-size:' + size + 'px"><span>' + count + '</span></div>',
                iconSize: [size, size]
            });
        }
    });

    var bounds = [];
    function drawPoints(points) {
    bounds = [];
    clusterGroup.clearLayers();
    points.forEach(function (point) {
        var marker = L.marker([point.lat, point.lng], {
            icon: createPointIcon(point.total, getPointFineRate(point))
        });
        var popup = createPointPopup(point);
        marker.reportTotal = Number(point.total || 0);
        marker.options.reportTotal = Number(point.total || 0);
        marker.pointData = point;
        marker.on('mouseover', function () {
            showAdaptiveTooltip(marker, buildTooltip(point));
        });
        marker.on('mouseout', function () {
            marker.closeTooltip();
        });
        marker.on('click', function () {
            marker.closeTooltip();
            if (point.cluster) { map.setView(marker.getLatLng(), Math.min(19,map.getZoom()+2)); return; }
            showPointPopup(marker, popup);
        });
        clusterGroup.addLayer(marker);
        bounds.push([point.lat, point.lng]);
    });
    }
    drawPoints(mapPoints);

    clusterGroup.on('clustermouseover', function (event) {
        var childPoints = event.layer.getAllChildMarkers().map(function (marker) {
            return marker.pointData;
        }).filter(Boolean);
        showAdaptiveTooltip(event.layer, buildClusterTooltip(childPoints));
    });
    clusterGroup.on('clustermouseout', function (event) {
        event.layer.closeTooltip();
    });

    map.addLayer(clusterGroup);
    if (bounds.length > 0) {
        map.fitBounds(bounds, { padding: [36, 36], maxZoom: 14 });
    }
    if (options.viewportURL) {
        var sequence=0, controller=null, timer=null, disposed=false, notice=null;
        // 이동·확대 뒤 갱신이 실패하면 지도 위에 알리고 다시 시도할 수 있게 한다. 예전에는 속성에만 남아
        // 이전 마커가 현재 범위 결과처럼 보였다(기술일지 C06).
        function showRefreshError(message) {
            if (!notice) {
                notice = document.createElement('div');
                notice.className = 'sr-map-refresh-error';
                notice.setAttribute('role', 'status');
                var text = document.createElement('span');
                var retry = document.createElement('button');
                retry.type = 'button';
                retry.className = 'btn btn-sm btn-light';
                retry.textContent = '다시 시도';
                retry.addEventListener('click', function (e) { e.stopPropagation(); refresh(); });
                L.DomEvent.disableClickPropagation(notice);
                notice.appendChild(text); notice.appendChild(retry);
                element.appendChild(notice);
            }
            notice.firstChild.textContent = '이 범위의 지도를 갱신하지 못했습니다(' + message + '). 이전 결과를 표시 중입니다.';
            notice.hidden = false;
        }
        function clearRefreshError() {
            element.removeAttribute('data-map-error');
            if (notice) notice.hidden = true;
        }
        function refresh() {
            var current=++sequence;
            if(controller) controller.abort();
            controller=new AbortController();
            var url=new URL(options.viewportURL,location.origin), b=map.getBounds();
            url.searchParams.set('bounds',[Math.max(-90,b.getSouth()),Math.max(-180,b.getWest()),Math.min(90,b.getNorth()),Math.min(180,b.getEast())].join(','));
            url.searchParams.set('zoom',map.getZoom());
            fetch(url,{headers:{Accept:'application/json'},signal:controller.signal}).then(function(r){if(!r.ok)throw Error('HTTP '+r.status);return r.json();})
                .then(function(p){if(!disposed && current===sequence){ clearRefreshError(); drawPoints(p.points || []);}})
                .catch(function(e){if(!disposed && current===sequence && e.name!=='AbortError'){ element.setAttribute('data-map-error',e.message); showRefreshError(e.message);}});
        }
        map.on('moveend',function(){clearTimeout(timer);timer=setTimeout(refresh,180);});
        function stopViewportRefresh(){disposed=true;clearTimeout(timer);if(controller)controller.abort();}
        map.on('unload',function(){
            stopViewportRefresh();
            window.removeEventListener('pagehide',stopViewportRefresh);
            if(tooltipMeasureElement)tooltipMeasureElement.remove();
            if(notice)notice.remove();
        });
        window.addEventListener('pagehide',stopViewportRefresh);
    }

    return {
        map: map,
        clusterGroup: clusterGroup,
        invalidateSize: function () { map.invalidateSize(); }
    };
}
return { create: create };
})();
