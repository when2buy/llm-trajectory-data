# llm-trajectory-data

**Reference dataset: what an LLM agent execution chain looks like, at the granularity
needed for token routing / token remap research.**

When an agent like **Claude Code** or **Codex CLI** solves a task, it runs a multi-turn
loop — read files, write code, run it, read the error, fix it, repeat. This repo captures
eight real, end-to-end examples of those execution chains and documents **exactly what
JSON we collect and at what granularity**, so the team can agree on the data spec before we
build the router.

📄 **Start here:** [**Guided report**](./docs/report.md) · [**Schema spec**](./docs/trajectory-schema.md)
🌐 **Web view:** **https://when2buy.github.io/llm-trajectory-data/** (interactive trace browser)

---

## Why this exists

We want to route each step of an agent run to the cheapest model that still gets the task
done. To study that, we first need to know **what data a trajectory must carry**. The short
answer, derived from the samples here:

> A routing-grade trajectory is an ordered list of **steps**. Each step records its
> **source** (user / agent / tool-result), the **message**, **per-step token counts**, the
> **structured tool call** (`tool_use_name` + `raw_arguments`), the **full tool result**,
> and a **`stop_reason`** — plus a task-level **reward**.

Full field-by-field spec in [`docs/trajectory-schema.md`](./docs/trajectory-schema.md).

---

## The data

Eight curated traces, chosen to cover three routability regimes and both agent types:

| # | sample | agent | model | reward | steps | what it shows |
|---|--------|-------|-------|:------:|:-----:|---------------|
| 1 | `01-freewin-black-scholes-haiku` | claude-code | Haiku 4.5 | 1.0 | 20 | cheap model fully solves it (free win) |
| 2 | `02-freewin-sma-opus` | claude-code | Opus 4.6 | 1.0 | 14 | same regime, run on Opus = overpaying |
| 3 | `03-divergence-heston-opus-pass` | claude-code | Opus 4.6 | 1.0 | 14 | strong model, clean solve |
| 4 | `04-divergence-heston-haiku-fail` | claude-code | Haiku 4.5 | 0.0 | 98 | weak model flails — and costs **more** |
| 5 | `05-opusonly-american-fd-opus-pass` | claude-code | Opus 4.7 | 1.0 | 21 | hard task only strong model passes |
| 6 | `06-opusonly-american-fd-haiku-fail` | claude-code | Haiku 4.5 | 0.0 | 28 | cheap model gives up |
| 7 | `07-codex-bl-regime-hmm` | codex | gpt-5.3-codex | 1.0 | 13 | Codex trace (different schema fidelity) |
| 8 | `08-codex-zero-coupon` | codex | gpt-5.4 | 1.0 | 13 | Codex trace |

Each `samples/<id>/` contains:

```
trajectory.json   ATIF agent trace — the core artifact (ordered steps)
result.json       Harbor result: reward, token totals, cost
ctrf.json         per-test pass/fail (where available)
meta.json         derived routing-relevant stats
```

### Headline findings

**1. Routing is per-step, and ~100% of trajectories are routable.** Across 2,859 real
claude-code traces, **every single one** has at least one cheap "light" step (dispatch /
read / glue), and on Opus traces **~76% of model calls** are light. Repricing just the
light steps at Haiku's rate is an avg **62%** model-cost reduction (upper bound — safety is
a separate question, see L3 below). Routability is not "some % of tasks" — it's "most of
*every* trajectory." Details: [report](./docs/report.md#routability-is-per-step-not-per-task).

**2. On an out-of-depth task, the "cheap" model is the expensive one.** Same task
(`heston-mc-pricing`): Opus solved it in 14 steps for $1.18; Haiku thrashed through **98
steps and 3.1M input tokens** (22 failed Edit loops), never converged, and cost **$3.36 —
2.8× more — for reward 0.0.** Detecting this early is the router's core job, and it is only
possible because the trace records per-step tools, errors, and tokens.

**3. Safe routability must be *measured*, not inferred (L3 counterfactual).** We took an
Opus trajectory that scored 1.0, reconstructed the exact context at its decisive codegen
step, swapped *only that step* to a cheaper model, and re-ran the official 54-test verifier.
Result: Opus 1.0 (control) → **Sonnet 0.59 avg (1/3 full pass, 58% cheaper) → Haiku 0.21
(0/3, code crashes / non-convergent)**. The "light step %" from the trace said nothing about
this — the decisive step is exactly the one that cannot be safely downgraded.
Full method & data: [docs/counterfactual.md](./docs/counterfactual.md).

**4. Routing the REAL Claude Code via a Bedrock proxy — the deployable experiment.** A
routing proxy sits in front of the *actual* Claude Code (not a hand-written loop): Claude
Code assembles its own prompt and drives the loop; the proxy only rewrites which model
answers each Bedrock call. On `sma-crossover-spy` (official 20-test verifier), **every policy
scored reward 1.0 — including forcing the whole main loop onto Haiku.** Downgrading the
strong main-loop model to Haiku costs no reward on this easy task. A subtle bug found and
fixed along the way: Claude Code sends Opus-only fields (`output_config.effort`, adaptive
`thinking`) that Haiku rejects with a 400 — the proxy must strip them when downgrading. That
same class of model-incompatible-request bug is what made an earlier hand-rolled "mixed"
experiment look like mixing *hurt*; it doesn't. Full writeup + architecture:
[docs/real-claude-code-routing.md](./docs/real-claude-code-routing.md). (An earlier
hand-written-loop version is kept at [docs/live-routing.md](./docs/live-routing.md) with its
caveats noted.)

**5. Cross-provider: run the REAL Claude Code on a NON-Claude model.** Claude Code sends
standard Anthropic Messages (`POST /v1/messages`) to whatever `ANTHROPIC_BASE_URL` points at.
We wrote a ~250-line translation proxy ([scripts/xprovider_proxy.py](./scripts/xprovider_proxy.py))
that converts Anthropic⇄OpenAI (including tool_use / tool_result) and forwards to
**`gpt-oss-120b`** (an OpenAI open model, not Claude). Real Claude Code — multi-turn, with all
28 of its tools — wrote a file, ran it, and reported output, **driven entirely by gpt-oss**.
The open-source landscape (use these for production: `claude-code-router` ⭐35k,
`litellm` ⭐51k, minimal `claude-code-proxy` ⭐3.6k), the mechanism, the minimal build, and the
working demo with logs are in
[docs/cross-provider-routing.md](./docs/cross-provider-routing.md).

---

## Provenance

- **Tasks & verifier:** [QuantitativeFinance-Bench](https://github.com/beckybyte/QuantitativeFinance-Bench) (16+ quant-finance coding tasks, each with a pytest suite)
- **Runner:** [Harbor](https://github.com/harbor-framework/harbor) (emits ATIF trajectories + CTRF test results)
- **Format:** ATIF (Agent Trajectory Interchange Format) — `v1.2` for claude-code, `v1.5` for codex
- Traces are sanitized (absolute machine paths and session ids removed); data structure and
  content are otherwise intact.

## Reproduce / regenerate

```bash
python3 scripts/export_samples.py     # re-export & sanitize from source trials
```

## Roadmap

This dataset is the substrate for a follow-up **token remap** study: replay a strong-model
trajectory step-by-step on cheaper models, holding context fixed, and re-score with the
real verifier to prove which steps are safely downgradable. This repo defines the data such
a study needs.
