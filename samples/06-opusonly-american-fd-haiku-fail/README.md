# 06-opusonly-american-fd-haiku-fail

**Opus-only regime, cheap-model attempt.** Haiku 4.5 on the same hard task fails (reward 0.0) but gives up cheaply ($0.12, 28 steps) — unlike the Heston case it does not thrash. Useful contrast: failure cost is task-dependent.

| field | value |
|-------|-------|
| task | `american-option-fd-new` |
| agent | claude-code (v2.1.123) |
| model | `claude-haiku-4-5-20251001` |
| ATIF schema | ATIF-v1.2 |
| reward | **0.0** |
| steps | 28 |
| tool calls | Write×5, Bash×7, Edit×3 |
| total prompt tokens | 582,558 |
| total completion tokens | 30,777 |
| model cost (USD) | $0.124 |
| category | opus-only |

## Files
- `trajectory.json` — ATIF agent trace (28 steps; 16 carry per-step tokens)
- `result.json` — Harbor result (reward, tokens, cost)
- `ctrf.json` — per-test pass/fail breakdown- `meta.json` — derived stats (this table)

Source trial: `fb-v10-h45-american-option-fd-new` (when2buy/fb-bench-tracker)
