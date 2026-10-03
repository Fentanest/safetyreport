FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ chromium chromium-driver xvfb \
    libnss3 libatk-bridge2.0-0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libasound2 \
    libpangocairo-1.0-0 libpango-1.0-0 libcups2 \
    && rm -rf /var/lib/apt/lists/*

COPY ./requirements.txt .
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/entrypoint.sh

# 공개 커뮤니티 설정(Supabase URL·publishable key·site URL)을 이미지에 굽는다(PC 실행파일의 번들과 같은 파일·같은 검증).
# 공개값만: 비밀 키·자리표시자는 거부한다. 로컬/dev도 저장소 공개 기본값이 없으면 빌드를 멈춘다.
# 실행 때 환경변수(SAFETYREPORT_COMMUNITY_* / COMMUNITY_*)나 data/config.ini [COMMUNITY] 가 있으면 그 값이 우선한다.
ARG COMMUNITY_SUPABASE_URL=""
ARG COMMUNITY_SUPABASE_PUBLISHABLE_KEY=""
ARG COMMUNITY_SITE_URL=""
ARG COMMUNITY_CONFIG_REQUIRED="0"
RUN COMMUNITY_SUPABASE_URL="$COMMUNITY_SUPABASE_URL" COMMUNITY_SUPABASE_PUBLISHABLE_KEY="$COMMUNITY_SUPABASE_PUBLISHABLE_KEY" \
    COMMUNITY_SITE_URL="$COMMUNITY_SITE_URL" COMMUNITY_CONFIG_REQUIRED="$COMMUNITY_CONFIG_REQUIRED" \
    python scripts/build/write_community_public.py --out /app/community_public.json

VOLUME /app/data
EXPOSE 6819

ENTRYPOINT ["/app/entrypoint.sh"]
