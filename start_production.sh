#!/bin/bash

# Production startup script for Sava game application
# This script starts the application using gunicorn with eventlet workers

# Set default environment variables if not already set
export FLASK_ENV=${FLASK_ENV:-production}
export PORT=${PORT:-5000}
export HOST=${HOST:-0.0.0.0}
export LOG_LEVEL=${LOG_LEVEL:-info}

echo "Starting Sava game application in production mode..."
echo "Environment: $FLASK_ENV"
echo "Host: $HOST"
echo "Port: $PORT"
echo "Log Level: $LOG_LEVEL"

# Start gunicorn with the configuration file
exec gunicorn --config gunicorn.conf.py wsgi:application