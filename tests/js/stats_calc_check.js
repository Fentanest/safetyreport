// 통계·지도 순수 계산(web/static/ui/stats-calc.js, report-map-calc.js — EO R-16).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const rootDir = path.resolve(__dirname, '../..');
const sandbox = { window: {}, URLSearchParams };
for (const file of ['web/static/ui/stats-calc.js', 'web/static/ui/report-map-calc.js']) {
    vm.runInNewContext(fs.readFileSync(path.join(rootDir, file), 'utf8'), sandbox);
}
const S = sandbox.window.SrStatsCalc;
const M = sandbox.window.SrReportMapCalc;

// 표시 형식
assert.equal(S.pct(1, 3), '33.3%');
assert.equal(S.pct(0, 0), '—', '분모 0 은 계산 불가');
assert.equal(S.pctValue(1, 8), 12.5);
assert.equal(S.pctValue(1, 0), null);
assert.equal(S.esc(`<'">&`), '&lt;&#39;&quot;&gt;&amp;');
assert.equal(S.rowKey({ agency: 'A', agency_key: 'k' }, true), JSON.stringify(['k', undefined]));
assert.equal(S.rowKey({ agency: 'A', person: '김' }, true), JSON.stringify(['src:-:A', '김']));

// 합계 행: 평균은 표본 수로 가중(기관 평균의 단순 평균이 아님), 표본 없는 행은 빠진다
const base = { fine: 0, fineCount: 0, est: 0, estCount: 0, warn: 0, rejects: 0, dispositionUnknown: 0, noPenalty: 0, unclassified: 0 };
const summary = S.summarizeRows([
    { ...base, total: 10, fines: 2, avgD: 2.0, avgN: 8, rAvg: 4.0, rCount: 2 },
    { ...base, total: 5, fines: 1, avgD: 10.0, avgN: 2, rAvg: NaN, rCount: 0 },
    { ...base, total: 1, fines: 0, avgD: NaN, avgN: 0, rAvg: 1.0, rCount: 1 },
    undefined,
]);
assert.equal(summary.sums.total, 16);
assert.equal(summary.sums.fines, 3);
assert.equal((summary.wDays / summary.wBase).toFixed(1), '3.6', '(2×8 + 10×2) / 10');
assert.equal((summary.wRating / summary.wRatingBase).toFixed(2), '3.00', '(4×2 + 1×1) / 3');

// CSV: 수식 방지·따옴표, 확정/추정 금액 별도 열, 비율은 총 건수 기준
assert.equal(S.csvCell('=SUM(A1)'), "'=SUM(A1)");
assert.equal(S.csvCell('a,"b"'), '"a,""b"""');
assert.equal(S.csvCell(null), '');
assert.equal(S.csvCell(0), '0');
const DISP = [{ key: 'fines', label: '과태료' }, { key: 'rejects', label: '불수용/기타' }];
const header = S.csvHeader(true, DISP);
assert.deepEqual([...header.slice(0, 3)], ['처리기관', '담당자', '총 건수']);
assert.ok(header.includes('확정 과태료(원)') && header.includes('추정 과태료(원)'));
assert.deepEqual([...header.slice(-7)], ['과태료 건수', '과태료 비율(%)', '불수용/기타 건수', '불수용/기타 비율(%)', '별점 평균', '평가 수', '기관 집계 키']);
const row = S.csvRow({ agency: 'A서', person: '김', total: 8, avg_days: 3.25, avg_days_count: 4, total_fine_amount: 80000,
    fines: 3, fine_amount_unknown: 1, estimated_fine_amount: null, estimated_fine_count: null, rejects: 2, avg_rating: null,
    rating_count: 0 }, true, DISP);
assert.equal(row.length, header.length);
assert.deepEqual([...row], ['A서', '김', 8, 3.25, 4, 80000, 2, 1, null, null, 3, 37.5, 2, 25, null, 0, 'src:-:A서']);
const text = S.csvText(header, [row]);
assert.ok(text.startsWith('﻿처리기관,담당자'), 'BOM + 머리말');
assert.ok(text.endsWith('\r\n'));

// 드릴다운 주소: 연도는 답변일 범위로, 더 좁은 범위가 이긴다. 지도는 통계 조건 이름 그대로 + 대표건 모드 이어받기
const ctx = { year: '2026', filters: { law: '도로교통법 제32조', responseDateStart: '2026-03-01', excludePolice: true, agency: 'A&B', agencyExact: true },
              dedupeMode: 'canonical', search: '?dedupe=raw&year=2026' };
assert.equal(S.kpiListUrl('traffic', { status: '완료' }, ctx),
    '/data/traffic?dedupe=canonical&excludePolice=true&law=%EB%8F%84%EB%A1%9C%EA%B5%90%ED%86%B5%EB%B2%95+%EC%A0%9C32%EC%A1%B0&lawExact=true&responseDateStart=2026-03-01&responseDateEnd=2026-12-31&agency=A%26B&agencyExact=true&status=%EC%99%84%EB%A3%8C');
const mp = S.mapParams('parking', { agency: 'A', agencyKey: 'k', person: '김' }, ctx);
assert.equal(mp.get('category'), 'parking');
assert.equal(mp.get('year'), '2026');
assert.equal(mp.get('excludePolice'), 'true');
assert.equal(mp.get('dedupe'), 'raw');
assert.equal(mp.get('completedOnly'), 'true');
assert.equal(S.lawUrl('?year=2026&law=x', null), '/stats?year=2026');
assert.deepEqual(['제10조', '제2조', '제1조의2'].sort(S.naturalCompare), ['제1조의2', '제2조', '제10조']);

// 지도
assert.equal(M.markerSize(0), 38);
assert.equal(M.clusterSize(1e9), 92);
assert.equal(M.formatPct('x'), '0.0');
assert.equal(M.pointFineRate({ disposition_breakdown: [{ label: ' 과태료 ', pct: 62.5 }] }), 62.5);
assert.equal(M.addressListUrl(' 서울 1 ', { category: 'parking', dedupeMode: 'raw', listParams: { law: 'L', empty: '' } }),
    '/data/parking?law=L&location=%EC%84%9C%EC%9A%B8+1&dedupe=raw');
assert.equal(M.addressListUrl('', { category: 'x' }), '/data/all');
const pts = [{ region: '서울 강남구 역삼동', total: 3, agency_breakdown: [{ name: 'A', count: 2 }, { name: 'B', count: 1 }],
               status_breakdown: [{ label: '수용', count: 3 }] },
             { region: '서울 강남구 삼성동', total: 1, agency_breakdown: [{ name: 'B', count: 1 }], status_breakdown: [{ label: '처리중', count: 1 }] }];
assert.deepEqual(JSON.parse(JSON.stringify(M.summarizeClusterRegions(pts))),
    { title: '서울 강남구 역삼동 외 1곳', addressLines: ['주요 구역', '서울 강남구 역삼동 (3건)', '서울 강남구 삼성동 (1건)'] });
assert.deepEqual(JSON.parse(JSON.stringify(M.aggregateAgencies(pts, 4))),
    [{ name: 'A', count: 2, pct: 50 }, { name: 'B', count: 2, pct: 50 }]);
assert.deepEqual(JSON.parse(JSON.stringify(M.addPercent(M.sumBreakdownCounts(pts, 'status_breakdown', ['수용', '처리중', '취하']), 4))),
    [{ label: '수용', count: 3, pct: 75 }, { label: '처리중', count: 1, pct: 25 }]);
assert.equal(M.summarizeClusterRegions([{ region: '' }]).title, '주소 정보 없음');
// 한 주소만 묶이면 '외 N곳' 없이 그 주소
assert.equal(M.summarizeClusterRegions([{ region: '서울 중구', total: 2 }, { region: '서울 중구', total: 1 }]).title, '서울 중구');
// 서버 묶음 점(cluster)은 '… 외 N곳' region 대신 address(대표 주소)로 요약한다
const srv = [{ region: '서울 강서구 등촌동 101 외 2곳', address: '서울 강서구 등촌동 101', address_count: 3, total: 5, cluster: true },
             { region: '서울 강남구 역삼동', total: 3 }];
assert.deepEqual(JSON.parse(JSON.stringify(M.summarizeClusterRegions(srv))),
    { title: '서울 강서구 등촌동 101 외 3곳', addressLines: ['주요 구역', '서울 강서구 등촌동 101 (5건)', '서울 강남구 역삼동 (3건)'] });
// 서버 묶음 점의 region('… 외 N곳')은 이름으로 섞이지 않는다(대표 주소 + address_count 로 센다)
assert.ok(!M.summarizeClusterRegions(srv).addressLines.join().includes('외 2곳'));
assert.deepEqual(JSON.parse(JSON.stringify(M.summarizeClusterRegions(
    [{ region: '주소 정보 없음', address: '', total: 2, cluster: true }]))),
    { title: '주소 정보 없음', addressLines: [] });
console.log('stats-calc ok');
