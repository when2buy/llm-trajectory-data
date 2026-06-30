#!/usr/bin/env bash
# start.sh — launch new-api + beta_sanitizer (the two local processes).
# Run ./setup.sh first. Public exposure (Cloudflare tunnel etc.) is separate — see README.
set -euo pipefail
cd "$(dirname "$0")"
set -a; source .env; set +a
NEWAPI_PORT="${NEWAPI_PORT:-41029}"
SANITIZER_PORT="${SANITIZER_PORT:-41030}"
DATA_DIR="$(pwd)/data"
BIN="$(cat "$DATA_DIR/.binpath")"
SESSION_SECRET="$(cat "$DATA_DIR/.session_secret")"

# new-api
if ! curl -fs "http://127.0.0.1:${NEWAPI_PORT}/api/status" >/dev/null 2>&1; then
  echo "▸ starting new-api :${NEWAPI_PORT}"
  ( cd "$DATA_DIR" && setsid env SESSION_SECRET="$SESSION_SECRET" "$BIN" \
      --port "$NEWAPI_PORT" --log-dir ./logs >./newapi.log 2>&1 & )
  for i in $(seq 1 30); do curl -fs "http://127.0.0.1:${NEWAPI_PORT}/api/status" >/dev/null 2>&1 && break; sleep 1; done
else echo "▸ new-api already up"; fi

# beta_sanitizer (Claude Code entrypoint)
if ! curl -fs "http://127.0.0.1:${SANITIZER_PORT}/" >/dev/null 2>&1; then
  echo "▸ starting beta_sanitizer :${SANITIZER_PORT} -> :${NEWAPI_PORT}"
  setsid env BS_PORT="$SANITIZER_PORT" BS_UPSTREAM="http://127.0.0.1:${NEWAPI_PORT}" \
      python3 beta_sanitizer.py >"$DATA_DIR/sanitizer.log" 2>&1 &
else echo "▸ sanitizer already up"; fi

sleep 2
echo "✅ up. Claude Code entrypoint: http://127.0.0.1:${SANITIZER_PORT}"
echo "   admin UI:                  http://127.0.0.1:${NEWAPI_PORT}"
