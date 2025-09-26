FROM python:3.12.3-slim-bookworm AS builder

WORKDIR /app
RUN apt-get update && apt-get install -y gcc

COPY ./requirements.txt .
RUN pip install -r requirements.txt webdriver_manager openpyxl

FROM python:3.12.3-slim-bookworm
RUN apt-get update && apt-get install -y cron nano
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

WORKDIR /app
COPY . .
RUN chmod +x /app/entrypoint.sh

# Set non-sensitive environment variables with default values.
# Sensitive variables (USERNAME, PASSWORD, sheet_key, etc.) should be injected at runtime.
ENV dbfile="data.db"
ENV interval="60"
ENV max_retry="10"
ENV log_level="INFO"
ENV exectime1="10:00"
ENV exectime2="12:00"
ENV exectime3="19:00"

VOLUME /app/results
VOLUME /app/auth
VOLUME /app/logs

ENTRYPOINT ["/app/entrypoint.sh"]
