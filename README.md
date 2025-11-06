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
    - `data/config.ini` 파일을 통해 대부분의 설정을 쉽게 변경할 수 있습니다.
    - Docker 환경변수를 통해 `config.ini` 설정을 덮어쓸 수 있어, 유연한 배포가 가능합니다.

## 설정 및 실행

### 1. 설정 파일 준비

프로젝트 루트 경로에 `data` 폴더를 생성하고, 그 안에 `config.ini` 파일을 아래와 같이 작성합니다. Google Sheets나 Telegram 연동이 비활성화된 경우, 해당 섹션의 값을 비워두면 기능이 자동으로 비활성화됩니다.

**`data/config.ini`:**
```ini
[SELENIUM]
remotepath = http://localhost:4444/wd/hub

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
- **Telegram 연동**: `telegram_token`과 `chat_id` 값을 모두 채워야 기능이 활성화됩니다.

### 2. Docker Compose 실행

아래 예시와 같이 `docker-compose.yml` 파일을 작성하여 컨테이너를 실행합니다. `data` 폴더 전체를 볼륨으로 연결하여 설정, 로그, 인증 파일 및 결과물을 영속적으로 관리합니다.

```yaml
services:
  mysafetyreport:
    container_name: safetyreport
    image: fentanest/safetyreport:latest
    # .env 파일을 사용해 config.ini 설정을 덮어쓸 수 있습니다.
    # env_file:
    #   - .env 
    volumes:
      - ./data:/app/data
    restart: always
```

## 개발

### 빌드

`build.sh` 스크립트를 통해 Docker 이미지를 빌드할 수 있습니다.

- **릴리즈 빌드 및 푸시**:
  ```bash
  ./build.sh
  ```
  - `VERSION` 파일의 패치 버전을 1 올리고, `latest` 및 신규 버전 태그로 이미지를 빌드하여 Docker Hub에 푸시합니다.

- **개발용 로컬 빌드**:
  ```bash
  ./build.sh --dev
  ```
  - `dev` 태그로 로컬 빌드만 수행합니다. (푸시 X)

### 디버깅 스크립트

- **`debug_extractor.py`**: 특정 신고 번호의 상세 페이지만 파싱하여 결과를 `data/logs/[신고번호].txt` 파일로 저장합니다.
  ```bash
  python debug_extractor.py <신고번호>
  ```
- **`debug_save.py`**: DB에 저장된 최종 결과를 Excel 및 Google Sheets로 저장하는 기능만 테스트합니다.
- **`debug_merge.py`**: 여러 테이블의 데이터를 최종 테이블로 병합하는 기능만 테스트합니다.