#!/bin/sh
set -eu
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py bootstrap_admin
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 60 --error-logfile - --access-logfile /dev/null
