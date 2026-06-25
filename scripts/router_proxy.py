#!/usr/bin/env python3
"""
Bedrock routing PROXY — sits in front of the REAL Claude Code.

Claude Code (via CLAUDE_CODE_USE_BEDROCK + ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:PORT)
sends every model request here as:  POST /model/<modelId>/invoke[-with-response-stream]
with Claude-Code's OWN fully-assembled prompt in the body (messages/system/tools/thinking).

This proxy:
  1. reads the requested modelId from the URL,
  2. applies a ROUTING POLICY to choose the ACTUAL model to serve this turn,
  3. re-signs the request with SigV4 and forwards to real Bedrock,
  4. streams the response back untouched.

Because Claude Code assembles the prompt and drives the loop, there is NO hand-written
agent loop and NO hand-assembled context — eliminating the two flaws Steve flagged.
The only thing we change is which model answers each call.

Policies (env ROUTER_POLICY):
  passthrough     serve exactly what Claude Code asked for (control / sanity)
  all_haiku       force every call to Haiku
  all_sonnet      force every call to Sonnet
  downgrade_main  Claude Code uses a big model for the main loop and Haiku for its own
                  internal small tasks. This policy keeps Claude Code's small-model calls
                  as-is but DOWNGRADES the main-loop (big) model to a cheaper one.
                  i.e. route the heavy reasoning model -> sonnet (or haiku), leave the
                  already-small calls alone. This is the realistic "cut the expensive
                  calls" lever, applied through the real harness.

Every routed call is logged to ROUTER_LOG (jsonl): {orig_model, served_model, n_in, n_out}.
"""
import os, sys, json, http.server, socketserver, urllib.parse, datetime, hashlib, hmac
import boto3, botocore.session
from botocore.awsrequest import AWSRequest
from botocore.auth import SigV4Auth
import urllib.request

REGION = os.environ.get('AWS_REGION', 'us-west-2')
PORT = int(os.environ.get('ROUTER_PORT', '9920'))
POLICY = os.environ.get('ROUTER_POLICY', 'passthrough')
LOG = os.environ.get('ROUTER_LOG', '/tmp/router_calls.jsonl')

HAIKU  = 'us.anthropic.claude-haiku-4-5-20251001-v1:0'
SONNET = 'us.anthropic.claude-sonnet-4-6'
OPUS   = 'us.anthropic.claude-opus-4-8'

ENDPOINT = f'https://bedrock-runtime.{REGION}.amazonaws.com'
# This environment authenticates to Bedrock with a BEARER TOKEN, not SigV4.
BEARER = os.environ.get('AWS_BEARER_TOKEN_BEDROCK')
SESSION = botocore.session.get_session()
CREDS = None if BEARER else SESSION.get_credentials()

def is_small(model_id):
    return 'haiku' in model_id

def choose(orig_model):
    """Return the model id to actually serve, per policy."""
    if POLICY == 'passthrough':
        return orig_model
    if POLICY == 'all_haiku':
        return HAIKU
    if POLICY == 'all_sonnet':
        return SONNET
    if POLICY == 'downgrade_main':
        # leave Claude Code's own small/Haiku utility calls alone; downgrade the big
        # main-loop model (opus/sonnet) to Sonnet/Haiku.
        if is_small(orig_model):
            return orig_model
        return os.environ.get('ROUTER_MAIN_TARGET', SONNET)
    return orig_model

def log_call(orig, served, n_in, n_out):
    try:
        with open(LOG, 'a') as f:
            f.write(json.dumps({'orig': orig.split('.')[-1], 'served': served.split('.')[-1],
                                'n_in': n_in, 'n_out': n_out}) + '\n')
    except Exception:
        pass

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        # /model/<id>/invoke or /invoke-with-response-stream
        path = urllib.parse.unquote(self.path)
        parts = path.split('/')
        try:
            mi = parts.index('model'); orig_model = parts[mi+1]; verb = parts[mi+2]
        except Exception:
            self.send_error(400, 'bad path'); return
        served_model = choose(orig_model)
        # If we route to a DIFFERENT model family, Claude-Code's body may carry params the
        # target model rejects (e.g. Opus-4.8 sends output_config.effort + adaptive thinking,
        # which Haiku 4.5 does not accept -> 400). Strip those when the family changes.
        if served_model != orig_model and is_small(served_model) and not is_small(orig_model):
            try:
                j = json.loads(body)
                j.pop('output_config', None)
                j.pop('thinking', None)
                # interleaved-thinking beta is meaningless without thinking
                if isinstance(j.get('anthropic_beta'), list):
                    j['anthropic_beta'] = [b for b in j['anthropic_beta']
                                           if 'thinking' not in b]
                body = json.dumps(j).encode()
            except Exception:
                pass
        # rebuild path with served model
        new_path = '/'.join(parts[:mi+1] + [served_model, verb])
        url = ENDPOINT + new_path

        if BEARER:
            out_headers = {'Content-Type': 'application/json',
                           'Authorization': f'Bearer {BEARER}'}
        else:
            req = AWSRequest(method='POST', url=url, data=body,
                             headers={'Content-Type': 'application/json'})
            SigV4Auth(CREDS, 'bedrock', REGION).add_auth(req)
            out_headers = dict(req.prepare().headers)
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(url, data=body, method='POST',
                                       headers=out_headers),
                timeout=300)
            resp_body = r.read()
            status = r.status
            ctype = r.headers.get('Content-Type', 'application/json')
        except urllib.error.HTTPError as e:
            resp_body = e.read(); status = e.code; ctype = 'application/json'
        except Exception as e:
            self.send_error(502, f'upstream: {e}'); return

        # best-effort token logging (non-stream JSON only)
        n_in = n_out = 0
        if 'invoke-with-response-stream' not in verb:
            try:
                j = json.loads(resp_body); u = j.get('usage', {})
                n_in, n_out = u.get('input_tokens', 0), u.get('output_tokens', 0)
            except Exception: pass
        log_call(orig_model, served_model, n_in, n_out)

        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)
    def log_message(self, *a): pass

if __name__ == '__main__':
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(('127.0.0.1', PORT), Handler)
    print(f"router_proxy up :{PORT} policy={POLICY} region={REGION} log={LOG}", flush=True)
    srv.serve_forever()
