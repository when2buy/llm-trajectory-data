#!/usr/bin/env bash
# setup.sh — one-shot deploy of a Claude-Code → Bedrock token relay (new-api + sanitizer).
#
# Idempotent-ish: downloads new-api, initializes the root admin, creates a Bedrock channel
# (with the model-mapping / param-override fixes that make Claude Code work on Bedrock), and
# mints one budgeted virtual key. Encodes every gotcha from the original deployment so a
# teammate can stand this up on a fresh machine.
#
# Requires: bash, curl, python3, and a Bedrock bearer token. No Docker needed.
#
# Usage:
#   cp .env.example .env && edit .env      # set AWS_BEARER_TOKEN_BEDROCK, ADMIN_PASSWORD, ...
#   ./setup.sh
#   ./start.sh                              # then launch the processes
set -euo pipefail
cd "$(dirname "$0")"

# ---- load config ----
[ -f .env ] || { echo "❌ no .env — copy .env.example to .env and fill it in"; exit 1; }
set -a; source .env; set +a

: "${AWS_BEARER_TOKEN_BEDROCK:?set AWS_BEARER_TOKEN_BEDROCK in .env}"
: "${ADMIN_PASSWORD:?set ADMIN_PASSWORD in .env (>=8 chars, alphanumeric)}"
AWS_REGION="${AWS_REGION:-us-west-2}"
NEWAPI_PORT="${NEWAPI_PORT:-41029}"
NEWAPI_VERSION="${NEWAPI_VERSION:-v1.0.0-rc.15}"
KEY_BUDGET_USD="${KEY_BUDGET_USD:-5}"
BASE="http://127.0.0.1:${NEWAPI_PORT}"
DATA_DIR="$(pwd)/data"; mkdir -p "$DATA_DIR" logs

# ---- 1. download new-api binary (linux amd64) ----
BIN="$DATA_DIR/new-api-${NEWAPI_VERSION}"
if [ ! -x "$BIN" ]; then
  echo "▸ downloading new-api ${NEWAPI_VERSION} ..."
  URL="https://github.com/QuantumNous/new-api/releases/download/${NEWAPI_VERSION}/new-api-${NEWAPI_VERSION}"
  curl -fsSL "$URL" -o "$BIN"
  chmod +x "$BIN"
fi
echo "$BIN" > "$DATA_DIR/.binpath"

# ---- 2. boot new-api (temporarily, to configure it) ----
echo "▸ booting new-api on :${NEWAPI_PORT} ..."
SESSION_SECRET="${SESSION_SECRET:-$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)}"
echo "$SESSION_SECRET" > "$DATA_DIR/.session_secret"
( cd "$DATA_DIR" && SESSION_SECRET="$SESSION_SECRET" "$BIN" --port "$NEWAPI_PORT" --log-dir ./logs >./newapi.boot.log 2>&1 & echo $! > .boot_pid )
for i in $(seq 1 30); do curl -fs "$BASE/api/status" >/dev/null 2>&1 && break; sleep 1; done

cleanup_boot() { kill "$(cat "$DATA_DIR/.boot_pid" 2>/dev/null)" 2>/dev/null || true; }
trap cleanup_boot EXIT

# ---- 3. initialize root admin (idempotent) ----
echo "▸ initializing admin ..."
python3 - "$BASE" "$ADMIN_PASSWORD" <<'PY'
import sys,json,urllib.request,urllib.error
base,pw=sys.argv[1],sys.argv[2]
def post(path,body):
    r=urllib.request.urlopen(urllib.request.Request(base+path,data=json.dumps(body).encode(),
        method='POST',headers={'Content-Type':'application/json'}),timeout=15)
    return json.loads(r.read())
try:
    d=post('/api/setup',{"username":"root","password":pw,"confirmPassword":pw,"SelfUseModeEnabled":True})
    print("  setup:",d.get('message'))
except urllib.error.HTTPError as e:
    print("  setup skipped (already initialized?)",e.code)
PY

# ---- 4. login, capture session + user id ----
echo "▸ logging in ..."
LOGIN=$(curl -fs -c "$DATA_DIR/.cookies" -X POST "$BASE/api/user/login" \
  -H "Content-Type: application/json" -d "{\"username\":\"root\",\"password\":\"$ADMIN_PASSWORD\"}")
UID_NA=$(echo "$LOGIN" | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['id'])")
SESSION=$(grep -oE 'session[[:space:]]+[^[:space:]]+' "$DATA_DIR/.cookies" | awk '{print $2}' | tail -1)
echo "  user id: $UID_NA"

# ---- 5. create the Bedrock channel (with all the fixes) ----
echo "▸ creating Bedrock channel ..."
python3 - "$BASE" "$UID_NA" "$SESSION" "$AWS_BEARER_TOKEN_BEDROCK" "$AWS_REGION" <<'PY'
import sys,json,urllib.request,urllib.error
base,uid,sess,bearer,region=sys.argv[1:6]
hdr={'Content-Type':'application/json','New-Api-User':uid,'Cookie':f'session={sess}'}
# friendly model name -> real Bedrock inference-profile id  (EDIT to taste)
mapping={
  "claude-sonnet-4-5":"us.anthropic.claude-sonnet-4-6",
  "claude-haiku-4-5":"us.anthropic.claude-haiku-4-5-20251001-v1:0",
  "gpt-oss-120b":"openai.gpt-oss-120b-1:0",
  "gpt-oss-20b":"openai.gpt-oss-20b-1:0",
}
# Bedrock rejects these body fields that Claude Code sends -> delete them
param_override={"operations":[
  {"path":"context_management","mode":"delete"},
  {"path":"anthropic_beta","mode":"delete"},
  {"path":"betas","mode":"delete"},
]}
payload={"mode":"single","channel":{
  "name":"bedrock","type":33,                      # 33 = AWS Bedrock
  "key":f"{bearer}|{region}",                        # api-key (bearer) mode: <token>|<region>
  "base_url":"","group":"default","groups":["default"],
  "models":",".join(mapping.keys()),
  "model_mapping":json.dumps(mapping),
  "param_override":json.dumps(param_override),
  "settings":json.dumps({"aws_key_type":"api_key"}),
}}
try:
    r=urllib.request.urlopen(urllib.request.Request(base+'/api/channel/',
        data=json.dumps(payload).encode(),method='POST',headers=hdr),timeout=15)
    print("  channel:",json.loads(r.read()).get('message') or 'created')
except urllib.error.HTTPError as e:
    print("  channel error:",e.code,e.read().decode()[:200])
PY

# ---- 6. mint one budgeted virtual key ----
echo "▸ creating a budgeted virtual key (\$${KEY_BUDGET_USD}) ..."
# new-api quota unit: 500000 quota = $1 (default). budget*500000.
QUOTA=$(python3 -c "print(int(float('$KEY_BUDGET_USD')*500000))")
python3 - "$BASE" "$UID_NA" "$SESSION" "$QUOTA" <<'PY'
import sys,json,urllib.request
base,uid,sess,quota=sys.argv[1:5]
hdr={'Content-Type':'application/json','New-Api-User':uid,'Cookie':f'session={sess}'}
tok={"name":"team-key","remain_quota":int(quota),"unlimited_quota":False,"expired_time":-1}
urllib.request.urlopen(urllib.request.Request(base+'/api/token/',
    data=json.dumps(tok).encode(),method='POST',headers=hdr),timeout=15)
print("  key created (read it with ./show-key.sh)")
PY

cleanup_boot; trap - EXIT
echo ""
echo "✅ setup done. Next:"
echo "   ./start.sh          # launch new-api + beta_sanitizer"
echo "   ./show-key.sh       # print the virtual key to hand out"
echo "   admin UI: $BASE  (user root)"
