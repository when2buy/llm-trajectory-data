# 08-codex-zero-coupon

**Codex agent trace (second example).** gpt-5.4 solves zero-coupon bootstrapping (reward 1.0). Same ATIF-v1.5 fidelity caveats as sample 07 — included so the team can see the Codex format on more than one task.

| field | value |
|-------|-------|
| task | `zero-coupon-bootstrapping` |
| agent | codex (v0.128.0) |
| model | `gpt-5.4` |
| ATIF schema | ATIF-v1.5 |
| reward | **1.0** |
| steps | 13 |
| tool calls | — |
| total prompt tokens | 59,322 |
| total completion tokens | 4,877 |
| model cost (USD) | $0.109 |
| category | codex |

## Files
- `trajectory.json` — ATIF agent trace (13 steps; 0 carry per-step tokens)
- `result.json` — Harbor result (reward, tokens, cost)
- `ctrf.json` — per-test pass/fail breakdown- `meta.json` — derived stats (this table)

Source trial: `fb-codex-55-zero-coupon-bootstrapping` (when2buy/fb-bench-tracker)
