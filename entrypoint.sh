#!/bin/bash

# If arguments are passed to the script, execute them. Otherwise, run the default bot.
if [ "$#" -gt 0 ]; then
    exec "$@"
else
    exec /usr/local/bin/python /app/bot.py
fi
