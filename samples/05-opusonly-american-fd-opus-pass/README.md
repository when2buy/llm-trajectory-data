# 05-opusonly-american-fd-opus-pass

**Opus-only regime.** American-option finite-difference (Crank-Nicolson + PSOR) is hard; only the strong model passes. Opus 4.7 solves it in 21 steps for $3.12. Routing action: do not downgrade.

| field | value |
|-------|-------|
| task | `american-option-fd-new` |
| agent | claude-code (v2.1.128) |
| model | `claude-opus-4-7` |
| ATIF schema | ATIF-v1.2 |
| reward | **1.0** |
| steps | 21 |
| tool calls | Bash×8, Write×1 |
| total prompt tokens | 271,215 |
| total completion tokens | 116,871 |
| model cost (USD) | $3.115 |
| category | opus-only |

## Files
- `trajectory.json` — ATIF agent trace (21 steps; 10 carry per-step tokens)
- `result.json` — Harbor result (reward, tokens, cost)
- `ctrf.json` — per-test pass/fail breakdown- `meta.json` — derived stats (this table)

Source trial: `fb-v12-opus47-american-option-fd-new` (when2buy/fb-bench-tracker)
