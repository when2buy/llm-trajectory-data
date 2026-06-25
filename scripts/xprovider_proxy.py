#!/usr/bin/env python3
"""
xprovider_proxy.py — let the REAL Claude Code call a NON-Anthropic model.

Claude Code is pointed here via ANTHROPIC_BASE_URL=http://127.0.0.1:PORT. It sends native
Anthropic Messages requests (POST /v1/messages). This proxy TRANSLATES them to the OpenAI
Chat Completions format, forwards to an OpenAI-compatible backend (here: AWS Bedrock's
/openai/v1 endpoint serving gpt-oss — a genuinely non-Claude model), and translates the
OpenAI response back to Anthropic format so Claude Code is none the wiser.

This is the minimal version of what claude-code-router / litellm / claude-code-proxy do.
It demonstrates the core mechanism end-to-end with real code:
  Anthropic /v1/messages  <->  OpenAI /chat/completions   (incl. tools / tool_use / tool_result)

Scope: handles text + tool calling, the parts Claude Code actually needs. Streaming requests
(stream:true) are served by calling the backend non-streaming and re-emitting a single
Anthropic SSE message_* event sequence — enough for Claude Code to function.

Env:
  XP_PORT          listen port (default 8300)
  XP_BACKEND_URL   OpenAI-compatible chat/completions URL
                   (default: Bedrock openai endpoint in $AWS_REGION)
  XP_BACKEND_KEY   bearer token for backend (default: $AWS_BEARER_TOKEN_BEDROCK)
  XP_MODEL         backend model id (default: openai.gpt-oss-120b-1:0)
  XP_LOG           jsonl request log (default /tmp/xprovider.jsonl)
"""
import os, json, time, http.server, socketserver, urllib.request, urllib.error

PORT = int(os.environ.get('XP_PORT', '8300'))
REGION = os.environ.get('AWS_REGION', 'us-west-2')
BACKEND_URL = os.environ.get('XP_BACKEND_URL',
    f'https://bedrock-runtime.{REGION}.amazonaws.com/openai/v1/chat/completions')
BACKEND_KEY = os.environ.get('XP_BACKEND_KEY', os.environ.get('AWS_BEARER_TOKEN_BEDROCK', ''))
MODEL = os.environ.get('XP_MODEL', 'openai.gpt-oss-120b-1:0')
LOG = os.environ.get('XP_LOG', '/tmp/xprovider.jsonl')


# ---------- Anthropic -> OpenAI ----------
def anth_to_openai(req):
    out_msgs = []
    # system (Anthropic sends a string OR a list of {type:text,text})
    sys = req.get('system')
    if isinstance(sys, list):
        sys = '\n'.join(b.get('text', '') for b in sys if isinstance(b, dict))
    if sys:
        out_msgs.append({'role': 'system', 'content': sys})

    for m in req.get('messages', []):
        role = m['role']
        content = m['content']
        if isinstance(content, str):
            out_msgs.append({'role': role, 'content': content})
            continue
        # content is a list of blocks
        text_parts = []
        tool_calls = []
        tool_results = []  # become separate 'tool' role messages
        for b in content:
            t = b.get('type')
            if t == 'text':
                text_parts.append(b.get('text', ''))
            elif t == 'tool_use':
                tool_calls.append({
                    'id': b['id'], 'type': 'function',
                    'function': {'name': b['name'],
                                 'arguments': json.dumps(b.get('input', {}))}
                })
            elif t == 'tool_result':
                # Anthropic puts tool results in a user message; OpenAI wants role:tool
                c = b.get('content', '')
                if isinstance(c, list):
                    c = '\n'.join(x.get('text', '') if isinstance(x, dict) else str(x) for x in c)
                tool_results.append({'role': 'tool', 'tool_call_id': b.get('tool_use_id'),
                                     'content': c or '(no output)'})
        if role == 'assistant':
            msg = {'role': 'assistant', 'content': '\n'.join(text_parts) or None}
            if tool_calls:
                msg['tool_calls'] = tool_calls
            out_msgs.append(msg)
        else:  # user
            if text_parts:
                out_msgs.append({'role': 'user', 'content': '\n'.join(text_parts)})
            out_msgs.extend(tool_results)  # tool results follow

    oai = {'model': MODEL, 'messages': out_msgs,
           'max_completion_tokens': req.get('max_tokens', 4096)}
    # translate tools
    if req.get('tools'):
        oai['tools'] = [{'type': 'function',
                         'function': {'name': t['name'],
                                      'description': t.get('description', '')[:1024],
                                      'parameters': t.get('input_schema', {})}}
                        for t in req['tools']]
    return oai


# ---------- OpenAI -> Anthropic ----------
def openai_to_anth(resp, model_label):
    choice = (resp.get('choices') or [{}])[0]
    msg = choice.get('message', {})
    blocks = []
    text = msg.get('content') or ''
    # gpt-oss wraps chain-of-thought in <reasoning>...</reasoning> — strip for cleanliness
    if '</reasoning>' in text:
        text = text.split('</reasoning>')[-1].strip()
    if text:
        blocks.append({'type': 'text', 'text': text})
    for tc in (msg.get('tool_calls') or []):
        fn = tc.get('function', {})
        try:
            args = json.loads(fn.get('arguments', '{}'))
        except Exception:
            args = {}
        blocks.append({'type': 'tool_use', 'id': tc.get('id', 'call_x'),
                       'name': fn.get('name'), 'input': args})
    if not blocks:
        blocks.append({'type': 'text', 'text': ''})
    fr = choice.get('finish_reason', 'stop')
    stop = {'tool_calls': 'tool_use', 'length': 'max_tokens',
            'stop': 'end_turn'}.get(fr, 'end_turn')
    u = resp.get('usage', {})
    return {'id': resp.get('id', 'msg_x'), 'type': 'message', 'role': 'assistant',
            'model': model_label,
            'content': blocks, 'stop_reason': stop, 'stop_sequence': None,
            'usage': {'input_tokens': u.get('prompt_tokens', 0),
                      'output_tokens': u.get('completion_tokens', 0)}}


def call_backend(oai_body):
    data = json.dumps(oai_body).encode()
    req = urllib.request.Request(BACKEND_URL, data=data, method='POST',
        headers={'Content-Type': 'application/json',
                 'Authorization': f'Bearer {BACKEND_KEY}'})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def sse(anth_msg):
    """Render a complete Anthropic-style SSE stream for one message."""
    def ev(t, d): return f"event: {t}\ndata: {json.dumps(d)}\n\n"
    parts = []
    parts.append(ev('message_start', {'type': 'message_start',
        'message': {**{k: anth_msg[k] for k in ('id','type','role','model')},
                    'content': [], 'stop_reason': None,
                    'usage': anth_msg['usage']}}))
    for i, blk in enumerate(anth_msg['content']):
        if blk['type'] == 'text':
            parts.append(ev('content_block_start', {'type':'content_block_start','index':i,
                'content_block':{'type':'text','text':''}}))
            parts.append(ev('content_block_delta', {'type':'content_block_delta','index':i,
                'delta':{'type':'text_delta','text':blk['text']}}))
        else:  # tool_use
            parts.append(ev('content_block_start', {'type':'content_block_start','index':i,
                'content_block':{'type':'tool_use','id':blk['id'],'name':blk['name'],'input':{}}}))
            parts.append(ev('content_block_delta', {'type':'content_block_delta','index':i,
                'delta':{'type':'input_json_delta','partial_json':json.dumps(blk['input'])}}))
        parts.append(ev('content_block_stop', {'type':'content_block_stop','index':i}))
    parts.append(ev('message_delta', {'type':'message_delta',
        'delta':{'stop_reason':anth_msg['stop_reason'],'stop_sequence':None},
        'usage':{'output_tokens':anth_msg['usage']['output_tokens']}}))
    parts.append(ev('message_stop', {'type':'message_stop'}))
    return ''.join(parts).encode()


def log(orig_model, oai_body, anth_msg):
    try:
        with open(LOG, 'a') as f:
            f.write(json.dumps({'claude_asked': orig_model, 'backend_model': MODEL,
                'n_in': anth_msg['usage']['input_tokens'],
                'n_out': anth_msg['usage']['output_tokens'],
                'n_tools_sent': len(oai_body.get('tools', [])),
                'stop': anth_msg['stop_reason']}) + '\n')
    except Exception:
        pass


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def _err(self, code, msg):
        b = json.dumps({'type':'error','error':{'type':'api_error','message':msg}}).encode()
        self.send_response(code); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        if '/v1/messages' not in self.path:
            return self._err(404, 'only /v1/messages')
        l = int(self.headers.get('Content-Length', 0))
        req = json.loads(self.rfile.read(l))
        want_stream = bool(req.get('stream'))
        try:
            oai = anth_to_openai(req)
            resp = call_backend(oai)
            anth = openai_to_anth(resp, req.get('model', 'proxy'))
        except urllib.error.HTTPError as e:
            return self._err(e.code, f'backend: {e.read()[:200]}')
        except Exception as e:
            return self._err(502, f'translate/forward error: {e}')
        log(req.get('model'), oai, anth)
        if want_stream:
            body = sse(anth)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
        else:
            body = json.dumps(anth).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass


if __name__ == '__main__':
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    print(f"xprovider_proxy up :{PORT} -> {MODEL} @ {BACKEND_URL}", flush=True)
    socketserver.ThreadingTCPServer(('127.0.0.1', PORT), Handler).serve_forever()
