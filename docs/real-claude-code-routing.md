# Routing the REAL Claude Code (via a Bedrock proxy)

Two flaws in the earlier `live-routing.md` experiment, both correctly called out:

1. **It used a hand-written agent loop.** Nobody deploys that. A real token router has to
   work in front of the *actual* agent (Claude Code / Codex), not a toy reimplementation.
2. **It hand-assembled the per-turn context.** That assembly can be wrong — and it *was*
   implicated in the bizarre result that the mixed `light_haiku` policy scored *worse* than
   pure Haiku, which is impossible if the strong model is a real fallback.

This experiment fixes both by putting a **routing proxy in front of the real Claude Code**.

## Architecture (deployable)

```
Claude Code 2.1.169  ──HTTPS──►  router_proxy (localhost)  ──Bedrock──►  Opus/Sonnet/Haiku
   (real agent loop,             POST /model/<id>/invoke[-with-response-stream]
    its own prompt,              • read requested modelId from the URL
    thinking, tools,             • apply ROUTING POLICY → choose actual model
    compaction)                  • forward (Bearer-token auth) and stream back
```

Claude Code is pointed at the proxy with `ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:PORT`.
**The proxy never touches the prompt** — Claude Code assembles `messages/system/tools/
thinking` itself; the proxy only rewrites which model answers. This removes both flaws:
the real agent runs, and there is no hand-assembled context to get wrong.

## The bug that explained the earlier anomaly

When the proxy downgraded a main-loop call to Haiku, Bedrock returned:

```
400 output_config.effort: Extra inputs are not permitted
```

Claude Code sends its main model (Opus 4.8) a body containing
`output_config={"effort":"high"}` and `thinking={"type":"adaptive"}` — **new-Opus features
that Haiku 4.5 rejects.** Naively swapping the modelId forwards an incompatible body, the
call 400s, Claude Code aborts after 1–2 calls, the output dir stays empty, reward = 0.

**This is the same class of failure (model-incompatible request, not model incapability)
that made the old hand-rolled `light_haiku` look terrible.** The fix: when routing to a
smaller family, the proxy strips the fields the target doesn't support
(`output_config`, `thinking`, thinking-only betas).

## Result — sma-crossover-spy, real Claude Code, official 20-test verifier

| policy | reward (2 runs) | model mix (served) | notes |
|--------|:---------------:|--------------------|-------|
| **passthrough** (CC default) | 1.0 / 1.0 | Sonnet ×7–8 + Haiku ×1 | Claude Code's own default is Sonnet main-loop + 1 Haiku utility call; solves in 8–9 calls |
| **all_haiku** | **1.0 / 1.0** | Haiku ×18–23 | every call forced to Haiku — full reward, but more turns |
| **downgrade_main** | **1.0 / 1.0** | Haiku main + CC's own Haiku util | downgrade the main-loop model only — full reward |

Every policy scored **1.0**. On this easy task, **downgrading Claude Code's main-loop model
to Haiku does not cost any reward** — vindicating the original intuition that simple work
can be pushed to a cheap model under a strong-model architecture. The earlier "mixing hurts"
result was a proxy bug, not a property of routing.

## What this does and doesn't show

- ✅ The deployable shape works: real Claude Code, routed per-call at the proxy, scored by
  the official verifier.
- ✅ Full reward is achievable on Haiku for this task — the question is now **cost**, not
  capability: Haiku takes more turns (18–23) than Sonnet (8–9). Net token cost per policy is
  the next thing to measure (the proxy logs per-call tokens to `*.calls.jsonl`).
- ⚠️ Easy task, 2 runs each. Hard tasks (cf. `counterfactual.md`, american-option-fd) are
  where Haiku genuinely fails on the decisive step — there, downgrade_main should lose
  reward. The interesting policy is **difficulty-aware**: downgrade where it's safe,
  escalate where it isn't. That cross-task sweep is the next experiment.
- ⚠️ Stream responses are currently buffered then relayed (fine for scoring; a production
  proxy would pass the event stream through incrementally).

## Reproduce

```bash
# one run:
bash scripts/run_cc_routed.sh sma-crossover-spy downgrade_main 0 us.anthropic.claude-haiku-4-5-20251001-v1:0
# proxy alone:
ROUTER_POLICY=all_haiku ROUTER_PORT=9920 uvx -p 3.11 --with boto3 python scripts/router_proxy.py
ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:9920 claude --dangerously-skip-permissions -p "..."
```
