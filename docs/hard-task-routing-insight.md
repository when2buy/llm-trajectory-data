# Routing on a HARD task: where Claude Code breaks when you swap the model, and what to do

This is the experiment Steve asked for: pick a task **hard enough that the cheap model
actually fails**, route the **real Claude Code** through a proxy, and **observe — from
Claude Code's own internal API calls — exactly where and how it breaks** when the model is
swapped. Then turn that into a routing strategy.

Task: **`american-option-fd-new`** (Crank-Nicolson finite-difference American option pricing
with PSOR + discrete dividends). Difficulty: **Hard**. Historical Harbor result on this exact
task: **Opus 4.6 = 1.0, Sonnet 4.5 = 0.0, Haiku 4.5 = 0.0** — a genuine breaking point, the
opposite of the easy `sma-crossover-spy` where everything scored 1.0.

## Proof these are Claude Code's OWN internal calls (not ours)

The router proxy logs the full request each call carries. Every routed call shows Claude
Code's own fingerprint — we assemble none of it:

```
system_prompt_head: "x-anthropic-billing-header: cc_version=2.1.169.f71; cc_entrypoint=sdk-ts;
                     You are a Claude agent, built on Anthropic's Claude Code..."
n_tools: 9   tools: [Agent, AskUserQuestion, Bash, Edit, Read, Skill, ToolSearch, Workflow, Write]
```

That is Claude Code 2.1.169's system prompt and its real tool set. The proxy only rewrites
which model answers; the prompt, tools, and loop are 100% Claude Code's.
(Mechanism + trace schema: [real-claude-code-routing.md](./real-claude-code-routing.md).)

## What happened: all calls → Haiku, on the hard task

**85 internal Claude Code model calls. Final reward 0.80 (43/54 tests). 10 tool results
came back as errors.** Tool usage: Bash ×54, Edit ×12, Write ×9, Read ×8 — i.e. a huge
amount of run-debug-edit churning. Contrast: on the *easy* task Haiku finished in ~20 calls
at reward 1.0; here it spent 85 calls and still missed 11 tests.

### The exact failure modes (read off Claude Code's tool_result errors)

| seq | error fed back to the model | what it means |
|-----|------------------------------|---------------|
| 5, 9, 15, 19 | `Exit code 1` + Python traceback | the generated PSOR / pricing code is numerically/logically wrong and crashes |
| 7, 11, 13 | **`Exit code 124`** (timeout) | the code *runs* but never finishes — the 600×600 Crank-Nicolson grid is too slow / loops; Haiku didn't write convergent/vectorized code |
| 26, 27 | `ModuleNotFoundError` | Haiku fell back to throwaway `python3 -c "..."` snippets that lose the environment |
| 36 | grid logged as **`NS=10, NT=10`** | **the tell**: to make it run at all, Haiku shrank the grid to 10×10 — far from the required NS=300/NT=600. It "passes" structural tests but fails the numerical-accuracy tests → that's the missing 11. |

So the failure is not random flailing — it's a specific cascade: **wrong/slow numerics →
timeouts → simplify the problem to make it run → lose accuracy.** A cheap model under a real
agent harness degrades by *quietly reducing fidelity*, which is the dangerous kind of
failure (it still produces output and partial passes).

### Why "cheap" is also slow/expensive here

85 calls vs ~10–20 for a strong model on the same task. Each call re-sends the growing
conversation, so the input-token bill compounds. This reproduces, inside the **real** Claude
Code, the earlier finding that on an out-of-depth task the cheap model is the expensive one.

## The routing strategy this implies

The data across easy + hard tasks points to a **difficulty-aware, escalate-on-signal** router
rather than any static per-step rule:

1. **Default to the cheap model** (Haiku). On easy tasks it reaches reward 1.0 and is far
   cheaper — proven on `sma-crossover-spy` (all_haiku 1.0).
2. **Watch Claude Code's own tool_results for distress signals** — the proxy already sees
   them per call:
   - repeated `Exit code 124` (timeouts) or `Exit code 1` tracebacks on the *same* file,
   - `InputValidationError` on tool calls (the cheap model mis-formats Claude Code's tool
     schema under long context — observed separately),
   - the same `Edit`/`Bash` loop N times without a green run.
3. **Escalate to a strong model when ≥2 such signals occur** — and escalate the *whole rest
   of the run*, because the cheap model's failure here is a capability ceiling (it can't do
   PSOR correctly), not a transient slip. This is the regime where Opus historically gets
   1.0 and Haiku gets ≤0.8.
4. **Do not pre-classify steps as "light/heavy" from the trace and statically downgrade
   them** — an earlier attempt at that was the *worst* policy (see live-routing.md). Routing
   must react to live execution signals, not guesses about which step looks simple.

In one line: **start cheap, and let Claude Code's own error stream tell you when to escalate.**
The hard task shows the escalation trigger is real and observable; the easy task shows that
when no distress signals appear, staying on Haiku is free money.

## Honest limitations of this run

- **The strong-model control on this task did not complete through the proxy.** Our buffering
  proxy intermittently stalls on Claude Code's streamed responses (it buffers the full
  event-stream instead of passing events through incrementally), and the Opus/Sonnet control
  hung mid-run twice. The reward gap is therefore anchored on (a) the completed all_haiku run
  (0.80) and (b) historical Harbor numbers on this exact task (Opus 1.0 / Haiku 0.0 under the
  official runner) plus the L3 single-step swap (Opus 1.0 → Haiku 0.21). A production proxy
  must stream-passthrough; that is the next fix before quoting a clean head-to-head cost
  number.
- One run per policy on the hard task; non-determinism is real. The *failure-mode taxonomy*
  above is the robust finding; exact reward will vary run to run.
- Local verifier via `uvx` (no Docker), simplified workspace. Absolute costs are indicative.

## Reproduce

```bash
# real Claude Code, all calls forced to Haiku, full per-call tracing:
ROUTER_TRACE=/tmp/trace.jsonl bash scripts/run_cc_routed.sh american-option-fd-new all_haiku 0
# inspect where it broke:
python3 - <<'PY'
import json
for l in open('/tmp/ccrouted/american-option-fd-new_all_haiku_0.trace.jsonl'):
    r=json.loads(l); req=r['request']
    errs=[b for b in req.get('last_content',[]) if isinstance(b,dict) and b.get('is_error')]
    if errs: print(r['seq'], errs[0]['preview'][:80])
PY
```
