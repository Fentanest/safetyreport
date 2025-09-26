# 안전신문고 크롤러

안전신문고 웹사이트의 신고 내역을 크롤링하는 프로그램입니다.

## 주요 기능

- 안전신문고에 자동으로 로그인하여 모든 신고 내역을 크롤링합니다.
- 크롤링한 데이터를 SQLite 데이터베이스와 Excel 파일로 저장합니다.
- Google Sheets API와 연동하여 결과를 구글 스프레드시트로 저장하는 기능을 선택적으로 사용할 수 있습니다.
- Telegram 봇을 통해 크롤링 완료 알림을 받거나, 수동으로 크롤링을 시작할 수 있습니다.

## 사용 방법

### Docker Compose

아래 예시와 같이 `docker-compose.yml` 파일을 작성하여 컨테이너를 실행할 수 있습니다.

```yaml
services:
  mysafetyreport:
    container_name: safetyreport
    image: fentanest/safetyreport:latest
    env_file:
      - .env
    volumes:
      - ./data/logs:/app/logs
      - ./data/results:/app/results
      - ./data/auth:/app/auth
    restart: always
```

### 설정 (`.env` 파일)

실행에 필요한 환경 변수는 `.env` 파일을 통해 설정합니다.

```
# 안전신문고 계정 정보
USERNAME= # 아이디
PASSWORD= # 비밀번호

# Selenium Grid Hub 주소 (예시:"http://192.168.50.1:4444")
remotepath= 

# 자동 크롤링 실행 시간 (최대 3개, 아래는 기본값)
exectime1=10:00
exectime2=12:00
exectime3=20:00

# 크롤링 실패 시 재시도 설정
interval=60       # 재시도 간격 (초)
max_retry=3       # 최대 재시도 횟수

# Google Sheets 연동 (선택 사항)
# auth/gspread.json 파일과 아래 sheet_key가 모두 있어야 활성화됩니다.
sheet_key= # 구글 스프레드시트 고유 주소

# Telegram 봇 알림 (선택 사항)
telegram_token= # 텔레그램 봇 토큰
chat_id=        # 알림을 받을 채팅 ID
```
