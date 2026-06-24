# 07-codex-bl-regime-hmm

**Codex agent trace.** gpt-5.3-codex solves a Black-Litterman / regime-HMM task (reward 1.0). Note the schema differences vs claude-code: **no per-step token split** (only run totals), tool name folded into `message`, but `reasoning_output_tokens` and per-step cache tokens are exposed. See schema spec §5.

| field | value |
|-------|-------|
| task | `bl-regime-hmm` |
| agent | codex (v0.128.0) |
| model | `gpt-5.3-codex` |
| ATIF schema | ATIF-v1.5 |
| reward | **1.0** |
| steps | 13 |
| tool calls | — |
| total prompt tokens | 90,014 |
| total completion tokens | 4,470 |
| model cost (USD) | $0.106 |
| category | codex |

## Files
- `trajectory.json` — ATIF agent trace (13 steps; 0 carry per-step tokens)
- `result.json` — Harbor result (reward, tokens, cost)
- `ctrf.json` — per-test pass/fail breakdown- `meta.json` — derived stats (this table)

Source trial: `fb-codex-53c-r2-bl-regime-hmm` (when2buy/fb-bench-tracker)
