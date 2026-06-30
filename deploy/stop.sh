#!/usr/bin/env bash
# stop.sh — stop the two local processes (does not touch any tunnel).
set -uo pipefail
cd "$(dirname "$0")"
pkill -f "beta_sanitizer.py" 2>/dev/null && echo "▸ stopped sanitizer" || echo "▸ sanitizer not running"
pkill -f "new-api-" 2>/dev/null && echo "▸ stopped new-api" || echo "▸ new-api not running"
