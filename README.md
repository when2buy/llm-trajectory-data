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

### Headline finding

On the **same task** (`heston-mc-pricing`), Opus solved it in 14 steps for $1.18, while
Haiku thrashed through **98 steps and 3.1M input tokens** (22 failed Edit loops), never
converged, and cost **$3.36 — 2.8× more — for reward 0.0.** On a task out of its depth, the
"cheap" model is the expensive one. Detecting this early is the router's core job, and it
is only possible because the trace records per-step tools, errors, and tokens.

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
