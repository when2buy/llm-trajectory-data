#!/usr/bin/env python3
"""
keyed_proxy.py — E1 prototype of a shared "proxy key" service.

Wraps the cross-provider translation core with three middleware layers:
  AUTH   : require Authorization: Bearer tk_...  (virtual keys; real creds stay server-side)
  QUOTA  : reject (429) when a key's spent_usd >= monthly_budget_usd
  METER  : after each call, add served-model cost to the key's spent_usd

This is what turns the proxy into a hand-out-one-key service: teammates set
ANTHROPIC_API_KEY=tk_... and ANTHROPIC_BASE_URL=<proxy>; we route, meter, and cap centrally.

Reuses translation logic from xprovider_proxy.py (imported).
"""
import os, json, http.server, socketserver, urllib.request, urllib.error, importlib.util

# import the translation core
_spec = importlib.util.spec_from_file_location("xp",
    os.path.join(os.path.dirname(__file__), "xprovider_proxy.py"))
xp = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(xp)

PORT = int(os.environ.get('KP_PORT', '8400'))
KEYS_PATH = os.environ.get('KP_KEYS', '/tmp/keys.json')

# price per 1M tokens (in, out) by served-model substring
PRICE = [
    ('opus',        15.0, 75.0),
    ('sonnet',       3.0, 15.0),
    ('haiku',        0.8,  4.0),
    ('gpt-oss-120b', 0.15, 0.60),
    ('gpt-oss-20b',  0.07, 0.30),
]
def price_of(model):
    for sub, i, o in PRICE:
        if sub in model: return i, o
    return 3.0, 15.0  # default ~sonnet

# which served model each policy uses (MVP: single model per policy).
# NOTE: real deployment must pick the BACKEND per model — Claude models go to the native
# Bedrock/Anthropic endpoint, OpenAI models go to /openai/v1. This MVP demo routes all
# policies to OpenAI-family models so one backend (Bedrock /openai/v1) serves everything.
POLICY_MODEL = {
    'quality':  'openai.gpt-oss-120b-1:0',   # (prod: claude-opus, via Anthropic backend)
    'balanced': 'openai.gpt-oss-120b-1:0',   # (prod: claude-sonnet, via Anthropic backend)
    'cheap':    'openai.gpt-oss-20b-1:0',
}

def load_keys():
    try: return json.load(open(KEYS_PATH))
    except Exception: return {}
def save_keys(k):
    json.dump(k, open(KEYS_PATH, 'w'), indent=2)

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
    def _err(self, code, msg):
        self._json(code, {'type':'error','error':{'type':'api_error','message':msg}})

    def do_POST(self):
        if '/v1/messages' not in self.path:
            return self._err(404, 'only /v1/messages')
        # ---- AUTH ----
        auth = self.headers.get('Authorization','')
        token = auth.replace('Bearer ','').replace('x-api-key ','').strip()
        if not token:
            token = self.headers.get('x-api-key','').strip()
        keys = load_keys()
        rec = keys.get(token)
        if not rec or rec.get('disabled'):
            return self._err(401, 'invalid or disabled proxy key')
        # ---- QUOTA ----
        if rec['spent_usd'] >= rec['monthly_budget_usd']:
            return self._err(429, f"budget exhausted: spent ${rec['spent_usd']:.4f} "
                                   f">= cap ${rec['monthly_budget_usd']:.2f}")
        # ---- ROUTE (by key policy) ----
        served = POLICY_MODEL.get(rec.get('policy','balanced'), POLICY_MODEL['balanced'])
        xp.MODEL = served  # the translation core forwards to this model

        l = int(self.headers.get('Content-Length', 0))
        req = json.loads(self.rfile.read(l))
        want_stream = bool(req.get('stream'))
        try:
            oai = xp.anth_to_openai(req)
            resp = xp.call_backend(oai)
            anth = xp.openai_to_anth(resp, req.get('model','proxy'))
        except urllib.error.HTTPError as e:
            return self._err(e.code, f'backend: {e.read()[:200]}')
        except Exception as e:
            return self._err(502, f'forward error: {e}')

        # ---- METER ----
        pi, po = price_of(served)
        cost = (anth['usage']['input_tokens']*pi + anth['usage']['output_tokens']*po)/1e6
        rec['spent_usd'] = round(rec['spent_usd'] + cost, 6)
        keys[token] = rec; save_keys(keys)
        print(f"[meter] {token} policy={rec['policy']} served={served.split('.')[-1]} "
              f"+${cost:.5f} total=${rec['spent_usd']:.4f}/{rec['monthly_budget_usd']}", flush=True)

        out = xp.sse(anth) if want_stream else json.dumps(anth).encode()
        ctype = 'text/event-stream' if want_stream else 'application/json'
        self.send_response(200); self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self,*a): pass

if __name__ == '__main__':
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    print(f"keyed_proxy up :{PORT} keys={KEYS_PATH} backend={xp.BACKEND_URL}", flush=True)
    socketserver.ThreadingTCPServer(('127.0.0.1', PORT), Handler).serve_forever()
