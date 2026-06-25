#!/usr/bin/env python3
"""
LIVE agent loop with mid-execution token routing — the real experiment.

This is NOT replay. The model generates a tool call, we ACTUALLY execute it in an
isolated workspace, feed the REAL result back, and loop — exactly like a Claude-Code
run. A ROUTING POLICY decides, per turn, which model handles that turn. Because the
chosen model drives with its OWN actions, errors propagate for real. At the end we run
the OFFICIAL QFBench verifier to get the true reward.

Policies:
  all_opus     every turn -> Opus            (quality ceiling, cost ceiling)
  all_haiku    every turn -> Haiku           (cost floor)
  all_sonnet   every turn -> Sonnet
  light_haiku  decide PER TURN from live context: if the model's PREVIOUS turn was a
               heavy/codegen turn or this is turn 0 planning -> Opus; otherwise (the
               turn is dispatch/observation glue) -> Haiku.  <-- the "route the light
               turns" hypothesis, tested live instead of inferred from a recording.
  escalate     start on Haiku; if a turn errors or stalls (no tool call / repeated
               failure), escalate that turn (and onward) to Opus.

Usage:
  uvx -p 3.11 --with boto3 python live_router.py <task> <policy> <n_runs> [max_turns]
"""
import json, os, re, shutil, subprocess, sys, uuid, time
import boto3
from botocore.config import Config

REGION='us-west-2'
BR=boto3.client('bedrock-runtime', region_name=REGION,
                config=Config(read_timeout=120, connect_timeout=20,
                              retries={'max_attempts':4,'mode':'adaptive'}))
MODELS={'opus':'us.anthropic.claude-opus-4-8',
        'sonnet':'us.anthropic.claude-sonnet-4-6',
        'haiku':'us.anthropic.claude-haiku-4-5-20251001-v1:0'}
PRICE={'opus':{'in':15,'out':75},'sonnet':{'in':3,'out':15},'haiku':{'in':0.8,'out':4}}

ROOT='/mnt/localssd/token-router'
TASK_DIR=f'{ROOT}/finance-bench/tasks'
APP_OUTPUT='/app/output'

TOOLS=[
 {"name":"Bash","description":"Run a bash command in the working directory. Returns stdout+stderr.",
  "input_schema":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}},
 {"name":"Write","description":"Write content to a file (creates parent dirs).",
  "input_schema":{"type":"object","properties":{"file_path":{"type":"string"},"content":{"type":"string"}},"required":["file_path","content"]}},
 {"name":"Read","description":"Read a file's contents.",
  "input_schema":{"type":"object","properties":{"file_path":{"type":"string"}},"required":["file_path"]}},
]
SYSTEM=("You are an expert quantitative-finance coding agent working in /app. Input data is in "
        "/app/, write all output files to /app/output/. Implement the solution (e.g. /app/solve.py), "
        "run it, inspect outputs, and fix problems until the task is fully solved. Be efficient. "
        "When everything is done and outputs are written, stop.")

def setup_ws(task):
    ws=f'/tmp/live/{task}_{uuid.uuid4().hex[:8]}'
    if os.path.exists(ws): shutil.rmtree(ws)
    os.makedirs(f'{ws}/output',exist_ok=True)
    data=f'{TASK_DIR}/{task}/environment/data'
    if os.path.isdir(data):
        for f in os.listdir(data):
            s=os.path.join(data,f)
            if os.path.isfile(s): shutil.copy2(s,ws)
    return ws

def exec_tool(name, inp, ws):
    if name=='Bash':
        cmd=(inp.get('command') or '').replace('/app',ws)
        if not cmd: return True,'(empty command)'
        if re.search(r'\bpython3?\b',cmd):
            cmd=re.sub(r'\bpython3?\b',
                       'uvx -p 3.11 --with numpy --with scipy --with pandas --with matplotlib --with plotly python',
                       cmd,count=1)
        try:
            r=subprocess.run(['bash','-c',f'cd {ws} && {cmd}'],capture_output=True,text=True,timeout=180)
            out=(r.stdout+(('\n[stderr] '+r.stderr) if r.stderr else ''))[:4000]
            return r.returncode==0,(out or '(no output)')
        except subprocess.TimeoutExpired:
            return False,'[TIMEOUT >180s]'
    if name=='Write':
        path=(inp.get('file_path') or '').replace('/app',ws)
        if not path: return False,'no file_path'
        os.makedirs(os.path.dirname(path),exist_ok=True)
        open(path,'w').write(inp.get('content',''))
        return True,f'wrote {len(inp.get("content",""))} bytes'
    if name=='Read':
        path=(inp.get('file_path') or '').replace('/app',ws)
        try: return True,open(path).read()[:4000]
        except Exception as e: return False,f'error: {e}'
    return False,f'unknown tool {name}'

def pick_model(policy, turn, history):
    """Decide the model for THIS turn from LIVE signal (not from a recording)."""
    if policy in ('all_opus','all_haiku','all_sonnet'):
        return policy.split('_')[1]
    if policy=='light_haiku':
        # turn 0 = planning -> strong. After a turn that wrote code or errored, the next
        # turn is interpreting results = still benefits from strong. Pure dispatch/read
        # turns (prev turn was a short Bash/Read with ok result) -> haiku.
        if turn==0: return 'opus'
        prev=history[-1]
        if prev.get('wrote_code') or prev.get('errored'): return 'opus'
        if prev.get('out_tokens',0)>=800: return 'opus'   # prev was heavy reasoning
        return 'haiku'
    if policy=='escalate':
        # haiku until something goes wrong, then opus for the rest
        if any(h.get('errored') for h in history): return 'opus'
        return 'haiku'
    return 'opus'

def call(model, msgs, max_tokens=8000):
    body={'anthropic_version':'bedrock-2023-05-31','max_tokens':max_tokens,
          'system':SYSTEM,'messages':msgs,'tools':TOOLS}
    r=BR.invoke_model(modelId=MODELS[model],body=json.dumps(body))
    return json.loads(r['body'].read())

def verify(ws, task):
    if os.path.isdir(APP_OUTPUT):
        for f in os.listdir(APP_OUTPUT):
            fp=os.path.join(APP_OUTPUT,f)
            if os.path.isfile(fp): os.remove(fp)
    od=f'{ws}/output'
    if os.path.isdir(od):
        for f in os.listdir(od):
            s=os.path.join(od,f)
            if os.path.isfile(s): shutil.copy2(s,APP_OUTPUT)
    test=f'{TASK_DIR}/{task}/tests/test_outputs.py'
    try:
        v=subprocess.run(['uvx','-p','3.11','--with','pytest==8.4.1','--with','pytest-json-ctrf==0.3.5',
                          '--with','numpy','--with','scipy','--with','pandas','--with','plotly',
                          'pytest',test,'--tb=no','-q'],capture_output=True,text=True,timeout=240)
    except subprocess.TimeoutExpired:
        return {'reward':0.0,'passed':0,'total':0,'note':'verifier timeout'}
    np_=int(re.search(r'(\d+) passed',v.stdout).group(1)) if re.search(r'(\d+) passed',v.stdout) else 0
    nf=int(re.search(r'(\d+) failed',v.stdout).group(1)) if re.search(r'(\d+) failed',v.stdout) else 0
    tot=np_+nf
    return {'reward':np_/tot if tot else 0.0,'passed':np_,'total':tot}

def run_episode(task, policy, max_turns=25, verbose=True):
    ws=setup_ws(task)
    instr=open(f'{TASK_DIR}/{task}/instruction.md').read()
    msgs=[{'role':'user','content':instr}]
    history=[]; cost=0.0; model_seq=[]
    for turn in range(max_turns):
        model=pick_model(policy,turn,history)
        model_seq.append(model)
        try:
            resp=call(model,msgs)
        except Exception as e:
            if verbose: print(f"  T{turn:>2}[{model}] API ERR {str(e)[:50]}",flush=True)
            break
        u=resp.get('usage',{}); it,ot=u.get('input_tokens',0),u.get('output_tokens',0)
        cost+=(it*PRICE[model]['in']+ot*PRICE[model]['out'])/1e6
        content=resp.get('content',[]); stop=resp.get('stop_reason')
        tool_uses=[b for b in content if b.get('type')=='tool_use']
        wrote_code=any(b['name']=='Write' and (b.get('input',{}).get('file_path','')).endswith('.py') for b in tool_uses)
        txt=''.join(b.get('text','') for b in content if b.get('type')=='text')
        # execute tools, collect real results
        msgs.append({'role':'assistant','content':content})
        errored=False; tool_results=[]
        for b in tool_uses:
            ok,out=exec_tool(b['name'],b.get('input',{}),ws)
            if not ok: errored=True
            tool_results.append({'type':'tool_result','tool_use_id':b.get('id'),'content':out,'is_error':not ok})
        tname=','.join(b['name'] for b in tool_uses) or 'text'
        if verbose:
            print(f"  T{turn:>2}[{model:>6}] {tname:<14} out={ot:<5} {'ERR' if errored else '   '} "
                  f"{txt[:42].replace(chr(10),' ')}",flush=True)
        history.append({'turn':turn,'model':model,'out_tokens':ot,'wrote_code':wrote_code,
                        'errored':errored,'tools':[b['name'] for b in tool_uses]})
        if stop=='end_turn' or not tool_uses:
            break
        msgs.append({'role':'user','content':tool_results})
    res=verify(ws,task)
    from collections import Counter
    return {'task':task,'policy':policy,'reward':res['reward'],'passed':res['passed'],
            'total':res['total'],'cost_usd':round(cost,5),'turns':len(history),
            'model_mix':dict(Counter(model_seq)),'workspace':ws}

def main():
    task=sys.argv[1] if len(sys.argv)>1 else 'sma-crossover-spy'
    policy=sys.argv[2] if len(sys.argv)>2 else 'all_haiku'
    n=int(sys.argv[3]) if len(sys.argv)>3 else 1
    max_turns=int(sys.argv[4]) if len(sys.argv)>4 else 25
    print(f"=== LIVE {task} | policy={policy} | runs={n} | max_turns={max_turns} ===",flush=True)
    out=[]
    for i in range(n):
        print(f"\n--- run {i} ---",flush=True)
        r=run_episode(task,policy,max_turns)
        print(f"  => reward={r['reward']:.3f} ({r['passed']}/{r['total']}) cost=${r['cost_usd']} "
              f"turns={r['turns']} mix={r['model_mix']}",flush=True)
        out.append(r)
    path=f'{ROOT}/llm-trajectory-data/docs/live_{task}_{policy}.json'
    json.dump(out,open(path,'w'),indent=2)
    print(f"\nsaved {path}",flush=True)

if __name__=='__main__':
    main()
