#!/bin/bash

# Ensure database directory exists
mkdir -p /app/data/logs
mkdir -p /app/data/auth

if [ "$#" -gt 0 ]; then
    exec "$@"
else
    # Run the FastAPI server via Uvicorn explicitly
    exec /usr/local/bin/python -m uvicorn main:app --host 0.0.0.0 --port 6819
fi
