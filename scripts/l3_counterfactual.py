#!/usr/bin/env python3
"""
L3 — SINGLE-TURN COUNTERFACTUAL SWAP (the only solid test of "safe routability").

Question we answer:  in an Opus trajectory that scored reward=1.0, which individual
steps can be swapped to a cheaper model WITHOUT losing the final reward?

Method (per Steve's methodology — reconstruct the run, swap one turn, re-score):
  1. Load a real Opus trajectory that passed (reward=1.0). Replay its tool calls
     deterministically to regenerate the exact /app working state, step by step.
  2. For a chosen step K (a model-call step):
       - rebuild the EXACT context the original model saw at step K
         (byte-for-byte: same system prompt + full prior messages + tool results)
       - call the CANDIDATE model (haiku/sonnet) for step K only
       - from K+1 onward, in ISOLATED mode: splice back the original Opus actions
         (so we measure the effect of swapping THAT step alone)
         in CONTINUE mode: let the candidate keep driving to the end
       - run the OFFICIAL verifier → reward_K
  3. Compare reward_K vs 1.0 → label step K  {SAFE | UNSAFE}.

This file builds the harness incrementally. Stage 1 (this commit): faithfully replay an
Opus trajectory's tool calls to reproduce reward=1.0 with NO model in the loop — proving
the environment reconstruction is correct before we introduce swaps.
"""
import json, os, re, shutil, subprocess, sys

ROOT = '/mnt/localssd/token-router'
TASK_DIR = f'{ROOT}/finance-bench/tasks'
TRIALS = f'{ROOT}/fb-bench-tracker/trials'
APP_OUTPUT = '/app/output'

def load_traj(trial):
    return json.load(open(f'{TRIALS}/{trial}/agent/trajectory.json'))

def extract_actions(traj):
    """Pull the ordered tool calls (Write/Edit/Bash) with their exact arguments
    from an ATIF trajectory. These are the ground-truth actions that produced reward=1.0."""
    actions = []
    for s in traj['steps']:
        e = s.get('extra', {}) or {}
        tool = e.get('tool_use_name')
        raw = e.get('raw_arguments')
        if not tool or raw is None:
            continue
        # raw_arguments is a python-repr-ish dict string OR a dict
        args = raw if isinstance(raw, dict) else _parse_raw(raw)
        actions.append({'step_id': s.get('step_id'), 'tool': tool, 'args': args})
    return actions

def _parse_raw(raw):
    """raw_arguments may be a str like \"{'command': 'ls'}\". Try json then ast."""
    import ast
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        try:
            return ast.literal_eval(raw)
        except Exception:
            return {}

def setup_workspace(task, run_id):
    ws = f'/tmp/l3/{task}_{run_id}'
    if os.path.exists(ws): shutil.rmtree(ws)
    os.makedirs(f'{ws}/output', exist_ok=True)
    # copy any task data files (most quant tasks embed params in the instruction; some ship data)
    data = f'{TASK_DIR}/{task}/environment/data'
    if os.path.isdir(data):
        for f in os.listdir(data):
            src = os.path.join(data, f)
            if os.path.isfile(src): shutil.copy2(src, ws)
    return ws

def exec_action(act, ws):
    """Execute one ground-truth action in the workspace. Returns (ok, output)."""
    tool, args = act['tool'], act['args']
    if tool == 'Write':
        path = (args.get('file_path') or '').replace('/app', ws)
        if not path: return False, 'no file_path'
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'w').write(args.get('content', ''))
        return True, f'wrote {len(args.get("content",""))}b'
    if tool == 'Edit':
        path = (args.get('file_path') or '').replace('/app', ws)
        if not os.path.isfile(path): return False, 'edit: file missing'
        txt = open(path).read()
        old, new = args.get('old_string', ''), args.get('new_string', '')
        if old not in txt: return False, 'edit: old_string not found'
        open(path, 'w').write(txt.replace(old, new, 1))
        return True, 'edited'
    if tool == 'Bash':
        cmd = (args.get('command') or '').replace('/app', ws)
        if not cmd: return False, 'empty cmd'
        # run heavy python via uvx for sci deps; plain bash otherwise
        if 'python' in cmd and ('solve' in cmd or '.py' in cmd):
            cmd = re.sub(r'\bpython3?\b',
                         'uvx -p 3.11 --with numpy --with scipy --with matplotlib --with pandas python',
                         cmd, count=1)
        r = subprocess.run(['bash','-c', f'cd {ws} && {cmd}'],
                           capture_output=True, text=True, timeout=180)
        return r.returncode == 0, (r.stdout + r.stderr)[-400:]
    # Read/Glob/etc: no state change
    return True, '(noop)'

def verify(ws, task):
    """Run the official verifier against this workspace's output."""
    # isolate /app/output
    if os.path.isdir(APP_OUTPUT):
        for f in os.listdir(APP_OUTPUT):
            fp = os.path.join(APP_OUTPUT, f)
            if os.path.isfile(fp): os.remove(fp)
    for f in os.listdir(f'{ws}/output'):
        src = os.path.join(ws, 'output', f)
        if os.path.isfile(src): shutil.copy2(src, APP_OUTPUT)
    test = f'{TASK_DIR}/{task}/tests/test_outputs.py'
    r = subprocess.run(
        ['uvx','-p','3.11','--with','pytest==8.4.1','--with','pytest-json-ctrf==0.3.5',
         '--with','numpy','--with','scipy','--with','pandas',
         'pytest', test, '-rA','--tb=no','-q'],
        capture_output=True, text=True, timeout=240)
    out = r.stdout
    passed = len(re.findall(r'PASSED', out))
    failed = len(re.findall(r'FAILED', out))
    total = passed + failed
    return {'passed': passed, 'failed': failed, 'total': total,
            'reward': passed/total if total else 0.0, 'tail': out[-300:]}

def replay_only(trial, task):
    """Stage 1: replay ground-truth actions, NO model. Must reproduce reward=1.0."""
    traj = load_traj(trial)
    actions = extract_actions(traj)
    ws = setup_workspace(task, 'replay')
    print(f"replaying {len(actions)} actions from {trial}")
    for a in actions:
        ok, msg = exec_action(a, ws)
        flag = 'ok ' if ok else 'ERR'
        print(f"  step {a['step_id']:>3} {a['tool']:<6} [{flag}] {msg[:70]}")
    res = verify(ws, task)
    print(f"\nREPLAY reward = {res['reward']:.3f}  ({res['passed']}/{res['total']})")
    print(res['tail'])
    return res

if __name__ == '__main__':
    trial = sys.argv[1] if len(sys.argv) > 1 else 'fb-v12-opus47-american-option-fd-new'
    task  = sys.argv[2] if len(sys.argv) > 2 else 'american-option-fd-new'
    replay_only(trial, task)
