# Live routing — intervening mid-execution (hand-written loop — superseded)

> ⚠️ **Superseded by [real-claude-code-routing.md](./real-claude-code-routing.md).** This
> version used a *hand-written* agent loop and *hand-assembled* per-turn context — both
> flawed. In particular the "`light_haiku` is worst" result here was later traced to a
> model-incompatible-request bug (mixing models forwarded Opus-only fields that Haiku
> rejects), **not** a real property of routing. When the real Claude Code is routed through
> a proxy (which never touches the prompt), downgrading to Haiku does *not* hurt reward on
> this task. Read the real-Claude-Code writeup for the trustworthy result; this file is kept
> for history.

Everything before this doc (L1/L2 step classification, even the L3 single-step swap) worked
from **recorded** trajectories. This experiment does not. It runs a **live agent loop** on a
real QFBench task — the model emits a tool call, we **actually execute it**, feed the **real
result** back, and loop — and a **routing policy chooses the model per turn, mid-run**. The
chosen model drives with its *own* actions, so errors propagate for real. At the end we run
the **official 20-test verifier**.

This is the setup that can actually answer "which turns can be routed?" — you cannot infer
it by reading a recording and guessing that a "read a file" step looks simple.

## Policies tested (task: `sma-crossover-spy`)

| policy | reward | cost (per run) | model mix | verdict |
|--------|:------:|:--------------:|-----------|---------|
| **all_opus** | 1.0 / 1.0 | $2.86 / $3.01 | Opus only | quality & cost ceiling |
| **all_sonnet** | 1.0 / 1.0 | $0.62 / $0.40 | Sonnet only | full reward, ~85% cheaper |
| **all_haiku** | 1.0 | **$0.32** | Haiku only | **full reward, ~89% cheaper — best** |
| **light_haiku** | 0.85 / 1.0 | $1.71 / $1.82 | Opus 7 + Haiku 13–18 (switches per turn) | ❌ worst: costlier *and* lower reward |
| **escalate** | 1.0 / 1.0 | $2.63 / $2.93 | Haiku 1 + Opus 17 | degenerates to all_opus |

`light_haiku` = the "route the light turns to Haiku" hypothesis, decided live: turn 0 and
any turn following a codegen/errored turn → Opus; otherwise → Haiku. `escalate` = start on
Haiku, switch to Opus for the rest once any turn errors.

## What the experiment proved (and what it corrected)

**1. Inferring "this step is simple" from the trace is unreliable — proven, not asserted.**
The `light_haiku` policy, which is exactly the "downgrade the light/dispatch turns" idea
from L1, was the **worst** policy: ~5× the cost of all_haiku *and* lower reward. Switching
models mid-trajectory forces one model to continue on another's divergent solving path; the
"thoughts" don't line up and quality drops. So the earlier L1/L2 suggestion — "route the
light steps to a cheap model" — **does not survive a live test**. This is the correction the
experiment forces on our earlier inference-based framing.

**2. On an easy task, the winning move is "all cheap," not "mix."** all_haiku got full
reward at $0.32 — 89% cheaper than all_opus and cheaper than any mixed policy. Per-turn
routing *added* cost and risk here.

**3. Routability is strongly task-difficulty dependent.** This is the opposite conclusion to
the hard-task L3 result (`american-option-fd-new`), where Haiku could not produce the
decisive codegen step at all (reward 1.0 → 0.21). Easy task → go all-cheap; hard task → the
decisive step needs the strong model. A real router must therefore key off **task/turn
difficulty signal**, not a static "step type" rule.

**4. Naive escalation wastes the savings.** `escalate` flips the *entire rest* of the run to
Opus on the first error, so it ends up ~all-Opus. Real errors happen often (even all_haiku
hit a working-directory error and recovered on its own). The escalation trigger must be
surgical — escalate the *retry of the failing step*, not the whole tail.

## The router design these results actually support

- **Pick the cheapest model that can carry the *whole* task**, decided up front from a
  difficulty estimate, rather than switching models within a single solving chain.
- **Within a model, allow targeted escalation** only on a step that demonstrably fails
  (errored tool call, failed self-check), and only for the retry — not the whole tail.
- **Avoid free-form mid-trajectory model swapping** on a single attempt: divergent solving
  styles make the handoff lossy. (This is why L3-style single-step swaps must splice back to
  the original model — they measure a counterfactual, they are not a deployable policy.)

## Honest limitations

- Two runs per policy — non-determinism is large (see `light_haiku` 0.85 vs 1.0). More runs
  needed for tight numbers; the *ordering* of policies is the robust finding.
- One easy task so far. The hard-task picture (L3) differs — both data points are needed.
- Our agent loop uses a simplified system prompt + 3 tools, not the full Claude Code harness
  (no compaction, fewer tools). Absolute costs are indicative, not production numbers.

## Reproduce

```bash
uvx -p 3.11 --with boto3 python scripts/live_router.py sma-crossover-spy all_haiku 2 25
uvx -p 3.11 --with boto3 python scripts/live_router.py sma-crossover-spy light_haiku 2 25
# results → docs/live_*.json ; summary → docs/live_routing_sma.json
```
