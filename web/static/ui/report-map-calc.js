/* 신고 지도 순수 계산(EO R-16: report-map.js create() 안에서 옮겼다). DOM·Leaflet 을 쓰지 않는다.
   마커·클러스터 크기, 과태료 비율, 주소 목록 주소, 묶음의 분포·기관·행정구역 요약. */
(function (root) {
    'use strict';

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

    function pointFineRate(point) {
        if (!point || !Array.isArray(point.disposition_breakdown)) {
            return 0;
        }
        var fineItem = point.disposition_breakdown.find(function (item) {
            return String(item && item.label || '').trim() === '과태료';
        });
        var pct = Number(fineItem && fineItem.pct || 0);
        return Number.isFinite(pct) ? pct : 0;
    }

    // options = { category, dedupeMode, listParams }: 통계에서 넘어온 조건(목록이 읽는 이름만)을 이어 준다.
    function addressListUrl(address, options) {
        var category = options.category || 'all';
        var route = category === 'traffic' || category === 'parking' || category === 'other' ? '/data/' + category : '/data/all';
        var params = new URLSearchParams();
        var listParams = options.listParams || {};
        Object.keys(listParams).forEach(function (key) {
            if (listParams[key]) { params.set(key, listParams[key]); }
        });
        var normalizedAddress = String(address || '').trim();
        if (normalizedAddress) {
            params.set('location', normalizedAddress);
        }
        if (options.dedupeMode) {
            params.set('dedupe', options.dedupeMode);
        }
        var queryString = params.toString();
        return route + (queryString ? '?' + queryString : '');
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
            return { label: label, count: counts[label] || 0 };
        }).filter(function (item) { return item.count > 0; });
    }

    function addPercent(items, total) {
        return items.map(function (item) {
            return { label: item.label, count: item.count, pct: total > 0 ? Number((item.count / total * 100).toFixed(1)) : 0 };
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
            return { name: name, count: counts[name], pct: total > 0 ? Number((counts[name] / total * 100).toFixed(1)) : 0 };
        });
    }

    // 서버 묶음 점(point.cluster)의 region 은 '… 외 N곳' 형태라 행정구역처럼 섞으면
    // 이상한 제목이 되므로 address(대표 주소)를 쓴다. 그 밖의 점은 region 그대로.
    function clusterDisplayName(point) {
        if (point && point.cluster) {
            return String(point.address || '').trim();
        }
        return String(point && point.region || '').trim();
    }

    function summarizeClusterRegions(points) {
        var regions = points.map(function (point) {
            return clusterDisplayName(point);
        }).filter(Boolean);
        if (regions.length === 0) {
            return { title: '복수 행정구역', addressLines: ['행정구역 정보 없음'] };
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
            var region = clusterDisplayName(point);
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
        return { title: title || '복수 행정구역', addressLines: addressLines };
    }

    root.SrReportMapCalc = {
        markerSize: markerSize, clusterSize: clusterSize, formatPct: formatPct, pointFineRate: pointFineRate,
        addressListUrl: addressListUrl, sumBreakdownCounts: sumBreakdownCounts, addPercent: addPercent,
        aggregateAgencies: aggregateAgencies, summarizeClusterRegions: summarizeClusterRegions,
    };
})(typeof window === 'undefined' ? globalThis : window);
