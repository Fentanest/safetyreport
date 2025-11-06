FROM python:3.14 AS builder

WORKDIR /app
RUN apt-get update && apt-get install -y gcc g++

COPY ./requirements.txt .
RUN python -m pip install --upgrade pip setuptools wheel cython
RUN pip install -r requirements.txt

FROM python:3.14
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages

WORKDIR /app
COPY . .
RUN chmod +x /app/entrypoint.sh

VOLUME /app/data

ENTRYPOINT ["/app/entrypoint.sh"]
