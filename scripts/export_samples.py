#!/usr/bin/env python3
"""
Export & sanitize curated trajectory samples for the public llm-trajectory-data repo.

Source: when2buy/fb-bench-tracker trials (Harbor + QuantitativeFinance-Bench).
For each sample we copy:
  - trajectory.json   (ATIF agent trace — the core artifact)
  - result.json       (Harbor result: reward, tokens, cost)
  - ctrf.json         (per-test pass/fail, if present)
and emit a per-sample meta.json with derived routing-relevant stats.

Sanitization: strip absolute machine paths (/sensei-fs-3/users/<user>/...) and
session ids, leaving the data structure & content fully intact for study.
"""
import json, os, re, shutil, hashlib

SRC = '/mnt/localssd/token-router/fb-bench-tracker/trials'
DST = os.path.join(os.path.dirname(__file__), '..', 'samples')

# (sample_dir, source_trial, label, expected_reward)
SAMPLES = [
    ('01-freewin-black-scholes-haiku',      'fb-v10-h45-black-scholes-pricing',      'free-win',    1.0),
    ('02-freewin-sma-opus',                 'fb-v10-opus46-sma-crossover-spy',       'free-win',    1.0),
    ('03-divergence-heston-opus-pass',      'fb-v10-opus46-heston-mc-pricing',       'divergence',  1.0),
    ('04-divergence-heston-haiku-fail',     'fb-v10-h45-heston-mc-pricing',          'divergence',  0.0),
    ('05-opusonly-american-fd-opus-pass',   'fb-v12-opus47-american-option-fd-new',  'opus-only',   1.0),
    ('06-opusonly-american-fd-haiku-fail',  'fb-v10-h45-american-option-fd-new',     'opus-only',   0.0),
    ('07-codex-bl-regime-hmm',              'fb-codex-53c-r2-bl-regime-hmm',          'codex',       1.0),
    ('08-codex-zero-coupon',                'fb-codex-55-zero-coupon-bootstrapping',  'codex',       1.0),
]

PATH_RE = re.compile(r'/sensei-fs-3/users/[^/"\s]+')

SECRET_KEY_RE = re.compile(r'(TOKEN|SECRET|KEY|PASSWORD|BEARER|API|AUTH)', re.I)

def sanitize(obj):
    """Recursively strip machine paths and neutralize any secret-bearing env values."""
    if isinstance(obj, str):
        return PATH_RE.sub('/workspace', obj)
    if isinstance(obj, list):
        return [sanitize(x) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, str) and SECRET_KEY_RE.search(str(k)):
                out[k] = '<redacted>'
            else:
                out[k] = sanitize(v)
        return out
    return obj

def derive_stats(traj, result):
    steps = traj.get('steps', [])
    fm = traj.get('final_metrics', {}) or {}
    agent = traj.get('agent', {}) or {}
    # per-step token presence
    perstep = [s for s in steps if (s.get('metrics') or {}).get('completion_tokens') is not None]
    tool_steps = [s for s in steps if (s.get('extra') or {}).get('tool_use_name')
                  or 'raw_arguments' in (s.get('extra') or {})]
    from collections import Counter
    tools = Counter((s.get('extra') or {}).get('tool_use_name') for s in steps
                    if (s.get('extra') or {}).get('tool_use_name'))
    reward = ((result.get('verifier_result') or {}).get('rewards', {}) or {}).get('reward')
    ar = result.get('agent_result') or {}
    return {
        'agent': agent.get('name'),
        'agent_version': agent.get('version'),
        'model': agent.get('model_name'),
        'atif_schema_version': traj.get('schema_version'),
        'n_steps': len(steps),
        'n_steps_with_per_step_tokens': len(perstep),
        'n_tool_steps': len(tool_steps),
        'tools_used': dict(tools),
        'reward': reward,
        'total_prompt_tokens': fm.get('total_prompt_tokens'),
        'total_completion_tokens': fm.get('total_completion_tokens'),
        'total_cached_tokens': fm.get('total_cached_tokens'),
        'reasoning_output_tokens': (fm.get('extra') or {}).get('reasoning_output_tokens'),
        'total_cost_usd': fm.get('total_cost_usd') or ar.get('cost_usd'),
    }

def main():
    index = []
    for sdir, trial, label, exp_reward in SAMPLES:
        src = os.path.join(SRC, trial)
        out = os.path.join(DST, sdir)
        os.makedirs(out, exist_ok=True)

        traj = json.load(open(os.path.join(src, 'agent', 'trajectory.json')))
        result = json.load(open(os.path.join(src, 'result.json')))
        traj_s, result_s = sanitize(traj), sanitize(result)

        json.dump(traj_s, open(os.path.join(out, 'trajectory.json'), 'w'), indent=2)
        json.dump(result_s, open(os.path.join(out, 'result.json'), 'w'), indent=2)

        ctrf_path = os.path.join(src, 'verifier', 'ctrf.json')
        if os.path.exists(ctrf_path):
            ctrf = sanitize(json.load(open(ctrf_path)))
            json.dump(ctrf, open(os.path.join(out, 'ctrf.json'), 'w'), indent=2)

        stats = derive_stats(traj, result)
        stats['category'] = label
        stats['task'] = result.get('task_name')
        stats['source_trial'] = trial
        assert stats['reward'] is not None and abs(stats['reward'] - exp_reward) < 1e-6, f"reward mismatch {sdir}"
        json.dump(stats, open(os.path.join(out, 'meta.json'), 'w'), indent=2)

        index.append({'dir': sdir, **stats})
        print(f"  ✓ {sdir:<38} {stats['agent']:<11} {stats['model']:<28} "
              f"reward={stats['reward']} steps={stats['n_steps']} ${stats['total_cost_usd']:.3f}")

    json.dump(index, open(os.path.join(DST, '..', 'docs', 'sample_index.json'), 'w'), indent=2)
    print(f"\nExported {len(index)} samples → {DST}")

if __name__ == '__main__':
    main()
