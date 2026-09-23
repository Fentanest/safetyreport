# 크롤링·파싱·처리 규칙

다루는 것: start.py 파이프라인, 파서 규칙, 카테고리 분류, 첨부 URL, 별점 API, 감시목록, 디버그 extractor.

2026-09-24 에 기존 루트 `CLAUDE.md`(725줄)에서 옮겼다. 아래 '이관 원문' 은 문구를 바꾸지 않고 옮긴 것이며, 원본 전체는 [legacy-claude-reference.md](legacy-claude-reference.md)에 있다.

## 코드 대조 정정 (기준 17df6cb)

| 원문 절 | 현재 코드 | 조치 |
|---|---|---|
| 전체 | 이번 이관에서는 크롤러 경로를 실행하지 않았다(실계정·외부 요청 금지). 코드 대조는 문서 절과 심볼 존재 수준만 확인했다. | 미검증 표시 |

## 이관 원문

<!-- legacy CLAUDE.md 372-412 -->
## 크롤링 파이프라인 (start.py)

```
main()
  → 로그인 전략 결정
      → nonmember      → legacy 강제 + Selenium 수동 로그인 대기
      → crawl_type=api → direct_login 시도
          → 성공       → driver 없이 API 호출
          → 실패       → Selenium 로그인 후 브라우저 API fallback
      → crawl_type=legacy → Selenium 회원 로그인 강제
  → _run_crawling_process()
      → API 경로
          → crawltitle_api.crawl_titles(browser_fallback 여부 반영)
          → crawldetail_api.crawl_details(browser_fallback 여부 반영)
      → 레거시 경로
          → crawltitle.crawl_titles()
          → crawldetail.crawl_details()
      → database.title_to_sql()
      → (큐 모드) extract_ids_from_queue()
          → missing_rnums 있으면 최대 100페이지 단건 크롤링으로 탐색
          → 발견 즉시 detaillist 추가, 모두 찾으면 조기 종료
      → database.detail_to_sql()   # 기존 deatil_to_sql alias 유지
  → _process_and_save_results()
      → database.merge_final()
      → crawl_state_store.save_crawl_done()    # crawl_done.json 저장 → 모바일 폴링용
      → crawl_state_store.save_crawl_changes() # 변경 목록 저장
      → export_service.export_results()        # auto_export_* 설정 반영
```

- `start.py`는 FastAPI와 **별도 서브프로세스**로 실행 → ws_manager 싱글톤에 직접 접근 불가
- 크롤링 완료 후 로그 회전/완료 마커 기록/WS 브로드캐스트는 `crawl_manager.run_after_crawl()` 경로로 수렴
- `login.py`는 Selenium 회원 로그인 담당, `direct_login.py`는 API용 직접 로그인 담당으로 역할을 분리한다.
- 최근 3일 답변 목록은 `답변일` 필터 후 `synced_at DESC`, 동순위 `신고번호 DESC` 로 정렬한다.
  `synced_at` 가 없는 과거 데이터는 `답변일 DESC`, `신고번호 DESC` fallback 이 필요하다.
- 모바일 Client용 `crawl_changes` 일반 신고 payload도 `synced_at` 을 포함해야 한다.
  서버가 내부적으로 category별 merge 테이블에서 결과를 모으므로, 저장 전 한 번 더
  `synced_at DESC` / `답변일 DESC` / `신고번호 DESC` 로 재정렬해 줘야 모바일 알림 히스토리와
  최근 답변 재계산이 서버 대시보드와 같은 기준을 쓴다.

---


<!-- legacy CLAUDE.md 502-515 -->
## 파싱 규칙 (services/parser.py)

### 과태료 자동 파싱
- 신고 유형이 "버스전용차로 위반", "쓰레기, 폐기물", "불법주정차신고" 이고 `처리상태 == "수용"` → `범칙금_과태료 = "과태료"`
- **예외**: `process_status == "취하"` 이면 과태료 설정 안 함. 취하 확정 시 `penalty_amount`, `penalty_points` 초기화.

### 불수용 키워드 강제 교정
`['부득이하게', '종결합니다', '처벌이 어려운 점', '처분이 불가']` 포함 시 → 불수용 + 범칙금 초기화

### 경고 키워드
`['교통질서 안내장', '훈방권', '12대 중과실', ...]` — 범칙금 없을 때 '경고' 설정

---


<!-- legacy CLAUDE.md 584-594 -->
### API 기반 크롤러 (crawltitle_api)
`page_size=200` 으로 수천 건을 수 초 내 스캔. Selenium DOM 파싱 대비 ~10배 속도 향상.

### 별점 2단계 API
1. GET으로 이미 참여 완료 여부 확인 → 완료건 스킵
2. POST로 별점 제출
API 차단 대비 Selenium 백업 코드 주석 보존.

### Watchlist 독립 테이블
메인 테이블 스키마 변경 없이 감시 기능 분리. Join으로 효율적 추적.


<!-- legacy CLAUDE.md 616-637 -->
### 주정차위반 카테고리 (crawldetail_api.py, crawldetail.py)
크롤링 시 `entry_value` 기준으로 3분류:
- `"자동차·교통위반"` in entry_value → `traffic`
- `"불법주정차신고"` in entry_value → `parking`
- 그 외 → `other`

entry_value는 "본 신고는 안전신문고 앱의 **(entry_value)** 메뉴로 접수된 신고입니다" 패턴에서 추출.
주정차위반 예시: `불법주정차신고-기타 불법주정차`

주정차위반 파싱 규칙:
- 수용 시 과태료: parser.py의 `"불법주정차신고"` in entry_value 조건 그대로 적용
- 취하 시: penalty 초기화 (기타위반과 동일)
- `"미확인"` penalty 로직은 `"자동차·교통위반"` 조건에만 해당 → 주정차에 미적용 (정상)

### 파서 첨부파일 URL (services/parser.py)
`FILE_URL`이 상대경로(`/fileDown/singo/...`)로 오는 경우 `https://www.safetyreport.go.kr` 프리픽스 자동 추가.
`STTEMNT_IMAGE_URL`도 동일하게 처리. (텔레그램 첨부 URL 깨짐 버그 수정)

### 첨부사진/파일 URL 구분자
DB에는 `\n` 으로 구분 저장됨. 웹 `data_table.html`의 `renderAttach()`는 `d.split('\n')`으로 파싱.
모바일 측 파싱은 `safetyreport-mobile` 레포 참조.


<!-- legacy CLAUDE.md 683-709 -->
## 디버그 extractor (`scripts/debug/extractor.py`)

### 사용법
```bash
python scripts/debug/extractor.py SPP-2604-1234567   # 신고번호
python scripts/debug/extractor.py 59216726 40871819  # 내부 ID 다중
```

### 기능
- 신고번호(SPP-xxx) → DB 조회로 내부 ID 자동 변환, 다중 ID 순차 처리
- **API 방식** + **Selenium 방식** 둘 다 크롤링하여 결과 비교
- 출력 파일 5종 (`data/logs/`):
  - `{id}_api_raw.json` — API 원시 응답
  - `{id}_api_parsed.txt` — API 파싱 결과
  - `{id}_legacy_raw.html` — Selenium 전체 페이지 소스
  - `{id}_legacy_parsed.txt` — Selenium 파싱 결과
  - `{id}_diff.txt` — 두 방식 파싱 차이 자동 비교
- **DB 갱신 없음** — 순수 테스터

### 주요 설계: `_create_debug_driver()`
- **Docker** (`/.dockerenv`): 이미지 내장 Chromium + 시스템 chromedriver 직접 사용 (Hub 미사용)
  - 같은 컨테이너에서 Hub 통신 시 네트워크 스파이크 → Cloudflare 502 유발하므로 Hub 우회
- **비Docker**: chrome_mode 설정(hub/remote/desktop) 그대로 따름
- `remote` 모드(비Docker 한정): `driver.quit()` 호출 안 함 → 공유 Chrome 유지

---
