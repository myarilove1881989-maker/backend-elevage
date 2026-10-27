#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
exec gunicorn -c deployment/gunicorn.conf.py config.wsgi:application
