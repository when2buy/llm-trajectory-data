#!/usr/bin/env python3
"""
L1 (surface routability) + L2 (cost-saving potential) across ALL claude-code trajectories.

L1: per model-call step, classify HEAVY (codegen/reasoning, completion>=800)
    vs LIGHT (dispatch/glue/read, completion<800). "% light steps" = surface routability.
L2: if every LIGHT step were repriced at Haiku's INPUT rate (the real cost driver in
    multi-turn agents), how much $ would the trajectory save? This is the UPPER BOUND —
    it ignores whether the swap preserves reward (that is L3 / counterfactual).

Codex traces are skipped: they carry no per-step token split (see schema doc §5).
"""
import json, glob, os
from collections import defaultdict

TRIALS = '/mnt/localssd/token-router/fb-bench-tracker/trials'

# Bedrock list price per 1M tokens (input, output)
PRICE = {
    'opus':   {'in': 15.0, 'out': 75.0},
    'sonnet': {'in':  3.0, 'out': 15.0},
    'haiku':  {'in':  0.8, 'out':  4.0},
}
HEAVY_THRESHOLD = 800  # completion tokens

def model_family(model_name):
    m = model_name.lower()
    if 'opus' in m: return 'opus'
    if 'sonnet' in m: return 'sonnet'
    if 'haiku' in m: return 'haiku'
    return None

def analyze(traj):
    fam = model_family(traj.get('agent', {}).get('model_name', ''))
    if not fam: return None
    steps = traj.get('steps', [])
    mc = []  # model calls: (kind, prompt_tokens, completion_tokens)
    for s in steps:
        m = s.get('metrics') or {}
        ct, pt = m.get('completion_tokens'), m.get('prompt_tokens')
        if ct is None: continue  # tool-exec / user step — no model call
        kind = 'HEAVY' if ct >= HEAVY_THRESHOLD else 'light'
        mc.append((kind, pt or 0, ct))
    if not mc: return None

    n_light = sum(1 for k, _, _ in mc if k == 'light')
    n_heavy = len(mc) - n_light
    # actual cost of this trajectory on its own model
    p = PRICE[fam]
    cost_actual = sum(pt * p['in'] + ct * p['out'] for _, pt, ct in mc) / 1e6
    # L2 upper bound: reprice LIGHT steps at Haiku, keep HEAVY on original model
    h = PRICE['haiku']
    cost_routed = 0.0
    for k, pt, ct in mc:
        pr = h if k == 'light' else p
        cost_routed += (pt * pr['in'] + ct * pr['out'])
    cost_routed /= 1e6
    saving = (cost_actual - cost_routed) / cost_actual if cost_actual > 0 else 0
    return dict(fam=fam, n_calls=len(mc), n_light=n_light, n_heavy=n_heavy,
                pct_light_steps=n_light / len(mc),
                cost_actual=cost_actual, cost_routed=cost_routed, saving_frac=saving)

def main():
    by_fam = defaultdict(list)
    n_total = n_used = 0
    for tp in glob.glob(f'{TRIALS}/*/agent/trajectory.json'):
        n_total += 1
        try:
            traj = json.load(open(tp))
        except: continue
        r = analyze(traj)
        if r: by_fam[r['fam']].append(r); n_used += 1

    print(f"scanned {n_total} trajectories, {n_used} usable (claude-code with per-step tokens)\n")
    print(f"{'model':<8}{'n':>6}{'avg %light steps':>18}{'median %light':>15}{'avg L2 saving (light→haiku)':>30}")
    print('-'*77)
    def med(xs):
        xs = sorted(xs); n = len(xs)
        return xs[n//2] if n else 0
    summary = {}
    for fam in ('opus', 'sonnet', 'haiku'):
        rows = by_fam.get(fam, [])
        if not rows: continue
        pl = [r['pct_light_steps'] for r in rows]
        sv = [r['saving_frac'] for r in rows if r['fam'] != 'haiku']
        avg_pl = sum(pl)/len(pl)
        avg_sv = sum(sv)/len(sv) if sv else 0
        summary[fam] = dict(n=len(rows), avg_pct_light=avg_pl, median_pct_light=med(pl), avg_l2_saving=avg_sv)
        print(f"{fam:<8}{len(rows):>6}{avg_pl*100:>17.0f}%{med(pl)*100:>14.0f}%{(avg_sv*100 if sv else 0):>29.0f}%")

    # how many trajectories have AT LEAST ONE light step (i.e. are routable at all)?
    print("\n=== L1 headline: what fraction of trajectories have >=1 routable (light) step? ===")
    for fam in ('opus', 'sonnet', 'haiku'):
        rows = by_fam.get(fam, [])
        if not rows: continue
        any_light = sum(1 for r in rows if r['n_light'] > 0)
        print(f"  {fam:<8}: {any_light}/{len(rows)} = {100*any_light/len(rows):.1f}% have at least one light step")

    json.dump({'summary': summary}, open(os.path.join(os.path.dirname(__file__), '..', 'docs', 'l1_l2_global.json'), 'w'), indent=2)

if __name__ == '__main__':
    main()
