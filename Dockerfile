FROM python:3.12 AS builder

WORKDIR /app
RUN apt-get update && apt-get install -y gcc g++

COPY ./requirements.txt .
RUN python -m pip install --upgrade pip setuptools wheel cython
RUN pip install -r requirements.txt webdriver_manager openpyxl

FROM python:3.12
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

WORKDIR /app
COPY . .
RUN chmod +x /app/entrypoint.sh

# 아이디, 비밀번호, 구글시트 고유주소는 기본값 없음
ENV dbfile="data.db"
ENV interval="60"
ENV max_retry="10"
ENV log_level="INFO"
ENV TZ="Asia/Seoul"

VOLUME /app/results
VOLUME /app/auth
VOLUME /app/logs

ENTRYPOINT ["/app/entrypoint.sh"]
