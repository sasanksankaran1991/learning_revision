#!/bin/sh
cd "$(dirname "$0")"
if [ -x backend/.venv/bin/python ]; then
  exec backend/.venv/bin/python run.py "$@"
else
  exec python3 run.py "$@"
fi
