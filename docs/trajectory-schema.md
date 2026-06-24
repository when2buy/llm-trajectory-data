# Trajectory Schema — what to collect, and at what granularity

This document specifies the **data we need to study token routing / token remap** on
multi-turn agent execution chains (Claude Code, Codex CLI, …). It is written against
real traces in [`../samples/`](../samples), all of which use the **ATIF** format
(Agent Trajectory Interchange Format) emitted by the
[Harbor](https://github.com/harbor-framework/harbor) runner.

> **TL;DR for the team.** A routing-grade trajectory is a flat, ordered list of `steps`.
> Each step is one event in the LLM execution chain (a model turn, a tool call, or a tool
> result). The *minimum* fields a step must carry to be useful for routing are:
> **`source`, `message`, per-step `prompt_tokens` / `completion_tokens`, the structured
> tool call (`tool_use_name` + `raw_arguments`), the tool result, and `stop_reason`.**
> Claude Code traces carry all of this. Codex traces are missing per-step tokens — see
> the gap table at the bottom.

---

## 1. Top-level envelope

```jsonc
{
  "schema_version": "ATIF-v1.2",        // v1.2 (claude-code) … v1.5 (codex). Diffs matter — see §5
  "session_id": "…",                    // opaque; sanitized out of public samples
  "agent": {
    "name": "claude-code",              // claude-code | codex | finance-zero
    "version": "2.1.126",
    "model_name": "claude-opus-4-6",    // the model under test ← routing key
    "extra": { "cwds": ["/app"], "git_branches": ["HEAD"] }
  },
  "steps": [ /* ordered events — §2 */ ],
  "final_metrics": { /* run totals — §4 */ }
}
```

The router's job is to decide, **per step**, whether a cheaper model could have produced
an equally good `message` / tool call. So everything hinges on the per-step granularity.

---

## 2. The `step` object — the unit of routing

Every step is one event. Three `source` values:

| `source` | meaning | carries tokens? |
|----------|---------|-----------------|
| `user`   | task prompt, or a tool *result* fed back to the model | no |
| `agent`  | a model turn (text and/or a tool call) **or** an executed tool | only on model turns |
| `system` | system/permission preamble (codex) | no |

```jsonc
{
  "step_id": 10,
  "timestamp": "2026-05-02T11:31:40.402Z",
  "source": "agent",
  "message": "Executed Write toolu_bdrk_0181g…",   // human-readable summary of the event
  "metrics": {                                       // PRESENT ONLY on model-call steps
    "prompt_tokens": 27186,                          // ← input tokens for THIS turn
    "completion_tokens": 1852                         // ← output tokens for THIS turn
  },
  "extra": { /* §3 — the structured payload */ }
}
```

### Why per-step `metrics` is the single most important field for routing

A multi-turn agent re-sends the whole conversation every turn, so **input tokens dominate
cost and they are paid on every step**. To know *where* the spend is and *which* steps are
cheap to downgrade, you need the prompt/completion split **per step**, not just a run total.

Observed in [`03-divergence-heston-opus-pass`](../samples/03-divergence-heston-opus-pass):
one `Write` step emits 1,852 completion tokens (the actual solution) while every other turn
emits 50–200. The expensive *reasoning/codegen* steps and the cheap *dispatch* steps ("now
run it") are only separable because tokens are recorded per step.

> **Granularity rule #1:** record `prompt_tokens` + `completion_tokens` on *every model
> call*. Tool-execution steps correctly have empty `metrics` (no model was called).

---

## 3. The `extra` object — structured tool & control payload

This is where the routing-relevant structure lives. Fields seen in claude-code traces:

| field | example | why routing needs it |
|-------|---------|----------------------|
| `stop_reason` | `"tool_use"` / `"end_turn"` | distinguishes a dispatch turn from a final turn |
| `tool_use_name` | `"Bash"`, `"Write"`, `"Read"`, `"Glob"`, `"Edit"` | the action taxonomy — routing policies key off tool type |
| `raw_arguments` | `{"file_path":"/app/heston.py","content":"import numpy…"}` | the **exact** tool input — lets us replay a step on another model byte-for-byte |
| `tool_result_metadata` | full file content / stdout returned to the model | the observation the next turn conditions on |
| `tool_result_is_error` | `true` / `false` | error-recovery turns are high-value-to-keep-expensive |
| `is_sidechain` | `false` | sub-agent vs main loop |
| `cwd` | `"/app"` | execution context |

> **Granularity rule #2:** capture the tool call as **structured data**
> (`tool_use_name` + `raw_arguments`), not just a prose summary. Without `raw_arguments`
> you cannot reconstruct the exact input to re-run the step on a candidate model — which is
> the whole experiment behind token remap.

> **Granularity rule #3:** capture the **full tool result** (`tool_result_metadata`), and
> the `tool_result_is_error` flag. The model's next decision is conditioned on it; a router
> that can't see it is flying blind.

---

## 4. `final_metrics` — run-level totals (necessary, not sufficient)

```jsonc
{
  "total_prompt_tokens": 222151,
  "total_completion_tokens": 2589,
  "total_cached_tokens": 0,
  "total_cost_usd": 1.17548,
  "total_steps": 14,
  "extra": {
    "reasoning_output_tokens": 1325,                  // codex only — hidden reasoning
    "total_cache_read_input_tokens": 0,
    "service_tiers": ["standard"]
  }
}
```

Totals are needed for the cost bottom line, but **routing decisions cannot be made from
totals** — they have no per-step structure. Collect both.

---

## 5. Cross-agent gap analysis — collect more from Codex

Both agents emit ATIF, but at different fidelity. This directly affects what routing
research is possible on each.

| capability needed for routing | claude-code (ATIF-v1.2) | codex (ATIF-v1.5) |
|-------------------------------|:-----------------------:|:-----------------:|
| per-step `prompt_tokens`/`completion_tokens` | ✅ on every model turn | ❌ **only run totals** |
| structured tool call (`tool_use_name`) | ✅ | ⚠️ name folded into `message`; args in `raw_arguments` |
| full tool result fed to model | ✅ `tool_result_metadata` | ⚠️ partial |
| error flag per step | ✅ `tool_result_is_error` | ❌ |
| hidden reasoning tokens | ❌ (not exposed) | ✅ `reasoning_output_tokens` |
| per-step cache tokens | ❌ (totals only) | ✅ `cached_input_tokens` |
| web search / streaming sub-calls | n/a | ✅ `web_search_call`, `write_stdin` |

**Recommendation for data collection going forward:**
1. **Always log per-step token splits.** This is the field Codex traces lack and the field
   routing most needs. If using Codex CLI, instrument the wrapper to record per-call usage.
2. **Always log the structured tool call + full result + error flag.** Prose summaries
   ("Executed Write …") are not replayable.
3. **Log cache + reasoning tokens when the provider exposes them** — they change the real
   cost of a step and therefore the routing decision.
4. **Keep the raw model id** (`agent.model_name`) — it is the routing label.

The eight curated samples in [`../samples/`](../samples) are chosen so the team can see
each of these fields in a real, end-to-end trace. See [`report.md`](./report.md) for the
guided tour.
