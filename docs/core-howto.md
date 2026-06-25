# The core trick — how to make Claude Code call another model

This is the minimal mental model. Everything else (routing rules, cost tracking, multi-provider)
is built on top of this one idea.

## The whole thing in one sentence

> Claude Code sends every model request as an HTTP POST to `${ANTHROPIC_BASE_URL}/v1/messages`.
> Set that env var to your own server, and you own every request — you decide what model
> answers and what to log.

```
                ANTHROPIC_BASE_URL=http://127.0.0.1:8300
   Claude Code  ───────────────────────────────────────►  YOUR PROXY  ──►  any model
   (unchanged)        POST /v1/messages (Anthropic format)              (Claude / GPT / Gemini…)
```

No patching Claude Code, no fork. Just an env var and a small HTTP server.

## The 3 things your server must do

### 1. Accept the Anthropic request
Claude Code POSTs `/v1/messages` with this shape (real capture):
```jsonc
{
  "model": "us.anthropic.claude-sonnet-4-6",
  "system": [{"type":"text","text":"..."}],   // NOTE: a list, not a string
  "messages": [ {"role":"user","content":[{"type":"text","text":"..."}]}, ... ],
  "tools":   [ /* ~28 tool defs, each {name, description, input_schema} */ ],
  "max_tokens": 8000,
  "stream": true,                               // Claude Code streams by default
  "thinking": {...}, "output_config": {...}     // extras you may need to drop
}
```

### 2a. (Easy case) Same provider, just swap the model
If you stay on Bedrock/Anthropic, you only rewrite the model id and forward. That's the
[`router_proxy.py`](../scripts/router_proxy.py) from earlier — ~30 lines of real logic.
**Gotcha:** when downgrading Opus→Haiku you must strip `output_config`/`thinking`, which
Haiku rejects with HTTP 400. (This bug is why an earlier mixed-routing test looked broken.)

### 2b. (Cross-provider) Translate Anthropic ⇄ OpenAI
To reach a non-Anthropic model you translate the body. The only non-trivial parts are tools:

| Anthropic | OpenAI |
|-----------|--------|
| `system` (list of blocks) | first `system` message (joined text) |
| `tools[].input_schema` | `tools[].function.parameters` |
| assistant `tool_use` block `{id,name,input}` | `tool_calls[].function {name, arguments(JSON str)}` |
| user `tool_result` block `{tool_use_id,content}` | a `role:"tool"` message `{tool_call_id,content}` |
| `stop_reason: tool_use/end_turn/max_tokens` | `finish_reason: tool_calls/stop/length` |

The full ~250-line implementation is [`xprovider_proxy.py`](../scripts/xprovider_proxy.py).
The heart of it is two functions — request and response translation:

```python
# Anthropic request  ->  OpenAI request  (tools are the tricky bit)
def anth_to_openai(req):
    msgs = []
    sys = req.get("system")
    if isinstance(sys, list):                       # system: list -> string
        sys = "\n".join(b.get("text","") for b in sys)
    if sys: msgs.append({"role":"system","content":sys})
    for m in req["messages"]:
        if isinstance(m["content"], str):
            msgs.append({"role":m["role"],"content":m["content"]}); continue
        text, tool_calls, tool_results = [], [], []
        for b in m["content"]:
            if b["type"] == "text":         text.append(b["text"])
            elif b["type"] == "tool_use":   tool_calls.append(
                {"id":b["id"],"type":"function",
                 "function":{"name":b["name"],"arguments":json.dumps(b["input"])}})
            elif b["type"] == "tool_result":tool_results.append(
                {"role":"tool","tool_call_id":b["tool_use_id"],"content":b["content"]})
        if m["role"] == "assistant":
            a = {"role":"assistant","content":"\n".join(text) or None}
            if tool_calls: a["tool_calls"] = tool_calls
            msgs.append(a)
        else:
            if text: msgs.append({"role":"user","content":"\n".join(text)})
            msgs.extend(tool_results)               # tool results -> their own messages
    out = {"model": MODEL, "messages": msgs, "max_completion_tokens": req.get("max_tokens",4096)}
    if req.get("tools"):
        out["tools"] = [{"type":"function","function":{
            "name":t["name"],"description":t.get("description","")[:1024],
            "parameters":t.get("input_schema",{})}} for t in req["tools"]]
    return out

# OpenAI response  ->  Anthropic response
def openai_to_anth(resp, model_label):
    msg = resp["choices"][0]["message"]; blocks = []
    if msg.get("content"): blocks.append({"type":"text","text":msg["content"]})
    for tc in msg.get("tool_calls", []):
        blocks.append({"type":"tool_use","id":tc["id"],
                       "name":tc["function"]["name"],
                       "input":json.loads(tc["function"]["arguments"] or "{}")})
    stop = {"tool_calls":"tool_use","length":"max_tokens","stop":"end_turn"}.get(
        resp["choices"][0]["finish_reason"], "end_turn")
    return {"id":resp["id"],"type":"message","role":"assistant","model":model_label,
            "content":blocks or [{"type":"text","text":""}],"stop_reason":stop,
            "usage":{"input_tokens":resp["usage"]["prompt_tokens"],
                     "output_tokens":resp["usage"]["completion_tokens"]}}
```

### 3. Handle streaming
Claude Code sends `stream:true`. Either proxy the upstream stream, or (minimal) call the
backend non-streaming and re-emit the Anthropic SSE event sequence:
```
message_start → (content_block_start → content_block_delta → content_block_stop)* → message_delta → message_stop
```
For `tool_use` blocks the delta carries `input_json_delta.partial_json`. See `sse()` in the proxy.

## Run it (copy-paste)

```bash
# 1. start the proxy (defaults to Bedrock's OpenAI endpoint serving gpt-oss-120b)
XP_PORT=8300 uvx -p 3.11 python scripts/xprovider_proxy.py &

# 2. point Claude Code at it — no other change
ANTHROPIC_BASE_URL=http://127.0.0.1:8300 ANTHROPIC_API_KEY=dummy CLAUDE_CODE_USE_BEDROCK= \
  claude --model sonnet -p "What is 17 * 23?"
# -> 391, produced by gpt-oss-120b (not Claude). Proof in docs/xprovider_demo_calls.jsonl
```

To target any other provider, point the proxy at its OpenAI-compatible endpoint:
```bash
XP_BACKEND_URL=https://openrouter.ai/api/v1/chat/completions \
XP_BACKEND_KEY=$OPENROUTER_KEY XP_MODEL=deepseek/deepseek-r1 \
  uvx -p 3.11 python scripts/xprovider_proxy.py
```

## When to use what

| Need | Use |
|------|-----|
| Swap model within Bedrock/Anthropic | [`router_proxy.py`](../scripts/router_proxy.py) (rewrite model id) |
| Reach a non-Anthropic model, learn the mechanism | [`xprovider_proxy.py`](../scripts/xprovider_proxy.py) (this doc) |
| Production: many providers, routing rules, UI | [claude-code-router](https://github.com/musistudio/claude-code-router) ⭐35k |
| Production gateway: cost tracking, 100+ providers | [litellm](https://github.com/BerriAI/litellm) ⭐51k |

That's the core. A shared, billed "proxy key" service for the team is designed in
[proxy-key-service.md](./proxy-key-service.md).
