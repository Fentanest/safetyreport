FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc g++ chromium chromium-driver xvfb
# Note: Selenium might need chromium/chromium-driver if not using a Hub remote.

COPY ./requirements.txt .
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/entrypoint.sh

VOLUME /app/data
EXPOSE 6819

ENTRYPOINT ["/app/entrypoint.sh"]
