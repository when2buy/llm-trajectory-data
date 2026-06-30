# Deploy your own Claude-Code → Bedrock token relay

Self-contained package to stand up a **token relay station** on any Linux machine: teammates
point Claude Code at one URL with one key; you route to AWS Bedrock, manage keys, meter cost,
and enforce quotas via the [new-api](https://github.com/QuantumNous/new-api) gateway. A tiny
`beta_sanitizer.py` sits in front to fix the Claude-Code↔Bedrock beta-flag mismatch.

No Docker required (uses new-api's prebuilt binary). Just bash + curl + python3.

## Quick start

```bash
cp .env.example .env
#   edit .env: set AWS_BEARER_TOKEN_BEDROCK and ADMIN_PASSWORD (alphanumeric, >=8 chars)

./setup.sh        # downloads new-api, inits admin, creates the Bedrock channel + a $5 key
./start.sh        # launches new-api (admin/API) + beta_sanitizer (Claude Code entrypoint)
./show-key.sh     # prints the virtual key(s) to hand out
```

Then any teammate uses it:

```bash
export ANTHROPIC_BASE_URL=http://<host>:41030        # the sanitizer port
export ANTHROPIC_API_KEY=sk-<their-key>              # from ./show-key.sh
export ANTHROPIC_MODEL=claude-sonnet-4-5
export ANTHROPIC_SMALL_FAST_MODEL=claude-haiku-4-5
unset CLAUDE_CODE_USE_BEDROCK
claude -p "do something"
```

`./stop.sh` stops the two processes. Re-run `./start.sh` after a reboot — the channel and
keys persist in `data/one-api.db`.

## What's in the box

| file | role |
|------|------|
| `setup.sh` | one-shot: download binary → init root → create Bedrock channel (with all the fixes) → mint a budgeted key |
| `start.sh` / `stop.sh` | launch / stop new-api + beta_sanitizer |
| `show-key.sh` | print full virtual keys (the UI/API masks them; reads the local DB) |
| `beta_sanitizer.py` | strips the 3 `anthropic-beta` flags Bedrock rejects (Claude Code sends 6) |
| `.env.example` | config template (token, admin pw, ports, key budget) |
| `data/` | created at setup: the binary, `one-api.db`, secrets. **gitignored.** |

## Ports

| port | what | expose? |
|------|------|---------|
| `41030` (SANITIZER_PORT) | **Claude Code entrypoint** | yes — this is the relay |
| `41029` (NEWAPI_PORT) | admin UI + raw new-api API | internal/VPN ideally |

## The fixes setup.sh encodes (why it "just works" with Claude Code on Bedrock)

These are the non-obvious incompatibilities between what Claude Code sends and what Bedrock
accepts; setup.sh configures all of them automatically:

1. **AWS channel, bearer mode.** new-api channel type **33**, `aws_key_type: api_key`, key =
   `<AWS_BEARER_TOKEN_BEDROCK>|<region>`.
2. **Model mapping.** Friendly names → real Bedrock inference-profile ids (edit the `mapping`
   dict in `setup.sh` to add models).
3. **param_override deletes body fields Bedrock rejects** — `context_management`,
   `anthropic_beta`, `betas`.
4. **beta_sanitizer strips header flags Bedrock rejects** —
   `thinking-token-count-2026-05-13`, `prompt-caching-scope-2026-01-05`,
   `advisor-tool-2026-03-01`. (new-api rc.15's own header_override misses these on the AWS
   streaming path, hence the tiny front proxy.)

## Going public (optional)

The relay only needs `SANITIZER_PORT` reachable. Put it behind whatever you use — an SSH
tunnel, nginx, or Cloudflare Tunnel:

```bash
cloudflared tunnel --url http://127.0.0.1:41030      # quick public URL
```

⚠️ **Security:** the Bedrock credential lives only in `data/one-api.db` (channel config);
teammates hold only quota-limited `sk-` keys. Keep the **admin UI (41029) off the public
internet** (or behind VPN/IP allowlist) — if the root password leaks, an attacker can mint
unlimited keys against your Bedrock bill. Issue **small-budget** keys (`KEY_BUDGET_USD`).

## Manage keys

Use the admin UI at `http://<host>:41029` (login `root` / your `ADMIN_PASSWORD`) → **Tokens**
to create per-teammate keys with individual budgets, or **Channels** to add more
providers/models. `./show-key.sh` prints the raw key values (the UI masks them after creation).

## Troubleshooting

- **`Invalid token` / `record not found`** — new-api stores its SQLite **relative to its
  working directory**. Always launch it via `start.sh` (which `cd`s into `data/`); running the
  binary from elsewhere creates a second empty DB.
- **`invalid beta flag`** — beta_sanitizer isn't in the path. Make sure Claude Code points at
  `SANITIZER_PORT` (41030), not new-api directly (41029).
- **`model identifier is invalid`** — the model isn't in the channel `model_mapping`; add it
  in `setup.sh` and re-run, or edit the channel in the admin UI.
- **`token quota is not enough`** — the key hit its budget; raise it in the UI or issue a new
  key. (This is the quota engine working as intended.)

See [`../docs/token-relay-station.md`](../docs/token-relay-station.md) for the full writeup
and the open-source comparison (new-api vs sub2api vs one-api).
