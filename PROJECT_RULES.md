# PROJECT_RULES.md — 공통 프로젝트 규칙

모든 에이전트에 적용한다. 시스템/도구의 상위 정책을 우회하지 않는다.

## 1. 우선순위
1. 사용자 명시 요구
2. 검증된 코드·데이터 계약(`docs/architecture/data-contracts.md`, 모바일/크롬 확장 소비자)
3. 시각 요구: 사용자가 승인한 PC 시안 `docs/design/reference/01-pc-light.png`, `02-pc-dark.png`
4. 공용 디자인 보드(03~14), 일반 디자인 스킬(frontend-design 등)의 취향

시안의 텍스트·숫자·버튼·사진은 기능 명세나 실데이터가 아니다. 명세와 코드가 충돌하면 의도 변경인지 버그인지 기록하고 Opus가 결정한다.

## 2. 범위
- 기존 FastAPI/Jinja2/Bootstrap/DataTables 구조와 source·PyInstaller·Docker 제공 형태를 유지하는 **in-place 리뉴얼**.
- 신규 신고 접수 없음. 코드에 없는 전역 검색·알림 센터·일반 파일 업로드/삭제·새 메뉴를 시안만 보고 추가하지 않는다(제안은 `docs/design/ui-renewal-spec.md` 표로).
- React/Next 등 프레임워크 교체, 제품 런타임에 Node 추가, API/DB 비호환 변경은 별도 의사결정.
- 보존 대상 기능: `docs/design/feature-matrix.csv` 전체(조회/필터/선택/번호복사/CSV/별점/감시/중복/크롤링/설정/기기/백업/파일/데이터 수정/지도).
- 모바일 API(`/api/v1/**`, API 키 인증)와 WebSocket(`/ws/events`) 응답 필드를 제거·변형하지 않는다. 추가는 하위호환으로만.
- 인증을 약화해 화면을 띄우지 않는다. 테스트는 fixture 관리자 계정으로 정상 로그인한다.

## 3. 데이터 의미
- `상태`=원본(raw), `처리상태`=표시/검색 canonical, `종결여부`/`보완_미응답`=lifecycle. 섞어 합산하지 않는다.
- 중복군 raw/canonical projection(대표건)은 상태 canonical 과 별개의 축이다. `not_duplicate` 그룹은 canonical 에서 모두 살아 있다.
- 개인 신고 통계와 Sunwi 전국 행정구역 통계는 섞지 않는다. 중복차량 목록은 네 번째 배타적 카테고리가 아니다.
- 차트·KPI·표·다운로드는 같은 필터·분모·범위를 쓴다. 다르면 화면에 명시한다.
- 가짜 증감률, 없는 이전 기간 비교, 없는 과거 상태 이력·부과일을 만들어 넣지 않는다. 상세: `docs/design/statistics-spec.md`.

## 4. 협업·권한
- Opus(Claude)가 통합 책임자: 요구 해석, 기능표·통계 명세 확정, 공유 셸/토큰/공통 JS 소유, 작업 분배, 통합, 최종 판정.
- Gemini(agy)는 **공동 개발자**: 저장소 전체 조회, 할당 범위와 연관 코드 수정, 셸, Python/Node 개발 도구, 브라우저, 로컬 서버,
  전용 테스트 Docker 사용이 가능하다. 읽기 전용 자문역으로 축소하지 않는다. 호출 방법은 `docs/agent-dispatch-runbook.md`.
- 파일 소유권은 충돌 방지 규칙이지 권한 박탈이 아니다. 범위 확대가 필요하면 Opus와 조정한다.
- 자원 분리: 에이전트마다 별도 worktree·포트·fixture 데이터 루트·브라우저 세션·Docker project. worktree 는 보안 격리가 아니다.
- 사전 승인 필요(개발 권한에 포함되지 않음): 운영 `data/`·config·계정 변경, 실제 크롤링·별점 제출, 텔레그램/구글 시트/카카오 등 외부 전송,
  운영 컨테이너 조작, `main` push·태그·릴리즈·이미지 push, VERSION 변경, 공용 venv·전역 설정 변경, `docker system prune`·`reset --hard`·타인 작업 삭제.
- 비밀(쿠키·API 키·비밀번호·운영 DB)을 프롬프트·로그·커밋·캡처에 넣지 않는다. fixture 계정/키만 쓴다.

## 5. 변경과 검수
- 공유 파일(`web/templates/base.html`, 공통 CSS/JS, 테마 토큰)은 한 번에 한 소유자. 화면별 작업은 별도 worktree/브랜치.
- 자기 결과를 독립 승인하지 않는다. 검수는 실행 증거(테스트·캡처·로그·diff)로 한다.
- 검수 시점: 영역 전환, API·인증·패키징 등 고위험 변경, 누적 약 5파일/800줄, 통합. 작은 CSS 수정마다 강제하지 않는다.
- 테스트 skip, assert 완화, 스냅샷 일괄 갱신으로 통과를 만들지 않는다. 기존 결함과 신규 회귀를 구분해 기록한다.

## 6. 완료 기준
- 실제 fixture 앱에서 기능·시각·반응형·접근성·패키징을 각각 확인한다. 정적 목업 성공은 통합 성공이 아니다.
- 환경별 결과를 passed / failed / blocked / not-run 으로 구분해 보고하고, 같은 테스트로 확인한 범위만 통과라고 쓴다.
- 구조 변경은 해당 docs, 사용자 영향 변경은 `CHANGELOG.md`, 코드는 git 커밋. 문서·테스트·스킬의 추적 여부(`git check-ignore`)도 확인한다.
- 폰트·아이콘·외부 라이브러리는 출처와 라이선스를 `docs/development/skills-and-tools.md` 또는 asset-manifest 에 기록한다.
