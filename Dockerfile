FROM python:3.12.3-slim-bookworm AS builder

WORKDIR /app
RUN apt-get update && apt-get install -y gcc

COPY ./requirements.txt .
RUN pip install -r requirements.txt webdriver_manager openpyxl

FROM python:3.12.3-slim-bookworm
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

WORKDIR /app
COPY . .

ENV USERNAME nousername
ENV PASSWORD nopassword
ENV dbfile data.db
ENV sheet_key nosheetkey
ENV interval 60
ENV max_retry 10
ENV remotepath nonpath
ENV log_level INFO
ENV telegram_token token
ENV chat_id id

ENV exectime1 10:00
ENV exectime2 12:00
ENV exectime3 19:00

VOLUME /app/results
VOLUME /app/auth
VOLUME /app/logs

ENTRYPOINT ["/app/entrypoint.sh"]