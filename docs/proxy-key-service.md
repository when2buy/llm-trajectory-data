# A shared "proxy key" service for the team (design + experiment plan)

Goal: hand teammates **one key and one URL**. They run Claude Code (or Codex) against it and
it just works — while we route to the cheapest capable model, **track per-key cost**, enforce
quotas, and never expose the underlying provider credentials.

## First, the money question (answered honestly)

The `gpt-oss` calls in our demo were **not free**. They used the **same
`AWS_BEARER_TOKEN_BEDROCK`** as Claude Code — i.e. our **Adobe AWS Bedrock account**, billed
per token. `gpt-oss` is an OpenAI *open-weights* model **hosted on Bedrock**, not free
ChatGPT. It's just ~100× cheaper than Opus:

| model (Bedrock) | $/1M in | $/1M out | vs Opus |
|-----------------|--------:|---------:|--------:|
| Opus 4.x | 15.00 | 75.00 | 1× |
| Sonnet 4.x | 3.00 | 15.00 | ~5× cheaper |
| Haiku 4.5 | 0.80 | 4.00 | ~19× cheaper |
| gpt-oss-120b | ~0.15 | ~0.60 | ~100× cheaper |

**Why this matters for a proxy key:** the spend already lands on *our* Bedrock account behind
*one* upstream credential. So the proxy is the natural place to (a) hide that credential,
(b) mint per-user keys, (c) meter and cap usage. That's exactly the "proxy key + token-author
integration" idea.

## Architecture

```
teammate's Claude Code                  OUR PROXY-KEY SERVICE                upstream
  ANTHROPIC_BASE_URL=https://llmproxy   ┌─────────────────────────────┐
  ANTHROPIC_API_KEY=tk_alice_xxx   ───► │ 1. auth: validate tk_ key    │
                                        │ 2. quota: check key's budget │ ──► Bedrock (1 cred)
                                        │ 3. route: pick model/policy  │ ──► OpenAI / OpenRouter
                                        │ 4. translate: anthropic⇄oai  │ ──► Gemini …
                                        │ 5. meter: log tokens & $/key │
                                        └─────────────────────────────┘
```

It's our `xprovider_proxy.py` + three new middleware layers: **auth → quota → meter**. The
routing/translation core already works (see [core-howto.md](./core-howto.md)).

## "Token author" integration — key issuance & accounting

The proxy issues **virtual keys** (`tk_<user>_<random>`); the real provider creds stay
server-side. Each key maps to a record we control:

```jsonc
// keys.json (server-side; never shipped to clients)
{
  "tk_alice_9f3a": {
    "owner": "alice@adobe.com",
    "policy": "balanced",          // which routing policy this key gets
    "monthly_budget_usd": 50.0,
    "spent_usd": 12.34,            // updated by the meter
    "allow_models": ["haiku","sonnet","gpt-oss-120b"],
    "disabled": false
  }
}
```

- **Issue / revoke** keys via a tiny admin CLI (or wire into the existing `svc`/token tooling).
- **Meter**: every response carries `usage`; the proxy multiplies by the served model's price
  and increments `spent_usd[key]`. This is the hook to "configure with our token author" —
  the proxy is the single source of truth for per-user LLM cost, and can push records to
  whatever ledger the token system uses.
- **Quota**: before forwarding, if `spent_usd ≥ monthly_budget_usd` → return a 429 with a
  clear message. Optional soft-cap: auto-downgrade to the cheapest model instead of blocking.
- **Cost attribution**: because one upstream Bedrock credential fans out to many virtual keys,
  finance sees one Bedrock bill, and *we* break it down per teammate from the meter log.

## Routing policies a key can be assigned

| policy | behavior | who |
|--------|----------|-----|
| `quality` | always Opus/Sonnet | latency/quality-critical |
| `balanced` | Sonnet main loop, Haiku for background/small calls | default |
| `cheap` | Haiku / gpt-oss everywhere, escalate on failure | bulk/experimental |
| `byo-provider` | route to OpenAI/Gemini/OpenRouter via translation | cross-provider needs |

Policy lives on the key, so teammates change nothing; we tune cost centrally.

## Experiment plan (staged, each step verifiable)

1. **E1 — Auth+meter MVP (local).** Add `Authorization: Bearer tk_…` validation + a
   `keys.json` + per-key token metering to `xprovider_proxy.py`. Verify: two keys, one with a
   tiny budget; confirm the second gets 429 after it's exhausted. *(prototype below — done)*
2. **E2 — Real Claude Code through a keyed proxy.** Run Claude Code with
   `ANTHROPIC_API_KEY=tk_…`; confirm metering increments and the key's policy picks the model.
3. **E3 — Cost truth check.** Run the same fixed task (e.g. a QFBench task) under `quality`
   vs `balanced` vs `cheap`; record real $ per key from the meter. Produces the "how much does
   routing actually save end-to-end" number.
4. **E4 — Deploy internally.** Put the proxy on a registered port (`svc add llmproxy`) behind
   `https://llmproxy.adobefoundry.com` (Cloudflare tunnel). Hand 2–3 teammates a `tk_` key.
5. **E5 — Token-system handshake.** Define the record format the proxy emits per call and how
   it lands in the existing token/billing ledger (needs a short sync on the token author's
   schema).
6. **E6 — Quality gate.** For `cheap`/`byo-provider`, add the verify-and-escalate loop (cheap
   model first; if a turn errors or fails a check, escalate) so cost cuts don't cost reward.

## What's proven vs open

- ✅ Proven: the routing + translation core, real Claude Code on gpt-oss, the auth+quota MVP
  (E1) below.
- ⚠️ Open: streaming should be incremental for production; cross-provider tool-call fidelity on
  long sessions; the exact token-ledger schema (E5); whether cheap models hold quality on hard
  tasks (E6, ties to `counterfactual.md`).
- For a production gateway with keys/quota/cost-tracking already built, **litellm** has virtual
  keys + budgets out of the box — fastest path if we don't need custom logic.

## E1 prototype

See [`scripts/keyed_proxy.py`](../scripts/keyed_proxy.py): wraps the translation proxy with
bearer-key auth, a `keys.json` budget ledger, and per-call USD metering. Demo result captured
in [`keyed_proxy_demo.txt`](./keyed_proxy_demo.txt).
