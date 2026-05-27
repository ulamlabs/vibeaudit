#!/bin/sh
set -e

# Run migrations if needed (optional)
if [ "$RUN_MIGRATIONS" = "true" ]; then
    echo "Running migrations..."
    python manage.py migrate --noinput
fi

# Determine which process to run based on the command
if [ "$1" = "migrate" ]; then
    echo "Running migrations..."
    exec python manage.py migrate --noinput
elif [ "$1" = "web" ]; then
    echo "Starting Gunicorn web server..."
    exec gunicorn vibeaudit.wsgi:application --bind 0.0.0.0:8080 \
        --access-logfile="-" \
        --error-logfile="-" \
        --workers=4 \
        --worker-class=gthread \
        --threads=2
# elif [ "$1" = "worker" ]; then
#     echo "Starting Celery worker..."
#     exec celery -A vibeaudit worker --loglevel=info
# elif [ "$1" = "beat" ]; then
#     echo "Starting Celery beat..."
#     exec celery -A vibeaudit beat --loglevel=info
else
    exec "$@"
fi
