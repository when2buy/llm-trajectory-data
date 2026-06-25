# What token-routing data looks like — a guided tour of 8 real traces

This report walks through eight real LLM agent execution chains, drawn from the
[Harbor](https://github.com/harbor-framework/harbor) +
[QuantitativeFinance-Bench](https://github.com/beckybyte/QuantitativeFinance-Bench)
benchmark. Each is a complete multi-turn trajectory of a coding agent (Claude Code or
Codex CLI) solving a quant-finance task, scored 0–1 by an automated pytest verifier.

The goal is to show the team **exactly what data we collect, at what granularity, and why
that granularity is the thing that makes token routing / token remap possible.**

For the field-by-field schema, see [`trajectory-schema.md`](./trajectory-schema.md).

---

## The eight samples

| # | sample | agent | model | reward | steps | model cost | what it teaches |
|---|--------|-------|-------|:------:|:-----:|:----------:|-----------------|
| 1 | [black-scholes](../samples/01-freewin-black-scholes-haiku) | claude-code | Haiku 4.5 | **1.0** | 20 | $0.14 | **Free win** — cheap model fully solves it |
| 2 | [sma-crossover](../samples/02-freewin-sma-opus) | claude-code | Opus 4.6 | 1.0 | 14 | $0.96 | Free win run on Opus = overpaying 7× |
| 3 | [heston-mc](../samples/03-divergence-heston-opus-pass) | claude-code | Opus 4.6 | **1.0** | 14 | $1.18 | **Divergence** — strong model, clean solve |
| 4 | [heston-mc](../samples/04-divergence-heston-haiku-fail) | claude-code | Haiku 4.5 | **0.0** | 98 | $3.36 | Divergence — weak model flails *and costs more* |
| 5 | [american-option-fd](../samples/05-opusonly-american-fd-opus-pass) | claude-code | Opus 4.7 | 1.0 | 21 | $3.12 | **Opus-only** — hard task, only strong model passes |
| 6 | [american-option-fd](../samples/06-opusonly-american-fd-haiku-fail) | claude-code | Haiku 4.5 | 0.0 | 28 | $0.12 | Opus-only — cheap model gives up cheap |
| 7 | [bl-regime-hmm](../samples/07-codex-bl-regime-hmm) | codex | gpt-5.3-codex | 1.0 | 13 | $0.11 | **Codex** trace — different schema fidelity |
| 8 | [zero-coupon](../samples/08-codex-zero-coupon) | codex | gpt-5.4 | 1.0 | 13 | $0.11 | Codex trace — second example |

Every sample directory contains:
- `trajectory.json` — the ATIF agent trace (the core artifact)
- `result.json` — Harbor result: reward, token totals, cost
- `ctrf.json` — per-test pass/fail breakdown (where available)
- `meta.json` — derived routing-relevant stats

---

## Routability is per-STEP, not per-task — ~100% of trajectories are routable

> **Correction / sharpening (this is the important part).** An earlier version of this
> report framed routability at the **task** level ("~14% of tasks are free wins"). That is
> the wrong unit. Routing happens **per step**, and at the step level *almost every
> trajectory is routable* — because even a hard, Opus-only task is mostly cheap glue steps
> ("read this file", "now run it", "check the output") with a few decisive reasoning/codegen
> steps in between.

Measured across **2,859 real claude-code trajectories** (per-step token data; Codex traces
excluded — they lack it, see schema §5):

| model | trajectories | avg % "light" steps | median | **100% have ≥1 routable step?** |
|-------|:---:|:---:|:---:|:---:|
| Opus | 1,018 | **76%** | 80% | ✅ yes |
| Sonnet | 1,053 | 80% | 81% | ✅ yes |
| Haiku | 788 | 81% | 82% | ✅ yes |

A *light* step = a model call emitting <800 completion tokens (dispatch / read / glue). A
*heavy* step = codegen/reasoning (≥800). **Every single trajectory has at least one light
step.** The right question is therefore not *"what fraction of tasks can be routed"* but
*"how much of each trajectory can be safely downgraded"* — answered at three levels:

| level | question | answer source |
|-------|----------|---------------|
| **L1 surface routability** | how many steps are light? | ✅ measured: Opus traj. avg **76%** of model calls are light |
| **L2 cost-saving potential** | reprice those light steps at Haiku → how much saved? | ✅ measured: avg **62%** model-cost reduction (upper bound) |
| **L3 safe routability** | after the swap, does the final **reward** survive? | ⏳ requires the counterfactual experiment (see [`counterfactual.md`](./counterfactual.md)) |

L1/L2 confirm the intuition that nearly everything is *mechanically* routable. Only L3 — a
real per-step swap + re-score — tells us which swaps are *safe*. Steps are not independent:
one wrong dispatch decision can derail the whole chain (see sample 04 below). That is why
the safe percentage can only be established by experiment, not read off the trace.

### A separate, task-level signal (still useful)

Independently, comparing whole-task reward across the v10 benchmark (149 tasks × 3 models ×
3 rounds): mean reward **Opus 0.584 › Sonnet 0.466 › Haiku 0.296**. ~14% of *tasks* are
solved equally (1.0) by all three models — those can be downgraded wholesale, the laziest
possible routing. But that 14% is a *floor* on the opportunity, not a ceiling: the other
86% still contain mostly-routable steps, per the L1 table above.

---

## The killer comparison: same task, two models (samples 3 vs 4)

Both samples solve **the identical task** (`heston-mc-pricing`). The only variable is the
model. The traces could not look more different:

| | Opus 4.6 (sample 3) | Haiku 4.5 (sample 4) |
|---|:---:|:---:|
| reward | **1.0 ✅** | **0.0 ❌** |
| steps | 14 | **98** |
| tool calls | Bash×4, Read×2, Glob×2, Write×1 | Bash×34, **Edit×22**, Write×1, Read×1 |
| tool errors | 0 | 3 |
| total input tokens | 222,151 | **3,133,827** (14×) |
| total output tokens | 2,589 | 44,319 |
| **model cost** | **$1.18** | **$3.36** (2.8×) |

**The counter-intuitive headline:** on a task it cannot solve, the *cheap* model is the
*expensive* one. Haiku burned 14× the input tokens thrashing through 22 `Edit` cycles,
never converged, and cost almost 3× as much as Opus — for a reward of zero.

This is the single most important lesson for routing policy: **"use the cheap model" is
not free when the task is out of the cheap model's depth.** A good router must detect this
early (e.g. repeated `tool_result_is_error`, ballooning Edit loops) and escalate — and the
*only* reason we can detect it is that the trajectory records per-step tools, errors, and
token counts. (See [`trajectory-schema.md`](./trajectory-schema.md) §3.)

---

## Where the money actually is (samples 2, 3)

Multi-turn agents re-send the whole conversation every turn, so **input tokens dominate**:
in sample 3, input is 222K vs output 2.6K — an ~86:1 ratio. Cost is paid on *every* step,
most of which are short "dispatch" turns ("now run it", "read that file") that emit 50–200
output tokens but still pay full input price.

That is the structural opportunity for token routing:
- **Dispatch / glue turns** are cheap to get right and dominate the *count* of turns →
  candidates for a cheaper model.
- **Reasoning / codegen turns** (the one `Write` step emitting ~1,850 tokens in sample 3)
  are where the task is actually won or lost → keep on the strong model.

You can only separate these two populations because tokens and tool types are recorded
**per step**. With run-level totals alone (the Codex gap, samples 7–8), this analysis is
impossible.

---

## Why Codex traces are weaker for this research (samples 7, 8)

Samples 7 and 8 are perfectly good *task* solutions, but as *routing data* they are
degraded: the Codex ATIF-v1.5 trace records **no per-step token split** — only run totals
in `final_metrics`. It does expose `reasoning_output_tokens` and per-step cache tokens that
Claude Code omits. The net is in the gap table in
[`trajectory-schema.md`](./trajectory-schema.md) §5.

**Action item for data collection:** instrument the Codex wrapper to log per-call usage so
its traces reach Claude-Code-grade fidelity. Per-step tokens are the one field routing
cannot do without.

---

## What we want to do next (token remap)

These traces are the substrate for the next study: take a strong-model trajectory that
scored 1.0, and at each step ask *"could a cheaper model have produced an equally good
output here, holding the full prior context fixed?"* — then re-run and re-score to prove
it. The data granularity documented here (per-step tokens + structured tool calls + full
results + reward) is precisely what makes that experiment runnable. This repo is the
shared, public reference for what that data should look like.
