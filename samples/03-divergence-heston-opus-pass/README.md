# 03-divergence-heston-opus-pass

**Divergence regime — the strong side.** Opus 4.6 solves Heston MC pricing cleanly: 14 steps, 0 tool errors, one decisive 1,852-token `Write`. Compare directly with sample 04 (same task, Haiku). This is the trace a router should preserve on the strong model.

| field | value |
|-------|-------|
| task | `heston-mc-pricing` |
| agent | claude-code (v2.1.126) |
| model | `claude-opus-4-6` |
| ATIF schema | ATIF-v1.2 |
| reward | **1.0** |
| steps | 14 |
| tool calls | Bash×4, Read×2, Glob×2, Write×1 |
| total prompt tokens | 222,151 |
| total completion tokens | 2,589 |
| model cost (USD) | $1.175 |
| category | divergence |

## Files
- `trajectory.json` — ATIF agent trace (14 steps; 8 carry per-step tokens)
- `result.json` — Harbor result (reward, tokens, cost)
- `ctrf.json` — per-test pass/fail breakdown- `meta.json` — derived stats (this table)

Source trial: `fb-v10-opus46-heston-mc-pricing` (when2buy/fb-bench-tracker)
