/* Show the shell immediately; map and complete statistics have independent requests. */
window.SrStatsLoader = { start: function (data) {
    if (window.SrStatsLoader.dispose) window.SrStatsLoader.dispose(true);
    var removers = [];
    function listen(target, type, callback) {
        target.addEventListener(type, callback);
        removers.push(function () { target.removeEventListener(type, callback); });
    }
    var disposed = false, mapDisposed = false, abort = new AbortController(), mapAbort = null, mapSeq = 0;
    window.SrStatsLoader.cancelMap = function () {
        mapDisposed = true; mapSeq++; if (mapAbort) mapAbort.abort(); window.srMapLoading = false;
    };
    var cat = 'traffic';
    try { cat = sessionStorage.getItem('stats_cat') || cat; } catch (e) { /* 저장 불가여도 동작 */ }
    if (!['traffic','parking','other'].includes(cat)) cat = 'traffic';
    var mapCard = document.getElementById('statsMapCard');
    var filters=data.filters || {}, listParams={};
    ['law','reportName','location','reportDateStart','reportDateEnd','occurDateStart','occurDateEnd','occurTimeStart','occurTimeEnd'].forEach(function(k){if(filters[k]) listParams[k]=filters[k];});
    var year=data.year || 'all';
    var starts=[year!=='all'?year+'-01-01':'',filters.responseDateStart || ''].sort();
    var ends=[year!=='all'?year+'-12-31':'',filters.responseDateEnd || ''].filter(Boolean).sort();
    if(starts[starts.length-1]) listParams.responseDateStart=starts[starts.length-1];
    if(ends.length) listParams.responseDateEnd=ends[0];
    if(filters.agency) {listParams.agency=filters.agency; if(filters.agencyExact) listParams.agencyExact='true';}
    if(filters.law) listParams.lawExact='true';
    var listReproducible=true;
    ['excludePolice','onlyPolice'].forEach(function (key) { if (filters[key]) listParams[key]='true'; });
    function loadMap() {
    if (disposed || mapDisposed) return;
    var seq = ++mapSeq, requestedCat = cat;
    if (mapAbort) mapAbort.abort();
    mapAbort = new AbortController();
    if (window.srEarlyMap) { window.srEarlyMap.map.remove(); window.srEarlyMap = null; }
    var qs = new URLSearchParams(location.search);
    qs.set('category', requestedCat);
    var mapURL = '/stats/map/points?' + qs.toString();
    document.getElementById('statsMapOpen').href='/stats/map?' + qs.toString();
    document.getElementById('statsMiniMap').hidden = true;
    var state = document.getElementById('statsMapState');
    state.hidden = false; state.textContent = '지도를 불러오는 중입니다.';
    document.getElementById('statsMapMeta').textContent = '';
    window.srMapLoading = true;
    performance.mark('sr-map-request');
    fetch(mapURL, {headers:{Accept:'application/json'}, signal:mapAbort.signal})
      .then(function(r){if(!r.ok) throw Error('HTTP '+r.status); return r.json();})
      .then(function(payload){
        if(disposed || mapDisposed || seq !== mapSeq) return;
        performance.mark('sr-map-data');
        var el = document.getElementById('statsMiniMap');
        if (!payload.points.length) {
            document.getElementById('statsMapState').textContent='이 조건에는 공식 좌표가 있는 신고가 없습니다.';
            return;
        }
        el.hidden=false; document.getElementById('statsMapState').hidden=true;
        window.srEarlyMap = SrReportMap.create(el,payload.points,{category:requestedCat,dedupeMode:data.dedupeMode,listParams:listParams,listReproducible:listReproducible,scrollWheelZoom:false,viewportURL:mapURL});
        performance.mark('sr-map-ready');
        document.dispatchEvent(new CustomEvent('sr:earlymap',{detail:window.srEarlyMap}));
        document.getElementById('statsMapMeta').textContent='지도 표시 '+payload.meta.geocoded_reports+'건 / 대상 '+payload.meta.total_reports+'건';
      }).catch(function(e){if(!disposed && !mapDisposed && seq === mapSeq && e.name!=='AbortError') {
          state.textContent='지도 요청 실패: '+e.message;
          var retry=document.createElement('button'); retry.type='button'; retry.className='btn btn-outline-secondary btn-sm'; retry.textContent='다시 시도';
          retry.onclick=loadMap; state.appendChild(retry);
      }}).finally(function(){if(!mapDisposed && seq === mapSeq) window.srMapLoading=false;});
    }
    function renderCategory() {
        document.querySelectorAll('.stats-cat-btn').forEach(function(button) {
            var active = button.dataset.cat === cat;
            button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
        });
    }
    document.querySelectorAll('.stats-cat-btn').forEach(function(button) {
        listen(button, 'click', function() {
            if (button.dataset.cat === cat) return;
            cat=button.dataset.cat;
            try { sessionStorage.setItem('stats_cat',cat); } catch(e) { /* 저장 불가여도 동작 */ }
            window.srPendingStatsCat = cat;
            renderCategory(); loadMap();
        });
    });
    window.srPendingStatsCat = cat;
    document.querySelectorAll('.stats-year-btn').forEach(function(button) {
        listen(button, 'click', function() {
            var params = new URLSearchParams(location.search), year=button.dataset.year;
            if(year==='all') params.delete('year'); else params.set('year',year);
            location.href='/stats'+(params.toString()?'?'+params.toString():'');
        });
    });
    renderCategory(); loadMap();
    fetch('/stats/content' + location.search,{headers:{Accept:'text/html','X-Requested-With':'XMLHttpRequest'},signal:abort.signal})
      .then(function(r){if(!r.ok) throw Error('HTTP '+r.status);return r.text();})
      .then(function(html){
        if(disposed) return;
        var parsed=new DOMParser().parseFromString(html,'text/html');
        var next=parsed.querySelector('.sr-stats-page');
        if (!next) throw Error('상세 통계 응답 형식 오류');
        // 안정된 셸·검색 폼·지도는 보존하고 서버가 생성한 표와 연도만 채운다.
        ['statsTabsContent', 'statsYearGroup'].forEach(function (id) {
            var source = next.querySelector('#' + id), target = document.getElementById(id);
            if (!source || !target) throw Error('상세 통계 응답 형식 오류');
            target.replaceChildren.apply(target, Array.from(source.childNodes));
        });
        document.querySelector('.sr-stats-page').setAttribute('aria-busy', 'false');
        document.getElementById('statsLoading').remove();
        document.getElementById('statsLawToggle').disabled = false;
        document.getElementById('statsLawToggle').removeAttribute('title');
        if(window.srEarlyMap) window.srEarlyMap.invalidateSize();
        document.getElementById('statsData').textContent=parsed.getElementById('statsData').textContent;
        window.SrStats.mount();
      }).catch(function(e){
        if(!disposed && e.name!=='AbortError') {
            var el=document.getElementById('statsLoading');
            el.textContent='상세 통계를 불러오지 못했습니다: '+e.message;
            var retry=document.createElement('button'); retry.className='btn btn-outline-secondary btn-sm'; retry.textContent='다시 시도';
            retry.onclick=function(){location.reload();}; el.appendChild(retry);
        }
      });
    window.SrStatsLoader.dispose = function (removeMap) {
        if (disposed) return;
        disposed=true; abort.abort(); window.SrStatsLoader.cancelMap();
        removers.forEach(function (remove) { remove(); });
        if (removeMap && window.srEarlyMap) { window.srEarlyMap.map.remove(); window.srEarlyMap=null; }
    };
    listen(window, 'pagehide', function () { window.SrStatsLoader.dispose(true); });
} };
