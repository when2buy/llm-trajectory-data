import importlib.util, os, json, threading, time, urllib.request, urllib.error, random

os.environ['KP_KEYS'] = '/tmp/keys.json'
spec = importlib.util.spec_from_file_location("kp",
    "/mnt/localssd/token-router/llm-trajectory-data/scripts/keyed_proxy.py")
kp = importlib.util.module_from_spec(spec); spec.loader.exec_module(kp)

json.dump({
 "tk_alice_9f3a": {"owner":"alice@adobe.com","policy":"cheap","monthly_budget_usd":0.00005,"spent_usd":0.0,"disabled":False},
 "tk_bob_2c7d":   {"owner":"bob@adobe.com","policy":"balanced","monthly_budget_usd":50.0,"spent_usd":0.0,"disabled":False},
}, open('/tmp/keys.json','w'), indent=2)

import socketserver
PORT = random.randint(8500, 8999)
srv = socketserver.ThreadingTCPServer(('127.0.0.1', PORT), kp.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.5)

def call(key, text):
    h = {'Content-Type':'application/json'}
    if key: h['Authorization'] = f'Bearer {key}'
    body = json.dumps({"model":"sonnet","max_tokens":40,
        "messages":[{"role":"user","content":[{"type":"text","text":text}]}]}).encode()
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{PORT}/v1/messages", data=body, method='POST', headers=h), timeout=120)
        d = json.loads(r.read())
        txt = ''.join(b.get('text','') for b in d.get('content',[]) if b.get('type')=='text').strip()
        return r.status, txt[:40], d.get('usage')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()).get('error',{}).get('message','')[:60], None

lines = []
s,m,u = call(None, "hi");                              lines.append(f"A. no key            -> {s}  {m}")
s,m,u = call("tk_bob_2c7d", "What is 5+5? number only");lines.append(f"B. bob (balanced)    -> {s}  answer={m!r} usage={u}")
lines.append("C. alice (budget $0.00005):")
for i in range(4):
    s,m,u = call("tk_alice_9f3a", "count to five")
    lines.append(f"   call{i}: {s}  {('OK '+str(u)) if s==200 else m}")
print("RESULTS")
print('\n'.join(lines))
print("\nFINAL LEDGER")
print(json.dumps(json.load(open('/tmp/keys.json')), indent=2))
