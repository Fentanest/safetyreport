# Self-host protocol 3 (server canonical contract)

This contract is independent of `/api/v1`, product versions, SQLite schema versions,
and the community ingest protocol. Server product major must be >=3. Mobile product
`2.0.0+31` and extension product `1.0.0` are allowed if they implement protocol 3.
Product versions: optional `v`, three or four numeric components, optional
`-dev`, `-alpha`, `-beta`, `-rc` suffix with identifiers, optional `+build`.
No lexicographic or first-character comparison. Protocol is exactly the string `3`.

## Probe (authenticated, exempt from compatibility and community gate)

`GET /api/v1/server/version`, with a valid `X-API-Key`. Existing top-level fields
are retained; this is the actual JSON shape:

```json
{"status":"success","version":"3.0.0.0-dev","latest_version":null,"up_to_date":true,
 "protocol_version":3,"supported_client_protocols":[3],"minimum_server_major":3}
```

Clients must verify BOTH server product major >=3 and advertised protocol support.
Probe success does not grant access to reports. The server checks every request.

## HTTP (all external paths, including onboarding, media, query-key downloads)

```http
X-API-Key: <existing authentication key>
X-SafetyReport-Client: mobile
X-SafetyReport-Version: 2.0.0+31
X-SafetyReport-Protocol: 3
```

Client type is `mobile` or `chromeextension`. The version is the real client
product version; do not manufacture a major 3 version. Query-key downloads retain
`api_key` but still require all three compatibility headers. Existing permission,
consent, CSRF and account checks remain independent. Admin web paths use verified
sessions; admin cookies do not exempt `/api/v1/**`. Health and OPTIONS are separate.

Invalid authentication -> 401. Valid authentication with missing protocol or
protocol 1/2, missing/invalid client identity -> HTTP 409 `CLIENT_UPGRADE_REQUIRED`.
Unknown/malformed protocol -> HTTP 409 `CLIENT_PROTOCOL_UNSUPPORTED`.
An invalid/old server version -> HTTP 409 `SERVER_UPGRADE_REQUIRED`.

```json
{"status":"error","code":"CLIENT_UPGRADE_REQUIRED",
 "detail":"self-host 통신 계약 v3을 지원하는 클라이언트로 업데이트하세요.",
 "message":"self-host 통신 계약 v3을 지원하는 클라이언트로 업데이트하세요.",
 "compatibility":{"protocol_version":3,"supported_client_protocols":[3],"minimum_server_major":3}}
```

Refusals are `Cache-Control: no-store`. Headers identify capability, never authenticate.
No legacy bypass setting exists. Persisted pre-upgrade keys receive the same checks.

## WebSocket

`/ws/events?api_key=<existing>&client_type=mobile&client_version=2.0.0%2B31&client_protocol=3`

Same fields apply to `/crawl/ws/logs` and `/rating/ws/rating_logs`. URL-encode the
product version. There are no new secret query fields. Invalid authentication
remains 4001; compatibility refusal accepts then closes with 4406 and reason equal
to the HTTP code; community refusal remains 4403. No connected notification, log
or broadcast is sent before compatibility succeeds. Every reconnect revalidates.
Verified admin session cookies remain valid for log WS only, not event WS.

## Added aggregate and pagination contracts

`GET /api/v1/stats/overview` retains all existing fields and adds
`result_distribution:{accept,partial,reject,unknown}` and
`violation_laws:[{name,filter,count}]` in each category and `all`.
Unknown result means completed `답변완료`; processing is separately counted.
Result and disposition are separate axes; partial+fine contributes to both.
Law uses the exact stored normalized law combination, one report per combination.
Missing law: `name:""`, `filter:"__없음__"`; no title-based inference.
Population, answer-year, raw/canonical projection and amount separation are unchanged.

`GET /api/v1/reports/{traffic|parking|other}/page?offset=0&limit=200&dedupe=canonical`
returns `{status:"success",category,total,offset,limit,count,next_offset,dedupe_mode,data:[...]}`.
`limit` is 1–1000; `offset` >=0; order is ID ascending. `next_offset` is null at the end.
`total` is the full filtered/projected population, not the page length. Record fields,
NULL/integer semantics and duplicate metadata match the full API. SQL count/page use
one read transaction. Paging is not a multi-request snapshot: restart after dataset
restore or concurrent collection if a stable full export is needed (DB download is consistent).

`GET /api/v1/stats/map/points?category=traffic&year=all&dedupe=canonical&max_points=1200&zoom=7`
optionally accepts `law` (exact stored combination) and `bounds=south,west,north,east`.
Coordinates must be finite and ordered within latitude ±90/longitude ±180;
antimeridian-crossing bounds are not supported. `max_points` 1–1200, zoom 0–19.
Returns `{status:"success",data:{points:[...],meta:{...}}}` with all existing full-map
point/meta fields. Meta `total_reports`, `geocoded_reports`, `missing_reports`,
`address_groups`, `agency_count` cover the entire filtered population; added
`viewport_reports`, `rendered_points`, `point_budget`, `clustered` describe rendering.
Spatial-cell points have `cluster:true`, weighted centroid lat/lng, total and exact
status/disposition/agency/category counts; zoom into the cell instead of treating
its centroid/label as an original address. Non-cell points retain original coordinates.

Law drilldown uses `/data/{category}?law=<filter>&lawExact=true&dedupe=<mode>` so
one law does not accidentally include longer/multiple-law combinations. Unsupported
police/complex-agency filters disable the link instead of misrepresenting counts.

Existing full report/map APIs never silently truncate. PC map points use at most
1200 spatial cells, with full-population metadata and viewport counts; cluster
centroids are display-only and never alter stored coordinates.

Shared vectors: `vectors.json`; statistics: `../stats-overview-vectors.json`.
Consumers must copy these canonical files in their own authorized work sessions.
