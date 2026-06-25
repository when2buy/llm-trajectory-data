# L3 — Single-turn counterfactual swap (the only solid test of *safe* routability)

L1/L2 (in [report.md](./report.md)) show that almost every trajectory is *mechanically*
routable — most steps are cheap "light" steps. But mechanical routability is not safety:
**steps are not independent**, and one wrong decision at a decisive step can sink the whole
task. The only way to know whether a step is *safely* downgradable is to actually swap it
and re-score. That is L3.

## Method

Per Steve's methodology — reconstruct the run, swap exactly one turn, re-score:

1. Take a real **Opus** trajectory that scored **reward = 1.0** (`american-option-fd-new`,
   trial `fb-v12-opus47-...`). This task is single-file codegen: the entire reward is
   decided by one step — the `Write /app/solve.py` at **step 12**. Earlier steps are
   environment probing; later steps just run the script.
2. **Reconstruct, byte-for-byte, the exact context Opus saw at step 12**: system prompt +
   the task instruction + every prior agent message + every `tool_use` block + every real
   `tool_result` (pulled from `tool_result_metadata.stdout` in the trace).
3. At step 12, call a **candidate model** (opus control / sonnet / haiku) with the *same*
   context and *same* tools, forcing it to produce the solution.
4. Extract its `solve.py`, run it, and run the **official 54-test verifier** → reward.
5. Repeat 3× per model to estimate non-determinism.

**Control:** Opus, given the reconstructed context, reproduces **1.0 (3/3)** — confirming
the context reconstruction is faithful and the harness is trustworthy. This is the
control-variable check Steve asked for.

## Result

| model | 3 runs (reward) | avg | full-pass rate | avg step cost | safe to swap here? |
|-------|-----------------|:---:|:--------------:|:-------------:|:------------------:|
| **Opus** (control) | 1.00 / 1.00 / 1.00 | **1.00** | 100% | $0.400 | — (reference) |
| **Sonnet** | NO_CODE / 1.00 / 0.78 | 0.59 | 33% | $0.167 | ⚠️ capable but unreliable |
| **Haiku** | 0.00 / 0.63 / 0.00 | 0.21 | 0% | $0.034 | ❌ no |

What actually happened on the failures (not surface issues — real capability gaps):
- **Haiku run0:** runtime crash inside the PSOR projection (numerical indexing error).
- **Haiku run2:** non-convergent — solution ran past the 240 s limit (likely an unbounded
  iteration). The model wrote *plausible* code that does not actually converge.
- **Sonnet run0:** never emitted a `Write` — it kept reasoning until it hit the 12k output
  cap. A real router would catch this (no tool call) and retry/escalate.

## Why this is the answer to "what % is routable?"

For this task, surface routability (L1) says **~30% of steps are "light"** and the one
heavy codegen step is the other 70% of the model calls. The naive reading — "downgrade the
light steps" — is fine, but the *decisive* step is exactly the one L3 proves **cannot** be
safely downgraded to Haiku (reward 1.0 → 0.21). 

**Surface routability ≠ safe routability.** You cannot read safety off the trace; you have
to swap and re-score. That is the whole point of L3, and it is why a percentage claimed from
trajectory inspection alone is not trustworthy.

## Implication for the router design

The right policy is **not** a static "this step type → cheap model" rule. It is:

> **cheap-first, verified, auto-escalate** — try the cheaper model on a step, check the
> outcome (did it emit a tool call? did the code run? did a cheap sanity check pass?), and
> escalate to a stronger model when the check fails.

On this step, Sonnet would save **58%** vs Opus and score 1.0 — but only 1 run in 3. The
value is real, but it is only safely capturable behind a verification + escalation gate.
The next experiments extend this single-step swap to (a) every model-call step in the
trajectory, and (b) the "continue" mode where the candidate keeps driving after the swap,
to measure end-to-end routed cost vs reward.

## Reproduce

```bash
uvx -p 3.11 --with boto3 python scripts/l3_swap.py opus,sonnet,haiku 3
# results → docs/l3_swap_results.json
```

Requires Bedrock creds (Opus 4.8 / Sonnet 4.6 / Haiku 4.5) and the QFBench task source
(`finance-bench/tasks/american-option-fd-new`). The verifier runs locally via `uvx` — no
Docker needed (Steve's "reconstruct the environment flexibly" requirement).
