#!/usr/bin/env python3
"""
L3 single-turn counterfactual swap — focused on the decisive codegen step.

For american-option-fd-new the entire reward is determined by ONE step: the
`Write /app/solve.py` (step 12) in the Opus trajectory that scored 1.0. Earlier steps
are environment probing (ls, import checks); later steps just run the script.

Experiment:
  - Reconstruct, BYTE-FOR-BYTE, the exact context Opus saw at step 12:
    system prompt + instruction + every prior agent message + every tool_use block +
    every real tool_result (pulled from tool_result_metadata.stdout).
  - At step 12, call a CANDIDATE model (opus control / sonnet / haiku) with the SAME
    context + SAME tools, forcing it to produce the solve.py.
  - Extract its solve.py, run it, run the OFFICIAL verifier → reward.
  - Repeat N times per model to estimate non-determinism.

This isolates the question: holding the full prior context fixed, can a cheaper model
produce an equally-correct solution at THIS step? That is "safe routability" for the step.
"""
import json, os, re, ast, shutil, subprocess, sys, uuid
import boto3
from botocore.config import Config

REGION='us-west-2'
BEDROCK=boto3.client('bedrock-runtime', region_name=REGION,
                     config=Config(read_timeout=180, connect_timeout=20,
                                   retries={'max_attempts':3,'mode':'standard'}))
MODELS={
 'opus':   'us.anthropic.claude-opus-4-8',
 'sonnet': 'us.anthropic.claude-sonnet-4-6',
 'haiku':  'us.anthropic.claude-haiku-4-5-20251001-v1:0',
}
PRICE={'opus':{'in':15,'out':75},'sonnet':{'in':3,'out':15},'haiku':{'in':0.8,'out':4}}

ROOT='/mnt/localssd/token-router'
TASK_DIR=f'{ROOT}/finance-bench/tasks'
TRIALS=f'{ROOT}/fb-bench-tracker/trials'
APP_OUTPUT='/app/output'

TOOLS=[
 {"name":"Bash","description":"Run a bash command.","input_schema":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}},
 {"name":"Write","description":"Write content to a file.","input_schema":{"type":"object","properties":{"file_path":{"type":"string"},"content":{"type":"string"}},"required":["file_path","content"]}},
 {"name":"Read","description":"Read a file.","input_schema":{"type":"object","properties":{"file_path":{"type":"string"}},"required":["file_path"]}},
]
SYSTEM=("You are a coding agent working in /app. Data is in /app/, write outputs to "
        "/app/output/. Implement the solution as /app/solve.py and run it. Be precise.")

def parse_raw(raw):
    if isinstance(raw,dict): return raw
    try: return json.loads(raw)
    except:
        try: return ast.literal_eval(raw)
        except: return {}

def build_context(traj, upto_step):
    """Reconstruct the Anthropic messages list up to (not including) the target step,
    using real tool_use blocks + real tool results."""
    msgs=[]
    pending_assistant=[]   # content blocks for current assistant turn
    pending_tool_results=[]
    def flush_assistant():
        nonlocal pending_assistant
        if pending_assistant:
            msgs.append({'role':'assistant','content':pending_assistant})
            pending_assistant=[]
    def flush_results():
        nonlocal pending_tool_results
        if pending_tool_results:
            msgs.append({'role':'user','content':pending_tool_results})
            pending_tool_results=[]

    for s in traj['steps']:
        sid=s['step_id']
        if sid>=upto_step: break
        src=s['source']; e=s.get('extra',{}) or {}; m=s.get('metrics') or {}
        msg=str(s.get('message','') or '')
        tool=e.get('tool_use_name')
        if src=='user' and sid==1:
            msgs.append({'role':'user','content':msg}); continue
        if src=='agent':
            if tool:
                # this step is a tool_use (+ its result embedded)
                flush_results()
                args=parse_raw(e.get('raw_arguments'))
                tuid=f"tool_{sid}"
                pending_assistant.append({'type':'tool_use','id':tuid,'name':tool,'input':args})
                flush_assistant()
                # the real observation
                trm=(e.get('tool_result_metadata') or {}).get('tool_use_result',{})
                obs=trm.get('stdout','') if isinstance(trm,dict) else ''
                pending_tool_results.append({'type':'tool_result','tool_use_id':tuid,
                                             'content':obs[:4000] if obs else '(no output)'})
            else:
                # pure assistant text (reasoning / plan)
                if m.get('completion_tokens') and msg.strip():
                    flush_results()
                    pending_assistant.append({'type':'text','text':msg})
    flush_results(); flush_assistant()
    # ensure ends on user turn so model can respond
    if not msgs or msgs[-1]['role']!='user':
        msgs.append({'role':'user','content':'Now implement /app/solve.py to solve the task, then run it.'})
    return msgs

def extract_solve(resp):
    for b in resp.get('content',[]):
        if b.get('type')=='tool_use' and b.get('name')=='Write':
            inp=b.get('input',{})
            if (inp.get('file_path','')).endswith('.py'):
                return inp.get('content','')
    # fallback: code fence in text
    txt=''.join(b.get('text','') for b in resp.get('content',[]) if b.get('type')=='text')
    m=re.findall(r'```python\n(.*?)```',txt,re.DOTALL)
    return max(m,key=len) if m else None

def run_solution(code, task, rid):
    ws=f'/tmp/l3swap/{task}_{rid}'
    if os.path.exists(ws): shutil.rmtree(ws)
    os.makedirs(f'{ws}/output',exist_ok=True)
    code=code.replace('/app/output',f'{ws}/output').replace('/app',ws)
    open(f'{ws}/solve.py','w').write(code)
    try:
        r=subprocess.run(['uvx','-p','3.11','--with','numpy','--with','scipy','--with','matplotlib',
                          '--with','numba','--with','pandas','python','solve.py'],
                         cwd=ws,capture_output=True,text=True,timeout=240)
        run_ok=r.returncode==0
        run_tail=(r.stdout+r.stderr)[-200:]
    except subprocess.TimeoutExpired:
        # non-convergent / infinite-loop solution — counts as a failed run (reward 0)
        return {'run_ok':False,'passed':0,'failed':0,'total':54,'reward':0.0,
                'run_tail':'TIMEOUT >240s (non-convergent solution)'}
    # verify
    if os.path.isdir(APP_OUTPUT):
        for f in os.listdir(APP_OUTPUT):
            fp=os.path.join(APP_OUTPUT,f)
            if os.path.isfile(fp): os.remove(fp)
    od=f'{ws}/output'
    if os.path.isdir(od):
        for f in os.listdir(od):
            sp=os.path.join(od,f)
            if os.path.isfile(sp): shutil.copy2(sp,APP_OUTPUT)
    test=f'{TASK_DIR}/{task}/tests/test_outputs.py'
    v=subprocess.run(['uvx','-p','3.11','--with','pytest==8.4.1','--with','pytest-json-ctrf==0.3.5',
                      '--with','numpy','--with','scipy','--with','pandas','pytest',test,'--tb=no','-q'],
                     capture_output=True,text=True,timeout=240)
    passed=len(re.findall(r'PASSED|passed',v.stdout))
    p=len(re.findall(r' PASSED',v.stdout)); f=len(re.findall(r' FAILED',v.stdout))
    mtot=re.search(r'(\d+) passed',v.stdout); mfail=re.search(r'(\d+) failed',v.stdout)
    np_=int(mtot.group(1)) if mtot else 0; nf=int(mfail.group(1)) if mfail else 0
    total=np_+nf
    return {'run_ok':run_ok,'passed':np_,'failed':nf,'total':total,
            'reward':np_/total if total else 0.0,'run_tail':(r.stdout+r.stderr)[-200:]}

def call(model, msgs, max_tokens=12000):
    body={'anthropic_version':'bedrock-2023-05-31','max_tokens':max_tokens,
          'system':SYSTEM,'messages':msgs,'tools':TOOLS,
          'tool_choice':{'type':'any'}}
    r=BEDROCK.invoke_model(modelId=MODELS[model],body=json.dumps(body))
    return json.loads(r['body'].read())

def experiment(trial, task, target_step, models, n_runs):
    traj=json.load(open(f'{TRIALS}/{trial}/agent/trajectory.json'))
    msgs=build_context(traj, target_step)
    print(f"context: {len(msgs)} messages reconstructed up to step {target_step}",flush=True)
    results=[]
    for model in models:
        for run in range(n_runs):
            rid=f'{model}_{run}_{uuid.uuid4().hex[:6]}'
            print(f"  {model} run{run}: calling...",flush=True)
            try:
                resp=call(model,msgs)
            except Exception as ex:
                print(f"  {model} run{run}: API ERR {str(ex)[:80]}",flush=True); continue
            u=resp.get('usage',{})
            cost=(u.get('input_tokens',0)*PRICE[model]['in']+u.get('output_tokens',0)*PRICE[model]['out'])/1e6
            code=extract_solve(resp)
            if not code:
                print(f"  {model} run{run}: NO CODE (out={u.get('output_tokens')})",flush=True)
                results.append({'model':model,'run':run,'reward':0.0,'has_code':False,'cost':cost}); continue
            v=run_solution(code,task,rid)
            print(f"  {model} run{run}: reward={v['reward']:.3f} ({v['passed']}/{v['total']}) run_ok={v['run_ok']} code={len(code)}b ${cost:.4f}",flush=True)
            if not v['run_ok']: print(f"      run_tail: {v['run_tail'][:120]}")
            results.append({'model':model,'run':run,'has_code':True,'cost':cost,**v})
    return results

if __name__=='__main__':
    trial='fb-v12-opus47-american-option-fd-new'; task='american-option-fd-new'; step=12
    models=sys.argv[1].split(',') if len(sys.argv)>1 else ['opus','sonnet','haiku']
    n=int(sys.argv[2]) if len(sys.argv)>2 else 2
    res=experiment(trial,task,step,models,n)
    os.makedirs(f'{ROOT}/llm-trajectory-data/docs',exist_ok=True)
    json.dump(res,open(f'{ROOT}/llm-trajectory-data/docs/l3_swap_results.json','w'),indent=2)
    print("\n=== SUMMARY (single-turn swap at decisive codegen step) ===")
    for model in models:
        rs=[r for r in res if r['model']==model]
        if not rs: continue
        avg=sum(r['reward'] for r in rs)/len(rs)
        pr=sum(1 for r in rs if r['reward']>=0.99)/len(rs)
        ac=sum(r['cost'] for r in rs)/len(rs)
        print(f"  {model:<7} avg_reward={avg:.3f} pass_rate={pr:.0%} avg_step_cost=${ac:.4f} (n={len(rs)})")
