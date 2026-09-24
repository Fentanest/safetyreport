# 크롤러 변경 계획: 주정차 사진 촬영 시각 수집

작성 2026-09-24. 상태: **범위 포함 확정**(사용자 지시). 밤샘주차 조사(G6) 반영 완료 — 착수 가능.
목적: 주정차 과태료 추정에서 2시간 이상 가중(별표6 괄호 금액)과 화물차 밤샘주차를 판별한다([../design/statistics-spec.md](../design/statistics-spec.md) §4-1).

## 확인된 사실 (2026-09-24)
- 주정차 신고 사진은 **안전신문고 앱 카메라로만** 찍힌다(사용자 확인). 사진 EXIF `DateTimeOriginal` 에 촬영 시각이 있고
  (Software `SafetyReport_Android_SYSTEM_CAMERA`), 사진 상단 "촬영일시:… (안전신문고)" 표시와 초 단위까지 같다.
- API 첨부 메타(`ARR_C_FILES.FILES_CREATED`, `C_DATE`)는 업로드 시각이라 쓸 수 없다.
- 첨부 URL(`https://www.safetyreport.go.kr/fileDown/singo/…`)은 로그인 없이 200, `application/download`, Range 미지원. 약 6개월 뒤 만료되어 DB 에 "6개월 초과"로 남는다.
- 첨부 URL 은 API 경로(`services/parser.py` `ARR_C_FILES`)와 레거시 경로(`parser.py` goViewer 파싱) 모두 `첨부사진`(줄바꿈 구분)으로 모인다.
- iOS 앱 카메라의 EXIF 보존 여부는 미확인 — 없으면 "촬영 시각 없음"으로 두고 가중하지 않는다.

## 설계
| 항목 | 내용 |
|---|---|
| 수집 시점 | 상세 크롤링에서 **주정차 카테고리**(`entry_value` 에 `불법주정차신고`) 이고, 신규이거나 촬영 시각 컬럼이 비어 있을 때만. 같은 신고를 다시 받지 않는다 |
| 위치 | `core/crawler/detail_pipeline.py` 공통 단계(API·레거시 공용) → 새 모듈 `services/photo_capture_time.py` |
| 다운로드 | 스트리밍 GET 으로 앞 128KB 까지만 읽고 연결을 끊는다(EXIF 는 첫 64KB 안). 사진 수만큼(보통 2~4장) 요청, 타임아웃·`core/utils/retry` 재시도, 실패는 크롤링을 멈추지 않고 NULL |
| EXIF 해석 | 표준 라이브러리만 쓰는 작은 TIFF/EXIF 파서(`DateTimeOriginal` 0x9003, 없으면 `DateTime` 0x0132). Pillow 를 제품 의존성에 추가하지 않는다(PyInstaller 크기) |
| 저장 | detail/merge 새 컬럼: `사진_첫촬영`, `사진_끝촬영`(ISO `YYYY-MM-DD HH:MM:SS`), `사진_촬영수`(INTEGER). `upgrade_schema()` ALTER, `_merge_for_table` select 추가 |
| 외부 계약 | **최우선(PROJECT_RULES §3-1)**: 서버·모바일 스키마, `db_backup` 양방향 변환, 모바일 가져오기/내보내기, API 필드를 같은 작업 단위로 변경하고 양방향 왕복 테스트로 전 컬럼 일치 확인. 모바일 레포도 함께 수정(사용자 지시) |
| fixture | `block_if_fixture("photo capture fetch")` — 테스트에서는 합성 EXIF 바이트로 파서만 검증 |
| 과거 데이터 | 아직 만료되지 않은 주정차 신고에 한해 1회 백필 명령(`scripts/`), 요청 간 지연. 만료분은 NULL 유지 |
| 통계 사용 | 맨 앞·맨 뒤 사진 촬영 간격 ≥ 2시간 → 별표6 괄호 금액. 밤샘주차: 사업용 화물 번호판 + 촬영 시각이 0~4시 안에서 1시간 이상 → 추정 과태료 최저 5만(내부 분류는 과태료, statistics-spec §4-1) |

## 테스트
- EXIF 파서: 합성 JPEG 바이트(빅/리틀 엔디언, EXIF 없음, 잘린 파일) 단위 테스트.
- 파이프라인: 주정차만 호출, 실패 시 NULL·크롤링 계속, 재크롤링 시 재요청 안 함, fixture 모드 차단.
- 스키마: 구 DB 업그레이드, merge 반영, 서버↔모바일 DB 왕복 보존.
- 통계: 간격 1시간 59분 / 2시간 경계, 촬영 시각 없음, 밤샘주차 시간대 경계.

## 승인 경계
- 운영 크롤링에서 사진 앞부분 추가 요청이 생긴다(신고당 2~4회, 주정차만). 백필은 실행 전 대상 건수와 예상 요청 수를 보고하고 승인받는다.
- 운영 DB 스키마 변경은 앱 기동 시 자동 마이그레이션으로 적용되므로 배포(`main`) 전 모바일 쪽 준비가 끝나야 한다.
