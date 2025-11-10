# 안전신문고 자동 크롤러

안전신문고 웹사이트의 신고 내역을 자동으로 크롤링하고 관리하는 시스템입니다.

## 주요 기능

- **자동 크롤링**: 안전신문고에 자동으로 로그인하여 모든 신고 내역(요약 및 상세)을 크롤링합니다.
- **데이터베이스 관리**:
    - 크롤링한 데이터를 SQLite 데이터베이스에 체계적으로 저장합니다.
    - **자동 스키마 마이그레이션**: 애플리케이션 업데이트 시, 기존 데이터를 유지하면서 DB 테이블에 누락된 컬럼을 자동으로 추가합니다.
- **첨부파일 관리**:
    - 상세 정보 파싱 시, 일반 첨부파일과 이미지 첨부파일(첨부사진)을 자동으로 분리하여 저장합니다.
    - **오래된 링크 정리**: 6개월이 지난 신고 건의 지도, 첨부사진, 첨부파일 링크를 "6개월 초과"로 자동 변경하여 관리합니다.
- **데이터 내보내기**:
    - **Excel**: 전체 데이터를 서식이 적용된 Excel 파일로 저장합니다.
    - **Google Sheets**: Google Sheets API와 연동하여 결과를 스프레드시트로 저장합니다.
        - 지도 및 첨부사진은 `=image()` 함수를 통해 이미지 미리보기를 제공합니다.
        - 이미지 가독성을 위해 데이터 행의 높이가 자동으로 조절됩니다.
- **텔레그램 봇 연동**:
    - 크롤링, 엑셀 저장 등 주요 기능을 원격으로 실행할 수 있습니다.
    - 차량 번호의 일부만으로도 관련 신고 내역을 검색할 수 있습니다.
    - 모든 작업 완료 시 텔레그램으로 알림을 받습니다.
- **유연한 설정**: 
    - Docker 환경변수를 통해 주요 환경값을 설정할 수 있습니다.

---

## 설정 및 실행 (윈도우)

### 1. 실행 파일 다운로드

Github에서 단일 실행파일을 다운로드 받아 실행합니다.

### 2. ID/PW입력

UI에서 아이디와 비밀번호를 입력하고 `크롤링 시작`을 클릭합니다.

### 3. 부가기능 사용

`Alt+O`를 이용하여 옵션창을 연 뒤 구글 스프레드 시트와 텔레그램 봇 기능을 설정합니다.

구글 스프레드시트를 사용하기 위해선, 스프레드시트 API인증을 위한 JSON파일과 권한이 설정된 스프레드시트의 주소값이 필요합니다.

[JSON파일 및 스프레드시트 권한설정 방법](https://sseozytank.tistory.com/74)

스프레드시트의 주소는 `https://docs.google.com/spreadsheets/d/abcdefghijklmnopqrstuvwxyz/` 일 경우 `abcdefghijklmnopqrstuvwxyz`를 사용하면 됩니다.

텔레그램 봇을 설정하고 프로그램이 실행되어 있다면,

텔레그램 봇을 통해 크롤링 지시, 누적된 데이터를 통한 차량검색 기능 등을 사용할 수 있습니다.

---

## 설정 및 실행 (도커)

### 1. 설정 파일 준비

프로젝트 루트 경로에 docker-compose와 사용할 `.env`를 아래의 예시를 참고하여 작성합니다.

**`.env`**
```ini
[SELENIUM]
remotepath = http://localhost:4444

[LOGIN]
username = your_username
password = your_password

[TELEGRAM]
telegram_token = your_token
chat_id = your_chat_id

[SETTINGS]
interval = 60
max_retry = 10
max_empty_pages = 3
log_level = INFO
TZ = Asia/Seoul

[GOOGLESHEET]
sheet_key = your_sheet_key
```

- **Google Sheets 연동**: `sheet_key` 값을 채우고, `data/auth/gspread.json` 위치에 구글 서비스 계정 인증 파일을 위치시켜야 기능이 활성화됩니다.
[JSON파일 및 스프레드시트 권한설정 방법](https://sseozytank.tistory.com/74)
- `sheet_key`값은 스프레드시트의 주소를 예로 들어 `https://docs.google.com/spreadsheets/d/abcdefghijklmnopqrstuvwxyz/` 일 경우 `abcdefghijklmnopqrstuvwxyz`를 사용하면 됩니다.
- **Telegram 연동**: `telegram_token`과 `chat_id` 값을 모두 채워야 기능이 활성화됩니다.
- `max_empty_pages`는 나의 신고내역 페이지에서 '진행'상태 신고건이 더 이상 없는 페이지를 n번째 도달할 경우 더 이상 새로운 신고내역을 찾지 않기 위한 설정값입니다.

### 2. Docker Compose 실행

아래 예시와 같이 `docker-compose.yml` 파일을 작성하여 컨테이너를 실행합니다. `data` 폴더 전체를 볼륨으로 연결하여 설정, 로그, 인증 파일 및 결과물을 영속적으로 관리합니다.

```yaml
services:
  mysafetyreport:
    container_name: safetyreport
    image: fentanest/safetyreport:latest
    env_file:
      - .env 
    volumes:
      - ./data:/app/data
    restart: always
```

### 실행 옵션 (start.py)

`start.py` 스크립트 실행 시 다음과 같은 인자를 사용하여 동작을 제어할 수 있습니다. Docker 환경에서는 `docker exec -it <컨테이너_이름> python start.py [옵션]` 형태로 사용할 수 있습니다.

- `--force`: 이미 처리된 신고 건을 포함하여 모든 상세 내역을 다시 크롤링합니다.
- `--reset`: 기존 데이터베이스 파일을 모두 삭제하고 처음부터 다시 시작합니다. (주의: 모든 데이터가 사라집니다.)
- `--min`: '진행' 상태의 신고가 `max_empty_pages` 설정값에 지정된 페이지 수만큼 연속으로 나오지 않으면 크롤링을 조기 종료합니다.
- `--p <페이지>`: 지정된 페이지만 크롤링합니다.
  - 예시: `python start.py --p 5` (5페이지 한 페이지만 크롤링)
- `--p <시작페이지>-<끝페이지>`: 지정된 페이지 범위만 크롤링합니다.
  - 예시: `python start.py --p 5-10` (5페이지부터 10페이지까지 크롤링)
- `--p <페이지1>,<페이지2>,...`: 쉼표로 구분된 특정 페이지만 크롤링합니다.
  - 예시: `python start.py --p 5,7,9` (5, 7, 9 페이지를 크롤링)
- 모든 페이지는 `30개씩 보기` 상태에서 계산됩니다.

## 개발

### 디버깅 스크립트

- **`debug_extractor.py`**: 특정 신고 번호의 상세 페이지만 파싱하여 결과를 `data/logs/[신고번호].txt` 파일로 저장합니다.
  ```bash
  python debug_extractor.py <신고번호>
  ```
- **`debug_save.py`**: DB에 저장된 최종 결과를 Excel 및 Google Sheets로 저장하는 기능만 테스트합니다.
- **`debug_merge.py`**: 여러 테이블의 데이터를 최종 테이블로 병합하는 기능만 테스트합니다.
