#!/bin/bash
# Run the REAL Claude Code on a QFBench task, with the routing proxy in front.
# Usage: run_cc_routed.sh <task> <policy> <run_id> [main_target]
set -u
TASK="$1"; POLICY="$2"; RID="${3:-0}"; MAIN_TARGET="${4:-us.anthropic.claude-sonnet-4-6}"
ROOT=/mnt/localssd/token-router
TASKDIR="$ROOT/finance-bench/tasks/$TASK"
PORT=$((9930 + RANDOM % 50))
WS="/tmp/ccrouted/${TASK}_${POLICY}_${RID}"
LOG="/tmp/ccrouted/${TASK}_${POLICY}_${RID}.calls.jsonl"
rm -rf "$WS"; mkdir -p "$WS/output" /tmp/ccrouted

# stage task data
if [ -d "$TASKDIR/environment/data" ]; then cp "$TASKDIR/environment/data/"* "$WS/" 2>/dev/null; fi
# instruction becomes the prompt; map /app -> workspace so paths resolve locally
sed "s#/app/output#$WS/output#g; s#/app#$WS#g" "$TASKDIR/instruction.md" > "$WS/TASK.md"

# launch proxy
TRACE="/tmp/ccrouted/${TASK}_${POLICY}_${RID}.trace.jsonl"; rm -f "$TRACE"
ROUTER_POLICY="$POLICY" ROUTER_PORT="$PORT" ROUTER_LOG="$LOG" ROUTER_MAIN_TARGET="$MAIN_TARGET" ROUTER_TRACE="$TRACE" \
  uvx -p 3.11 --with boto3 python "$ROOT/llm-trajectory-data/scripts/router_proxy.py" >"/tmp/ccrouted/proxy_${POLICY}_${RID}.log" 2>&1 &
PROXY=$!
sleep 3

PROMPT="Read TASK.md in this directory and fully solve the task. Write all required output files to $WS/output/. Implement code, run it, verify the outputs exist and are correct, and fix any problems. When done, stop."

cd "$WS"
ANTHROPIC_BEDROCK_BASE_URL="http://127.0.0.1:$PORT" \
  timeout 900 claude --dangerously-skip-permissions --add-dir "$WS" -p "$PROMPT" \
  > "/tmp/ccrouted/${TASK}_${POLICY}_${RID}.cc.log" 2>&1
CC_EXIT=$?
kill $PROXY 2>/dev/null

# verify with official tests
rm -f /app/output/* 2>/dev/null
cp "$WS/output/"* /app/output/ 2>/dev/null
VOUT=$(uvx -p 3.11 --with pytest==8.4.1 --with pytest-json-ctrf==0.3.5 --with numpy --with scipy --with pandas --with plotly \
  pytest "$TASKDIR/tests/test_outputs.py" --tb=no -q 2>&1)
PASS=$(echo "$VOUT" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' | head -1); PASS=${PASS:-0}
FAIL=$(echo "$VOUT" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' | head -1); FAIL=${FAIL:-0}
TOTAL=$((PASS+FAIL))
# tally routed calls
python3 - "$LOG" "$TASK" "$POLICY" "$RID" "$PASS" "$TOTAL" "$CC_EXIT" << 'PY'
import sys,json
log,task,policy,rid,p,tot,ccx=sys.argv[1:8]
served={}; n=0
try:
    for line in open(log):
        d=json.loads(line); served[d['served']]=served.get(d['served'],0)+1; n+=1
except FileNotFoundError: pass
reward=int(p)/int(tot) if int(tot) else 0.0
rec={'task':task,'policy':policy,'run':int(rid),'reward':reward,'passed':int(p),'total':int(tot),
     'cc_exit':int(ccx),'n_model_calls':n,'served_mix':served}
print(json.dumps(rec))
open(f'/tmp/ccrouted/{task}_{policy}_{rid}.result.json','w').write(json.dumps(rec,indent=2))
PY
