#!/bin/sh
# Starts the dashboard. First run: creates .env, an access key and gym.db if they are missing.
cd "$(dirname "$0")" || exit 1
PY=.venv/bin/python
[ -x "$PY" ] || { echo "No .venv yet. Run: python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
[ -f .env ] || cp .env.example .env
if ! grep -Eq '^WEBHOOK_SECRET=.+' .env; then
  SECRET=$($PY -c "import secrets; print(secrets.token_urlsafe(24))")
  grep -v '^WEBHOOK_SECRET=' .env > .env.tmp; echo "WEBHOOK_SECRET=$SECRET" >> .env.tmp; mv .env.tmp .env
  echo "Created your access key in .env (the WEBHOOK_SECRET line)."
fi
[ -f gym.db ] || $PY seed.py
echo ""
echo "  Open http://127.0.0.1:8000"
echo "  Sign in with the WEBHOOK_SECRET value from .env, e.g.:  grep WEBHOOK_SECRET .env"
echo ""
exec $PY -m uvicorn server:create_app --factory --host 127.0.0.1 --port 8000
