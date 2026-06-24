# 04-divergence-heston-haiku-fail

**Divergence regime — the cautionary side.** Same task as sample 03, on Haiku 4.5. Result: reward 0.0 after **98 steps, 3.1M input tokens, 22 failed `Edit` loops**, costing $3.36 — more than Opus. The lesson: a cheap model on an out-of-depth task is *not* cheap. Routers must detect the thrash (rising errors, Edit loops) and escalate.

| field | value |
|-------|-------|
| task | `heston-mc-pricing` |
| agent | claude-code (v2.1.126) |
| model | `claude-haiku-4-5-20251001` |
| ATIF schema | ATIF-v1.2 |
| reward | **0.0** |
| steps | 98 |
| tool calls | Bash×34, Write×1, Edit×22, Read×1 |
| total prompt tokens | 3,133,827 |
| total completion tokens | 44,319 |
| model cost (USD) | $3.355 |
| category | divergence |

## Files
- `trajectory.json` — ATIF agent trace (98 steps; 59 carry per-step tokens)
- `result.json` — Harbor result (reward, tokens, cost)
- `ctrf.json` — per-test pass/fail breakdown- `meta.json` — derived stats (this table)

Source trial: `fb-v10-h45-heston-mc-pricing` (when2buy/fb-bench-tracker)
