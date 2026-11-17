#!/usr/bin/env bash
set -euo pipefail

python -m pip install --upgrade pip --quiet
if [ -f requirements-dev.txt ]; then
  pip install -r requirements-dev.txt --quiet
elif [ -f requirements.txt ]; then
  pip install -r requirements.txt --quiet
fi

echo
echo "Dev container ready."
echo "Fill in .env, then run scripts/smoke_test.sh to check the credentials and python -m ledger to start the app."
