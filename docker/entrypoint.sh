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
    # Worker/thread counts are env-configurable (override without rebuilding):
    #   WEB_CONCURRENCY  - number of worker processes (default 2)
    #   GUNICORN_THREADS - threads per worker (default 4)
    # --preload imports the app once in the master and forks, so workers share
    # the resident set copy-on-write and spawn faster.
    exec gunicorn vibeaudit.wsgi:application --bind 0.0.0.0:8080 \
        --access-logfile="-" \
        --error-logfile="-" \
        --workers="${WEB_CONCURRENCY:-2}" \
        --worker-class=gthread \
        --threads="${GUNICORN_THREADS:-4}" \
        --preload
elif [ "$1" = "worker" ]; then
    echo "Starting Celery worker..."
    # Concurrency is env-configurable (override without rebuilding):
    #   CELERY_CONCURRENCY - number of prefork child processes (default 2)
    #   CELERY_LOGLEVEL    - log level (default info)
    # Each child imports the full AI stack, so this knob bounds worker memory.
    # Celery's own default is host-CPU-count, which over-forks on a CPU-limited
    # pod (billiard counts host cores, not the cgroup limit), so we set 2.
    exec celery -A vibeaudit worker \
        --loglevel="${CELERY_LOGLEVEL:-info}" \
        --concurrency="${CELERY_CONCURRENCY:-2}"
# elif [ "$1" = "beat" ]; then
#     echo "Starting Celery beat..."
#     exec celery -A vibeaudit beat --loglevel=info
else
    exec "$@"
fi
