# Making Claude Code call a different API (cross-provider routing)

**Goal:** put our own API in front of Claude Code, catch its requests, and route/decompose
them to whatever model/provider we want — including non-Anthropic models (OpenAI, Codex,
Gemini, …). This doc covers the open-source landscape, the core mechanism, the minimal
build, and a **working end-to-end demo with real results** (Claude Code driven by a
non-Claude model).

---

## TL;DR

- The hook is a single env var: **`ANTHROPIC_BASE_URL`**. Claude Code sends standard
  **Anthropic Messages** requests (`POST /v1/messages`) to whatever URL you set. Point it at
  a local proxy and you own every request.
- To reach a non-Anthropic model you need a **protocol translation** layer:
  Anthropic Messages ⇄ OpenAI Chat Completions (the hard part is `tool_use`/`tool_result`).
- **Don't build the full thing from scratch** — `claude-code-router` (⭐35k) or `litellm`
  (⭐51k) already do it well. Use one of those for production.
- We built a **~250-line minimal proxy** to prove the mechanism, and ran the **real Claude
  Code on `gpt-oss-120b`** (an OpenAI open model, not Claude) — it completed a multi-turn
  tool-using coding task. Logs below.

---

## Open-source landscape (real projects, surveyed)

| Project | Stars | Lang | What it is | Use it when |
|---------|------:|------|-----------|-------------|
| **[claude-code-router](https://github.com/musistudio/claude-code-router)** | ~35k | TS | Local gateway purpose-built for Claude Code/Codex. Routing rules (default / background / thinking / long-context / web-search / subagent), provider presets (OpenAI, Gemini, OpenRouter, DeepSeek, Moonshot, Z.AI, …), fallback, key rotation, desktop UI. | **Best turnkey choice for Claude Code specifically.** |
| **[litellm](https://github.com/BerriAI/litellm)** | ~51k | Py | Universal AI gateway: 100+ providers in OpenAI/native format, cost tracking, guardrails, load-balancing, logging. Has an Anthropic `/v1/messages` passthrough. | **Production gateway across many clients/providers**, billing/governance. |
| **[musistudio/llms](https://github.com/musistudio/llms)** | ~300 | TS | The protocol-transformation engine behind claude-code-router (anthropic ↔ openai ↔ gemini). | Embed translation in your own service. |
| **[1rgs/claude-code-proxy](https://github.com/1rgs/claude-code-proxy)** | ~3.6k | Py | Minimal "run Claude Code on OpenAI/Gemini" proxy, via LiteLLM. Maps `haiku`/`sonnet` → `SMALL_MODEL`/`BIG_MODEL`. | **Minimal reference** to read/fork. |
| **[anthropic-proxy](https://github.com/maxnowack/anthropic-proxy)** | ~400 | JS | Smallest anthropic→openai→OpenRouter converter. | Tiny single-file example. |

**Recommendation for us:** for a quick internal deployment, run **claude-code-router** (it
already has the routing rules + provider presets + UI). For embedding routing logic into our
own pipeline (analysis / decomposition), the **minimal proxy below** is the pattern, or use
**litellm** as a library.

---

## The core mechanism

Claude Code decides its endpoint from env vars:

| Mode | Env | Claude Code sends |
|------|-----|-------------------|
| Direct Anthropic | `ANTHROPIC_BASE_URL` (+ `ANTHROPIC_API_KEY`) | **Anthropic Messages** `POST /v1/messages` |
| Bedrock | `CLAUDE_CODE_USE_BEDROCK=1` (+ `ANTHROPIC_BEDROCK_BASE_URL`) | Bedrock InvokeModel `POST /model/<id>/invoke[-with-response-stream]` |

For cross-provider routing the **Anthropic Messages** mode is the one to intercept. A captured
real request looks like:

```
POST /v1/messages?beta=true
{ "model": "us.anthropic.claude-sonnet-4-6",
  "system": [{"type":"text","text":"..."}],     // a LIST, not a string
  "messages": [ ... ],
  "tools": [ 28 tool definitions ],               // Claude Code ships ~28 tools
  "max_tokens": ..., "stream": true,
  "thinking": {...}, "output_config": {...}, "context_management": {...} }
```

Your proxy must:
1. **Translate request** Anthropic→OpenAI: flatten `system` list → system message; map each
   `tool` (`input_schema`) → OpenAI `function` (`parameters`); turn Anthropic `tool_use`
   blocks → OpenAI `tool_calls` and `tool_result` blocks → OpenAI `role:"tool"` messages.
2. **Drop fields** the target doesn't accept (`thinking`, `output_config`,
   `context_management`).
3. **Forward** to the OpenAI-compatible backend.
4. **Translate response** OpenAI→Anthropic: `tool_calls` → `tool_use` blocks; map
   `finish_reason` (`tool_calls`→`tool_use`, `length`→`max_tokens`, `stop`→`end_turn`).
5. **Stream** if asked: emit the Anthropic SSE event sequence
   (`message_start` → `content_block_start/delta/stop` → `message_delta` → `message_stop`).

Minimal connect:
```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8300 ANTHROPIC_API_KEY=dummy claude -p "..."
```

---

## Our working demo (real code, real results)

We wrote a ~250-line translation proxy: [`scripts/xprovider_proxy.py`](../scripts/xprovider_proxy.py).
Backend: **AWS Bedrock's OpenAI-compatible endpoint** (`/openai/v1/chat/completions`) serving
**`gpt-oss-120b`** — a genuinely non-Claude model. (Bedrock conveniently exposes an
OpenAI-format endpoint, so it doubles as a real "other provider" for the demo.)

### Test 1 — translation unit test (tool calling)
Anthropic-format request with a `get_weather` tool → proxy → gpt-oss → back:
```
stop_reason: tool_use
content blocks: ['tool_use']
  TOOL CALL -> get_weather {'city': 'Tokyo'}
```
The tool round-trip (Anthropic tool def → OpenAI function → OpenAI tool_call → Anthropic
tool_use) works.

### Test 2 — real Claude Code, simple prompt
```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8302 ANTHROPIC_API_KEY=dummy claude --model sonnet \
  -p "What is 17 * 23? Reply with just the number."
# -> 391    (correct — produced by gpt-oss-120b, not Claude)
```
Proxy log proves who served it:
```json
{"claude_asked":"us.anthropic.claude-sonnet-4-6","backend_model":"openai.gpt-oss-120b-1:0","n_tools_sent":28,"stop":"end_turn"}
```

### Test 3 — real Claude Code, multi-turn TOOL-USING coding task
```bash
claude --dangerously-skip-permissions -p \
  "Create /tmp/xptest/fib.py that prints the first 10 Fibonacci numbers, run it, tell me the output."
```
Claude Code, **driven entirely by gpt-oss-120b**, wrote the file, ran it, and reported:
```
0 1 1 2 3 5 8 13 21 34
```
The file really exists ([`xprovider_demo_fib.py`](./xprovider_demo_fib.py)) and the 4-call
trajectory shows real tool use ([`xprovider_demo_calls.jsonl`](./xprovider_demo_calls.jsonl)):
```
haiku                          -> gpt-oss-120b  tools=0   stop=end_turn   (CC background task)
claude-sonnet-4-6              -> gpt-oss-120b  tools=28  stop=tool_use   (Write fib.py)
claude-sonnet-4-6              -> gpt-oss-120b  tools=28  stop=tool_use   (Bash run it)
claude-sonnet-4-6              -> gpt-oss-120b  tools=28  stop=end_turn   (report result)
```

**Conclusion:** Claude Code's full agent loop — multi-turn, tool calls, file edits, running
commands — runs on a non-Anthropic model behind a translation proxy. The construct works.

---

## How this connects to the token-router goal

This is the deployable substrate for everything earlier in this repo:
- The proxy is the single choke point where we can **route per request** (cheap vs strong
  model), **decompose** (split a turn, run sub-models, merge), **analyze/log** (per-call
  tokens, tools, cost), and **fail over** across providers.
- Earlier experiments (`real-claude-code-routing.md`) routed within Bedrock; this extends the
  same idea **across providers** via protocol translation.
- Open question to carry forward: which models can actually *carry* Claude Code's tool-heavy
  loop at quality (gpt-oss did simple tasks here; harder QFBench tasks are the real test) —
  and the cost trade-off of more-turns-on-a-cheaper-model.

## Honest limitations of our minimal proxy
- Streaming is buffered then re-emitted as one SSE burst (works for Claude Code; a production
  proxy should stream incrementally for latency).
- No retry/fallback, no key rotation, no cost dashboard — `claude-code-router`/`litellm` have
  these; use them for anything real.
- `gpt-oss` emits `<reasoning>…</reasoning>` preambles; we strip them. Other models may need
  their own quirks handled.
- Tested on small tasks. Tool-calling fidelity on long, complex Claude Code sessions (sub-
  agents, parallel tool calls, large contexts) needs more validation.

## Reproduce
```bash
XP_PORT=8300 uvx -p 3.11 python scripts/xprovider_proxy.py &     # uses Bedrock gpt-oss by default
ANTHROPIC_BASE_URL=http://127.0.0.1:8300 ANTHROPIC_API_KEY=dummy CLAUDE_CODE_USE_BEDROCK= \
  claude --model sonnet -p "What is 17 * 23?"
# point XP_BACKEND_URL / XP_BACKEND_KEY / XP_MODEL at any OpenAI-compatible provider
```
