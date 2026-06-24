# 02-freewin-sma-opus

**Free-win regime, run on Opus.** SMA-crossover is an easy task; Opus 4.6 solves it (reward 1.0) but at $0.96 — roughly 7× what Haiku costs on a comparable easy task. This is the overpayment a router exists to eliminate.

| field | value |
|-------|-------|
| task | `sma-crossover-spy` |
| agent | claude-code (v2.1.126) |
| model | `claude-opus-4-6` |
| ATIF schema | ATIF-v1.2 |
| reward | **1.0** |
| steps | 14 |
| tool calls | Read×1, Bash×6, Write×1 |
| total prompt tokens | 178,244 |
| total completion tokens | 2,893 |
| model cost (USD) | $0.964 |
| category | free-win |

## Files
- `trajectory.json` — ATIF agent trace (14 steps; 6 carry per-step tokens)
- `result.json` — Harbor result (reward, tokens, cost)
- `ctrf.json` — per-test pass/fail breakdown- `meta.json` — derived stats (this table)

Source trial: `fb-v10-opus46-sma-crossover-spy` (when2buy/fb-bench-tracker)
