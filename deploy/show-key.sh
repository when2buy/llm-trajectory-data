#!/usr/bin/env bash
# show-key.sh — print the virtual key(s) from the local DB (keys are masked in the API/UI).
set -euo pipefail
cd "$(dirname "$0")"
DB="$(pwd)/data/one-api.db"
[ -f "$DB" ] || { echo "❌ no DB at $DB — run ./setup.sh first"; exit 1; }
python3 - "$DB" <<'PY'
import sys,sqlite3
c=sqlite3.connect(sys.argv[1])
print(f"{'id':<4}{'name':<16}{'key':<54}{'quota'}")
for r in c.execute("SELECT id,name,key,remain_quota,unlimited_quota FROM tokens"):
    q='UNLIMITED' if r[4] else f'{r[3]/500000:.2f} USD'
    print(f"{r[0]:<4}{r[1]:<16}{'sk-'+r[2]:<54}{q}")
PY
