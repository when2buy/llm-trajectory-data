# 01-freewin-black-scholes-haiku

**Free-win regime.** Haiku 4.5 fully solves Black-Scholes pricing (reward 1.0) in 20 steps for $0.14. There is no reason to run this task on a more expensive model — the cheap model is already correct. Routing action: downgrade the whole task.

| field | value |
|-------|-------|
| task | `black-scholes-pricing` |
| agent | claude-code (v2.1.123) |
| model | `claude-haiku-4-5-20251001` |
| ATIF schema | ATIF-v1.2 |
| reward | **1.0** |
| steps | 20 |
| tool calls | Read×4, Write×2, Bash×2 |
| total prompt tokens | 274,863 |
| total completion tokens | 2,631 |
| model cost (USD) | $0.145 |
| category | free-win |

## Files
- `trajectory.json` — ATIF agent trace (20 steps; 10 carry per-step tokens)
- `result.json` — Harbor result (reward, tokens, cost)
- `ctrf.json` — per-test pass/fail breakdown- `meta.json` — derived stats (this table)

Source trial: `fb-v10-h45-black-scholes-pricing` (when2buy/fb-bench-tracker)
