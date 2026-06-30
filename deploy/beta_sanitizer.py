#!/usr/bin/env python3
"""
beta_sanitizer.py — tiny header-cleaning proxy in FRONT of new-api.

Claude Code sends an `anthropic-beta` header with flags that AWS Bedrock rejects
(thinking-token-count-*, prompt-caching-scope-*, advisor-tool-*). new-api rc.15's
header_override doesn't strip them on the AWS streaming path, so we drop them here
before forwarding to new-api. Everything else (auth, routing, billing) stays in new-api.

  Claude Code  ──►  this (strip bad beta flags)  ──►  new-api :41029  ──►  Bedrock

Env: BS_PORT (listen), BS_UPSTREAM (new-api base, default http://127.0.0.1:41029)
"""
import os, http.server, socketserver, urllib.request, urllib.error

PORT = int(os.environ.get('BS_PORT', '41030'))
UPSTREAM = os.environ.get('BS_UPSTREAM', 'http://127.0.0.1:41029')

# Bedrock-incompatible beta flags to strip (bisected against real Bedrock)
BAD_FLAGS = {
    'thinking-token-count-2026-05-13',
    'prompt-caching-scope-2026-01-05',
    'advisor-tool-2026-03-01',
}

def clean_beta(value):
    keep = [f.strip() for f in value.split(',') if f.strip() and f.strip() not in BAD_FLAGS]
    return ','.join(keep)

class H(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def _proxy(self, method):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length else None
        url = UPSTREAM + self.path
        # copy headers, sanitizing anthropic-beta
        out_headers = {}
        for k, v in self.headers.items():
            if k.lower() in ('host', 'content-length', 'connection'):
                continue
            if k.lower() == 'anthropic-beta':
                v = clean_beta(v)
                if not v:
                    continue
            out_headers[k] = v
        req = urllib.request.Request(url, data=body, method=method, headers=out_headers)
        try:
            r = urllib.request.urlopen(req, timeout=600)
            data = r.read(); status = r.status
            ctype = r.headers.get('Content-Type', 'application/json')
        except urllib.error.HTTPError as e:
            data = e.read(); status = e.code
            ctype = e.headers.get('Content-Type', 'application/json')
        except Exception as e:
            data = str(e).encode(); status = 502; ctype = 'text/plain'
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_POST(self): self._proxy('POST')
    def do_GET(self): self._proxy('GET')
    def log_message(self, *a): pass

if __name__ == '__main__':
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    print(f"beta_sanitizer up :{PORT} -> {UPSTREAM} (strip {len(BAD_FLAGS)} bad flags)", flush=True)
    socketserver.ThreadingTCPServer(('0.0.0.0', PORT), H).serve_forever()
