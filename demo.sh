#!/bin/sh
# Runs the end-to-end demo (uses demo_gym.db, never gym.db).
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then exec .venv/bin/python demo.py; else exec python3 demo.py; fi
