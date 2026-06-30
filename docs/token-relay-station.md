# Token relay station — new-api in front of Claude Code

A production-grade **token 中转站**: teammates get one key + one URL, point Claude Code at
it, and we route to Bedrock, manage keys, meter cost, and enforce quotas — all in an
open-source gateway with a web UI. **Verified with the
real Claude Code end-to-end.**

## Endpoints (deploy your own; example placeholders)

| URL | what |
|-----|------|
| `https://<your-relay-host>` | **Claude Code entrypoint** (`ANTHROPIC_BASE_URL`) |
| `https://<your-relay-admin-host>` | admin UI (key/channel/billing management, root login) |

## Which open-source project — and why

Steve floated **sub2api**; I surveyed the field with real GitHub data:

| project | ⭐ | what it's built for | fit for us |
|---------|----|--------------------|-----------|
| **[new-api](https://github.com/QuantumNous/new-api)** (chosen) | 40.5k | API-channel aggregation/distribution: connect provider **API keys**, expose unified OpenAI/**Claude**/Gemini endpoints, key mgmt + billing + multi-tenant UI | **High** — we connect a Bedrock API key, hand out keys, meter cost |
| [sub2api](https://github.com/Wei-Shaw/sub2api) | 29.6k | **Subscription** quota distribution: pool ChatGPT/Claude **OAuth subscriptions** for account/cost sharing | Medium — its killer feature (OAuth subscription pool) we don't use |
| [one-api](https://github.com/songquanpeng/one-api) | 35.3k | new-api's ancestor; lighter, JS | new-api's Claude Messages support is more complete |

**Conclusion: new-api.** Our need is "connect the Bedrock API-key backend → unified Claude
endpoint → issue keys + bill," which is new-api's home turf. sub2api shines when you're
pooling *subscription accounts*, which isn't our case. (If we later want to share Claude
Pro/ChatGPT *subscriptions*, revisit sub2api.)

## Architecture

```
teammate's Claude Code
  ANTHROPIC_BASE_URL=https://<your-relay-host>
  ANTHROPIC_API_KEY=sk-...(virtual key)
        │  Anthropic Messages /v1/messages  (+ anthropic-beta header)
        ▼
  Cloudflare Tunnel (dedicated: newapi-relay)
        ▼
  beta_sanitizer :41030 ── strips Bedrock-incompatible anthropic-beta flags
        ▼
  new-api :41029 ── auth(virtual key) · route(channel) · meter(tokens→$) · quota(429)
        ▼
  AWS Bedrock  ── Claude Sonnet/Haiku + gpt-oss  (one server-side bearer token)
```

The only credential to Bedrock (`AWS_BEARER_TOKEN_BEDROCK`) lives **server-side in new-api's
channel config**. Public users only ever hold a virtual `sk-` key with a quota.

## Verified end-to-end (real Claude Code, real verifier of correctness)

| test | result |
|------|--------|
| local: `123 * 456` | **56088** ✅ (Sonnet main-loop + Haiku background, both billed in new-api) |
| public domain: `789 * 654` | **516006** ✅ via `https://<your-relay-host>` |
| quota enforcement | a $5-budget key returned **403 "token quota is not enough, need $3.56"** — billing engine works |
| billing log | new-api logged per-call `claude-sonnet-4-5 in/out tokens → quota`, per virtual key |

## The real gotchas I had to solve (Claude Code ↔ Bedrock are not drop-in compatible)

These are the non-obvious bugs; documenting so the team doesn't re-hit them:

1. **`anthropic-beta` header flags Bedrock rejects.** Claude Code sends 6 beta flags; Bedrock
   400s on 3 of them (`thinking-token-count-2026-05-13`, `prompt-caching-scope-2026-01-05`,
   `advisor-tool-2026-03-01`). new-api rc.15's `header_override` doesn't strip them on the AWS
   **streaming** path → I added a 40-line `beta_sanitizer.py` in front that drops exactly
   those flags. (Bisected against live Bedrock; the other 3 incl. `interleaved-thinking` and
   `claude-code-20250219` are fine.)
2. **Body fields Bedrock rejects.** `context_management` (and `anthropic_beta`/`betas` in
   body) → removed via new-api channel **`param_override`** with `{"operations":[{"path":
   "context_management","mode":"delete"}, ...]}`.
3. **Model-name mapping.** Friendly names (`claude-haiku-4-5`) must map to real Bedrock
   inference-profile ids (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) via channel
   `model_mapping`, else `ValidationException: model identifier is invalid`.
4. **AWS channel uses bearer mode.** new-api's AWS channel type **33** supports
   `aws_key_type: api_key`, key format `<bearer>|<region>` — matches our
   `AWS_BEARER_TOKEN_BEDROCK` exactly (no AK/SK needed).
5. **Channel-create API shape.** `POST /api/channel/` needs `{"mode":"single","channel":{...}}`
   — sending channel fields at top level nil-panics the server.

## How a teammate uses it

```bash
# we issue them a virtual key in the admin UI (with a quota), then:
export ANTHROPIC_BASE_URL=https://<your-relay-host>
export ANTHROPIC_API_KEY=sk-<their-virtual-key>
export ANTHROPIC_MODEL=claude-sonnet-4-5
export ANTHROPIC_SMALL_FAST_MODEL=claude-haiku-4-5
unset CLAUDE_CODE_USE_BEDROCK
claude -p "do something"
```

Their usage shows up per-key in the admin dashboard; when they hit their budget they get a
clean 429.

## Security posture (this is a test deployment)

- ✅ Bedrock credential never leaves the server; public users hold only quota-limited `sk-` keys.
- ✅ Admin root uses a strong generated password (stored `chmod 600`, not the `123456` default).
- ✅ Dedicated Cloudflare tunnel (not a shared one) → no cross-service 502 risk.
- ⚠️ **Admin UI is public per request.** If the root password leaked, an attacker could mint
   unlimited keys against our Bedrock bill. For anything beyond testing: put the admin host
   behind VPN-gating, enable new-api's IP allowlist, and issue only small-budget keys.
- ⚠️ Issue **small-budget** test keys only; watch the Bedrock bill.

## Files & restart recovery

Keep the binary, sqlite db, secrets, and `beta_sanitizer.py` together on the host. The tunnel is token-based,
so it survives pod hostname drift. The relay survives pod restart by re-running the 3
processes; the channel/keys persist in `one-api.db`.

## Verdict

The token relay station works: real Claude Code, public URL, virtual keys, per-key billing,
quota enforcement, Bedrock backend — using a 40.5k-star open-source gateway plus one tiny
sanitizer for the Claude-Code↔Bedrock beta-flag mismatch. For production, add VPN-gating on
admin + small per-user budgets + the token-ledger integration (see proxy-key-service.md).
