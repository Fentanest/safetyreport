#!/bin/bash

# Log file for cron jobs
CRON_LOG="/var/log/cron.log"
touch $CRON_LOG

# Create a crontab file
CRON_FILE="/etc/cron.d/crawler-cron"
echo "" > $CRON_FILE # Clear the file

# Read execution times from environment variables and build the crontab
for i in {1..3}; do
    # Dynamically get the value of exectime1, exectime2, etc.
    var_name="exectime$i"
    execution_time=${!var_name}

    # Check if the variable is set and not empty
    if [ -n "$execution_time" ]; then
        # Validate the time format (HH:MM)
        if [[ $execution_time =~ ^([0-1][0-9]|2[0-3]):([0-5][0-9])$ ]]; then
            hour=${execution_time:0:2}
            minute=${execution_time:3:2}
            
            # Add a cron job entry. Redirect output to the log file.
            echo "$minute $hour * * * root python /app/start.py >> $CRON_LOG 2>&1" >> $CRON_FILE
            echo "Scheduled job at ${hour}:${minute}"
        else
            echo "Warning: Invalid time format for $var_name: '$execution_time'. Should be HH:MM. Skipping."
        fi
    fi
done

# Give execution rights on the cron job file
chmod 0644 $CRON_FILE

# Start the cron daemon in the background
cron

echo "Cron daemon started. Starting Telegram bot..."

# Execute the bot as the main process
exec python /app/bot.py
