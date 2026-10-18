# 프록시 신뢰·CSRF 재설계 (2026-10-18)

## 배경
운영 205: 호스트의 cloudflared → Docker 포트 매핑(6819) → 앱. 앱이 보는 socket peer 는 브리지 게이트웨이 `172.19.0.1`.
`trusted_proxies = 10.20.10.3`(예전 값)이라 `X-Forwarded-Proto: https` 를 무시 → `request.url.scheme=http` →
CSRF Origin(`https://safetyreport.fentanest.com`) 불일치 → 관리자 POST 전부 403 `{"detail":"csrf_failed","reason":"origin"}`.
설정 저장도 POST 라 화면에서 고칠 수 없다. 게이트웨이 IP 는 Docker 네트워크를 다시 만들면 바뀐다.

사용자 결정(2026-10-18): 신뢰 프록시가 맞지 않다고 기능이 실패하는 것은 과보호. Docker 에서 값을 매번 바꾸거나 compose 에
서브넷을 고정하는 것도 받아들일 수 없다. 아래 A안 + 저장 즉시 반영 + Tailscale 대역 포함으로 간다.

원칙: **프록시 신뢰 설정은 "접속자 IP·https 판정의 정확도"만 좌우하고, 기능(로그인·관리자 POST·화면 스크립트·리다이렉트)은
설정이 틀려도 동작해야 한다.** CSRF 방어의 핵심(세션 토큰 필수, cross-site 거절, JSON content-type 요구)은 유지한다.

코드 확인 사항: 클라이언트 IP 는 표시용뿐이다(`web/routers/api_route.py` 기기 추적, `web/routers/ws_route.py` 로그). IP 로 보안 판단을
하는 곳은 없다. 다시 확인하고, 있으면 보고한다.

## 변경 1 — CSRF 출처 확인을 프록시와 분리 (`core/utils/csrf.py` `verify_token`)
순서:
1. `Sec-Fetch-Site`(소문자 비교)
   - `cross-site` → `cross_site` 거절(현행 유지).
   - `same-origin` 또는 `none` → 출처 확인 통과(토큰 검사로 진행). 브라우저가 실제 주소창 기준으로 계산하고 JS 가 위조할 수 없는 헤더다.
   - `same-site` → 현행 엄격 비교(복원된 scheme/Host 와 Origin 의 scheme·host·port 정확 일치). 같은 사이트 다른 포트·하위 도메인 페이지 차단 유지.
   - 그 밖의 알 수 없는 값 → `same-site` 와 같이 엄격 비교.
2. `Sec-Fetch-Site` 가 없고 `Origin` 이 있으면(구형 브라우저): Origin 의 host[:port] 를 다음 후보 중 하나와 비교해 같으면 통과.
   scheme 차이는 허용하고, 포트는 각 쪽 scheme 의 기본 포트를 생략한 형태로 정규화한다(`https://x` ≡ Host `x`, `x:443`; `http://x:6819` ≡ Host `x:6819`).
   - 요청의 현재 Host 헤더(프록시 미들웨어가 복원했을 수 있음)
   - 원래 Host 헤더와 원래 `X-Forwarded-Host` 헤더 값 — **peer 신뢰 여부와 무관**. CSRF 공격자는 피해자 브라우저 요청의 Host·X-Forwarded-Host 를
     정할 수 없으므로(사용자 지정 헤더는 preflight) 이 비교는 CSRF 목적상 안전하다. 미들웨어가 원래 값을 scope 에 보존하게 한다.
     `X-Forwarded-Host` 가 쉼표 목록이면 각 항목을 후보로 본다.
   - 불일치 → `origin` 거절. 형식이 잘못된 Origin(`origin_tuple` 이 None, `null` 포함) → `origin` 거절.
3. 둘 다 없으면 출처 확인 생략(현행과 같음).
4. 그다음 세션 토큰 검사(현행 그대로, 항상 필수).
`verify_json_post` 의 content-type 검사 유지. 이 함수를 쓰는 모든 라우터(커뮤니티·원본 보기·재구성·업로드)와 `main.py` 관리자 POST 검사가 함께 바뀐다.
모듈 docstring 갱신.

## 변경 2 — 절대 URL 제거
- `web/templates/data_table.html`(3곳), `web/templates/settings.html`(1곳)의 `url_for('static', ...)` → `/static/...` 상대 경로(`base.html` 과 같은 방식).
  다른 템플릿·Python 에 static 이 아닌 `url_for`/`request.url_for`/`base_url` 절대 URL 생성이 있는지 다시 찾는다.
- 슬래시 리다이렉트: FastAPI/Starlette `redirect_slashes` 가 만드는 307 의 `Location` 은 `request.url` 기반 절대 URL 이라
  신뢰하지 않는 프록시 뒤에서 `http://` 로 내려간다. 앱이 내보내는 3xx 응답의 `Location` 이 이 요청과 같은 host 를 가리키는 절대 URL 이면
  경로+쿼리만 남긴 상대 URL 로 바꾼다(가장 바깥 쪽 순수 ASGI 미들웨어 또는 동등한 방식, 쿼리 보존). 다른 host 를 가리키는 Location 은 건드리지 않는다.
  기존 "구 slash 없는 GET 307 호환·쿼리 보존" 동작(`docs/architecture/web-ui.md` "프록시 뒤 웹 이동·CSRF")은 유지.

## 변경 3 — `trusted_proxies` 기본값 "자동" (`core/utils/proxy_headers.py`)
- 빈 값 = `auto` = 루프백 + 사설·내부 대역:
  `127.0.0.0/8`, `::1/128`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `100.64.0.0/10`(Tailscale/CGNAT), `169.254.0.0/16`, `fc00::/7`, `fe80::/10`.
  공인 IP peer 는 신뢰하지 않는다.
- IPv4-mapped IPv6 peer(`::ffff:172.19.0.1`)는 IPv4 로 풀어 비교한다(XFF 항목도 같게).
- 키워드:
  - `auto`: 위 기본 대역. 다른 항목과 함께 쓸 수 있다(`auto, 203.0.113.5` = 기본 대역 + 그 IP).
  - `none`: 아무 peer 도 신뢰하지 않음(전달 헤더 전부 무시). 다른 항목과 함께 쓰면 설정 저장은 400, 시작 때는 경고 후 `none` 우선.
  - `*`: 현행 유지(모든 peer + 경고).
- 명시 IP/CIDR 목록만 있으면 현행처럼 그 목록만 신뢰(기본 대역을 대체). 해석 불가 항목·`/0` 처리(시작 때 경고·무시, 저장 때 새로 넣으면 400, 기존 값과 같으면 통과)는 현행 유지.
  유효 항목이 하나도 없으면 `auto` 로 떨어진다(현행은 루프백만이었다).
- **실패 대신 무시**: 신뢰 peer 가 보낸 `X-Forwarded-Proto`/`X-Forwarded-Host` 가 복수·쉼표·형식 오류이거나 `X-Forwarded-For` 에 IP 가 아닌 항목이 있어도
  400/WS 1008 로 거절하지 않는다. 그 헤더만 적용하지 않고(scheme/Host/client 원래 값 유지) 진단 경고를 남긴다. 다른 정상 헤더는 적용한다.
  XFF 오른쪽부터 신뢰 경계를 걷는 현행 규칙 유지.
- 진단: 신뢰하지 않는 peer 가 `X-Forwarded-*` 를 보내면 코어 로그에 `[proxy]` 경고를 **peer 별로 한 번만**(프로세스 수명 동안, 개수 상한 둬서 메모리 무한 증가 방지)
  남긴다. 예: `[proxy] 203.0.113.9 가 X-Forwarded-* 를 보냈지만 trusted_proxies 에 없어 무시합니다. 이 주소가 리버스 프록시라면 trusted_proxies 에 추가하세요.`
  비밀값은 로그에 넣지 않는다. 신뢰 peer 의 잘못된 헤더 경고도 같은 방식으로 한 번만.
- 원래 socket peer 는 지금처럼 `scope['safetyreport.connection_peer']` 에 보존. 원래 Host·X-Forwarded-Host 도 보존(변경 1 용).

## 변경 4 — 저장 즉시 반영
- 미들웨어가 시작 때 고정한 설정 대신, 모듈 단위의 교체 가능한 현재 설정(스레드 안전하게 한 번에 바꾸는 불변 객체 참조)을 매 요청 읽는다.
  `create_app()` 이 시작 값으로 초기화, 설정 저장(`services/settings_service.py` 저장 성공 경로)이 새 값으로 교체. 시작 시 `[proxy]` 경고 로그는 유지.
- 설정 화면: "변경 후 서버 재시작 필요" 문구 제거, 설명을 새 동작(빈 값=자동: 같은 PC·Docker·LAN·Tailscale 프록시 자동 신뢰, `none`, 목록은 대체)으로 갱신.
  placeholder 도 갱신(`auto`). 칸 아래에 진단 한 줄: "지금 이 화면 연결: <peer IP> — 신뢰함/신뢰 안 함(전달 헤더 무시)". peer 는 `connection_peer`, 판정은 현재 설정.
  템플릿 컨텍스트에 필요한 값만 넘긴다(`services/settings_service.py`/`web/routers/settings_route.py` 기존 패턴을 따른다).
- 설정 저장 후 응답 리다이렉트도 변경 2 규칙을 따른다(상대 Location).

## 계약·범위
- `/api/v1/**`(API 키), 크롬 확장 엔드포인트, `/ws/events` 응답 필드·인증은 바꾸지 않는다. `config.ini` 키 이름 그대로(`[SETTINGS] trusted_proxies`).
- Uvicorn `proxy_headers=False`/`--no-proxy-headers` 유지.
- 새 의존성 추가 금지. 운영 `data/`·외부 서비스 건드리지 않음. 테스트는 `SAFETYREPORT_DATA_DIR=$(mktemp -d)` 와 fixture 만.

## 테스트 (`tests/`)
기존 `tests/test_proxy_navigation.py`, `tests/test_app_factory.py`, `tests/test_settings_service.py`, 커뮤니티 계열 CSRF 테스트의 기대값 중
**설계 변경으로 바뀌는 것만** 고친다(신뢰하지 않는 프록시 뒤 403 → 통과, 400 → 무시 등). 각 변경을 테스트 이름/주석에 이유와 함께 남긴다.
assert 를 약하게 만들거나 skip 하지 않는다. 최소 다음을 새로 확인:
- CSRF: Sec-Fetch-Site same-origin + scheme 불일치(신뢰하지 않는 peer, Origin https / 요청 http) → 토큰 맞으면 통과, 토큰 틀리면 `csrf`.
  `cross-site` → 거절. `same-site` + 다른 포트 Origin → `origin` 거절. 헤더 없음 + Origin host 가 Host 와 같고 scheme 만 다름 → 통과.
  헤더 없음 + Origin host 가 원래 X-Forwarded-Host 와 같음(신뢰 안 하는 peer, nginx 식 Host=`127.0.0.1:6819`) → 통과. 헤더 없음 + 다른 host → 거절. `Origin: null` → 거절.
- 운영 205 재현: peer `172.19.0.1`, 빈 `trusted_proxies`, `X-Forwarded-Proto: https`, Host `safetyreport.fentanest.com`, Origin `https://safetyreport.fentanest.com`
  → 관리자 POST 통과, `request.url.scheme == 'https'`. peer `10.20.10.3` 만 명시한 경우에도 Sec-Fetch-Site same-origin 이면 통과.
- 기본 대역: `100.64.1.2`·`fd00::1`·`::ffff:172.19.0.1` 신뢰, `8.8.8.8` 비신뢰. `none` 이면 `127.0.0.1` 도 비신뢰. `auto, 203.0.113.5` 조합.
- 신뢰 peer 의 복수 X-Forwarded-Host → 400 이 아니라 무시(Host 원래 값), 다른 헤더는 적용. 비 IP XFF → client 유지.
- 진단 경고 peer 당 한 번.
- 저장 즉시 반영: 설정 저장 후 앱 재생성 없이 다음 요청부터 새 신뢰 적용.
- 상대 경로: data_table/settings 렌더 결과에 `http://`/`https://` static 스크립트 URL 이 없음. `/crawl`(슬래시 없음, 쿼리 포함) GET → 307, Location 이 `/crawl/?…` 상대 경로.
- 설정 저장 검증: `none, 10.0.0.1` 400, `auto` 허용.
전체 회귀: `SAFETYREPORT_DATA_DIR=$(mktemp -d) /home/better0101/projects/safetyreport/.venv/bin/python -m unittest discover -s tests -p "test_*.py"` 전부 통과.

## 문서
- `docs/architecture/web-ui.md` "리버스 프록시 지원"·"프록시 뒤 웹 이동·CSRF" 표와 업그레이드 안내를 새 동작으로 갱신(옛 표는 "2026-10-18 개정" 으로 대체, 이력은 CHANGELOG).
- `docs/architecture/community-account.md` CSRF 문단.
- `CHANGELOG.md` 맨 위(기존 형식을 따른다): 사용자 영향(Docker·실행 파일·다른 PC 프록시에서 설정 없이 동작, 저장 즉시 반영, 205 증상).
