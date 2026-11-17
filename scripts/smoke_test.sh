#!/usr/bin/env bash
# Hits each ledger endpoint once and prints the HTTP status, to confirm credentials and token scopes.
# Every request is a GET. Values come from the environment, or from .env when a variable is unset or empty.
set -uo pipefail

if [ -f .env ]; then
  while IFS='=' read -r key value; do
    case "$key" in
      SIGNALWIRE_SPACE|SIGNALWIRE_PROJECT_ID|SIGNALWIRE_API_TOKEN|SIGNALWIRE_PAT)
        value="${value%$'\r'}"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
        [ -z "${!key:-}" ] && export "$key=$value"
        ;;
    esac
  done < .env
fi

: "${SIGNALWIRE_SPACE:?set SIGNALWIRE_SPACE in .env}"
# Accept the space name alone or its full host name.
SPACE="${SIGNALWIRE_SPACE#https://}"
SPACE="${SPACE%%/*}"
SPACE="${SPACE%.signalwire.com}"
BASE="https://${SPACE}.signalwire.com"
MONTH="$(date -u +%Y-%m)"

check() {
  local label="$1" auth="$2" path="$3"
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -u "$auth" "${BASE}${path}")
  printf '%-22s %s  %s\n' "$label" "$code" "$path"
}

if [ -n "${SIGNALWIRE_PROJECT_ID:-}" ] && [ -n "${SIGNALWIRE_API_TOKEN:-}" ]; then
  P="${SIGNALWIRE_PROJECT_ID}:${SIGNALWIRE_API_TOKEN}"
  check "voice logs"      "$P" "/api/voice/logs?page_size=1"
  check "message logs"    "$P" "/api/messaging/logs?page_size=1"
  check "fax logs"        "$P" "/api/fax/logs?page_size=1"
  check "video logs"      "$P" "/api/video/logs?page_size=1"
  check "conference logs" "$P" "/api/logs/conferences?page_size=1"
else
  echo "skipping log endpoints: SIGNALWIRE_PROJECT_ID / SIGNALWIRE_API_TOKEN not set"
fi

if [ -n "${SIGNALWIRE_PAT:-}" ]; then
  T=":${SIGNALWIRE_PAT}"
  check "space"               "$T" "/api/space"
  check "balance"             "$T" "/api/space/balance"
  check "low balance setting" "$T" "/api/space/low_balance_setting"
  check "usage"               "$T" "/api/space/usage"
  check "statement periods"   "$T" "/api/space/billing_statements"
  check "statement"           "$T" "/api/space/billing_statement?month=${MONTH}"
  check "payment history"     "$T" "/api/space/payment_history?page_size=1"
  check "billing profile"     "$T" "/api/space/billing_profile"
  check "payment methods"     "$T" "/api/space/payment_methods"
  check "projects"            "$T" "/api/projects?page_size=1"
else
  echo "skipping billing endpoints: SIGNALWIRE_PAT not set"
fi
