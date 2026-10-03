/* Show the shell immediately; map and complete statistics have independent requests. */
window.SrStatsLoader = { start: function (data) {
    var disposed = false, mapDisposed = false, abort = new AbortController(), mapAbort = new AbortController();
    window.SrStatsLoader.cancelMap = function () {
        mapDisposed = true; mapAbort.abort(); window.srMapLoading = false;
    };
    var cat = sessionStorage.getItem('stats_cat') || 'traffic';
    if (!['traffic','parking','other'].includes(cat)) cat = 'traffic';
    var qs = new URLSearchParams(location.search);
    qs.set('category', cat);
    var mapURL = '/stats/map/points?' + qs.toString();
    window.srMapLoading = true;
    performance.mark('sr-map-request');
    var mapCard = document.getElementById('statsMapCard');
    document.getElementById('statsMapOpen').href='/stats/map?' + qs.toString();
    var filters=data.filters || {}, listParams={};
    ['law','reportName','location','reportDateStart','reportDateEnd','occurDateStart','occurDateEnd','occurTimeStart','occurTimeEnd'].forEach(function(k){if(filters[k]) listParams[k]=filters[k];});
    var year=data.year || 'all';
    var starts=[year!=='all'?year+'-01-01':'',filters.responseDateStart || ''].sort();
    var ends=[year!=='all'?year+'-12-31':'',filters.responseDateEnd || ''].filter(Boolean).sort();
    if(starts[starts.length-1]) listParams.responseDateStart=starts[starts.length-1];
    if(ends.length) listParams.responseDateEnd=ends[0];
    if(filters.agency) {listParams.agency=filters.agency; if(filters.agencyExact) listParams.agencyExact='true';}
    if(filters.law) listParams.lawExact='true';
    var listReproducible=!filters.excludePolice && !filters.onlyPolice && !/[&,]/.test(filters.agency || '');
    fetch(mapURL, {headers:{Accept:'application/json'}, signal:mapAbort.signal})
      .then(function(r){if(!r.ok) throw Error('HTTP '+r.status); return r.json();})
      .then(function(payload){
        if(disposed || mapDisposed) return;
        performance.mark('sr-map-data');
        var el = document.getElementById('statsMiniMap');
        if (!payload.points.length) {
            document.getElementById('statsMapState').textContent='이 조건에는 공식 좌표가 있는 신고가 없습니다.';
            return;
        }
        el.hidden=false; document.getElementById('statsMapState').hidden=true;
        window.srEarlyMap = SrReportMap.create(el,payload.points,{category:cat,dedupeMode:data.dedupeMode,listParams:listParams,listReproducible:listReproducible,scrollWheelZoom:false,viewportURL:mapURL});
        performance.mark('sr-map-ready');
        document.dispatchEvent(new CustomEvent('sr:earlymap',{detail:window.srEarlyMap}));
        document.getElementById('statsMapMeta').textContent='지도 표시 '+payload.meta.geocoded_reports+'건 / 대상 '+payload.meta.total_reports+'건';
      }).catch(function(e){if(!disposed && !mapDisposed && e.name!=='AbortError') document.getElementById('statsMapState').textContent='지도 요청 실패: '+e.message;}).finally(function(){if(!mapDisposed) window.srMapLoading=false;});
    fetch('/stats/content' + location.search,{headers:{Accept:'text/html','X-Requested-With':'XMLHttpRequest'},signal:abort.signal})
      .then(function(r){if(!r.ok) throw Error('HTTP '+r.status);return r.text();})
      .then(function(html){
        if(disposed) return;
        var parsed=new DOMParser().parseFromString(html,'text/html');
        var next=parsed.querySelector('.sr-stats-page');
        next.querySelector('#statsMapCard').replaceWith(mapCard);
        document.querySelector('.sr-stats-page').replaceWith(next);
        if(window.srEarlyMap) window.srEarlyMap.invalidateSize();
        document.getElementById('statsData').textContent=parsed.getElementById('statsData').textContent;
        var script=document.createElement('script');
        script.src=document.querySelector('script[src*="/ui/stats.js"]').src;
        document.body.appendChild(script);
      }).catch(function(e){
        if(e.name!=='AbortError') {
            var el=document.getElementById('statsLoading');
            el.textContent='상세 통계를 불러오지 못했습니다: '+e.message;
            var retry=document.createElement('button'); retry.className='btn btn-outline-secondary btn-sm'; retry.textContent='다시 시도';
            retry.onclick=function(){location.reload();}; el.appendChild(retry);
        }
      });
    window.addEventListener('pagehide',function(){disposed=true;abort.abort();window.SrStatsLoader.cancelMap();});
} };
