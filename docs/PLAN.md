# Plan

## One-sentence version

Serve Qwen3-8B on a rented GPU, point DSH at it, run a private task bank many times per plugin configuration, let a frontier model propose one plugin change per iteration from failure traces, and promote a change only if it wins on tasks the proposer never saw.

## Architecture: five parts

```
            ┌──────────────────────────────┐
            │ 4. Proposer (frontier model) │  reads DEV failure traces only
            └──────────────┬───────────────┘
                           │ one bounded change
                           ▼
┌───────────────┐   ┌──────────────────────┐   ┌─────────────────────┐
│ 1. Fixed model│──▶│ 3. Runner + tracer   │──▶│ 5. Judge + promotion│
│ Qwen3-8B vLLM │   │ DSH profile × tasks  │   │ gate (held-out,     │
└───────────────┘   │ × k runs, full logs  │   │ transfer, control)  │
                    └──────────▲───────────┘   └─────────┬───────────┘
                               │                         │ champion / reject
                    ┌──────────┴───────────┐             ▼
                    │ 2. Task bank         │      experiment record
                    │ dev / held-out /     │      + git commit
                    │ transfer (private)   │
                    └──────────────────────┘
```

### 1. Fixed model layer
- vLLM serving **Qwen3-8B** (primary subject), pinned version and sampling settings.
- **Second model** for transfer checks: Qwen3-4B-Instruct-2507, and ideally one non-Qwen model (Llama 3.x or OLMo) since harness effects can differ by model family.
- **Ceiling check:** Qwen3-30B-A3B. If a setup fails on 8B but works here, the issue is model capacity, not the harness.
- **Periodic reality check:** real DeepSeek on a small held-out sample every few champions, because tricks that help weak models can hurt strong ones.
- DSH connects through its custom-provider support (`openai-completions` protocol). No DSH code changes needed. See [INFRA.md](INFRA.md).

### 2. Task bank (private)
Three splits, the core defence against benchmark hacking. See [TASKS.md](TASKS.md).
- **Dev (~40):** the proposer sees tasks and failure traces.
- **Held-out (~40):** frozen; the proposer sees only aggregate scores.
- **Transfer (~20):** different domains, run on the second model too.

Four families matching what "intelligence" means here: multi-step completion, recovery under injected faults, cross-session memory, long-context/compaction. Calibrated so baseline Qwen3-8B passes roughly 30–60%.

### 3. Runner + tracer
- Headless DSH via the Python SDK / headless bundle.
- For each configuration: tasks × **k = 5** runs, with step, token and wall-clock budgets.
- Per run, log: pass/fail, tokens (incl. tool-schema overhead), steps, tool chosen vs. expected, malformed calls, errors, wall-clock, full trace.
- Auto-tag failures with the harness-watch taxonomy: `MAX_TURNS`, `IDLE_LOOP`, `WRONG_VERIFY`, `BAD_EDIT`, `CONTEXT_OVERFLOW`, `REASONING`, `INFRA`, `TIMEOUT`, `REFUSAL`.
- Resumable: spot GPUs can be interrupted, so a sweep continues from the last completed run.

### 4. Proposer
- Frontier model (Astra via existing access) reads tagged **dev** failures and proposes **one** bounded change. See [LOOP.md](LOOP.md) for mutation types.
- The only expensive call: once per iteration, not per task.

### 5. Judge + promotion gate
A candidate becomes champion only if **all** hold:
1. Improves on dev.
2. Improves on held-out: paired bootstrap 95% CI of per-task difference above zero.
3. No significant regression on transfer split or second model.
4. Beats a **matched-budget control**: the current champion given the same extra inference (e.g. best-of-3). Rules out gains that are just more compute.
5. Pareto: tokens per solved task within limit (default: no more than +25% unless capability gain is large).
6. Leakage check passes: plugin text shares no task-specific strings with dev tasks.
7. **External benchmark check (final champion only, added 2026-09-28):** plain DSH vs. champion on a fixed, pre-registered Terminal-Bench subset (Docker), paired, k = 3, never used for tuning. Reported separately; floor effects and possible public-benchmark contamination noted.

## Anti-benchmark-hacking rules
- The proposer never sees held-out tasks, graders or per-task held-out results.
- Held-out and transfer tasks are refreshed periodically; retired held-out tasks may move to dev, never the reverse.
- The task bank lives in a **private** repo; public tasks leak into training data.
- Watch the gap: if dev keeps rising while held-out stays flat, the loop is memorising, not learning. Pause and meta-review.
- Removal and simplification count as improvements. A plugin that adds tokens without measured gain is removed.

## Metrics (from agent-intelligence-lab Pareto framework)
| Axis | Primary measure |
|---|---|
| Capability | Pass rate at fixed budget, paired vs. champion |
| Recovery | Pass rate on fault-injected tasks |
| Memory | Pass rate on cross-session tasks |
| Context | Pass rate on compaction-forcing tasks |
| Cost | Tokens per solved task; prompt overhead per plugin |
| Robustness | Malformed-call rate, idle-loop rate |

## Shannon principles applied
- **Noisy channel:** small models are high-variance, so k = 5 and paired statistics are mandatory. One run per setup is noise.
- **Channel capacity:** every plugin costs context tokens and must earn them through measured information gain (ablation: success with vs. without).
- **Error detection before correction:** schema validation and fault tagging first; retries and fixes second.
- **Held-out split as the loop's error-detecting code:** divergence between dev and held-out signals corruption (overfitting).

## Build phases

| Phase | Deliverable | Rough effort |
|---|---|---|
| **P0 Plumbing** | L40S up, vLLM + Qwen3-8B, DSH custom provider, one task run headless with trace saved | 1–2 days |
| **P1 Bench** | 60–100 tasks across 4 families, graders, fault injector, failure tagger, paired stats | 3–5 days |
| **P2 Baselines** | `sdk-minimal` vs. `standard` vs. `standard + autonomy-policy`: first real test of the v1 plugin | 1 day + GPU time |
| **P3 Loop** | Proposer → smoke → dev → held-out → gate → record → commit; hooked into the existing 12-hour Astra cycle, no new scheduler | 3–5 days |
| **P4 Transfer** | Second model, ceiling model, periodic DeepSeek checks; meta-review every ~10 iterations | ongoing |

### First milestone (definition of done for P0–P2)
One results table: 3 configurations × dev + held-out × k = 5, with pass rate and CI, tokens per solve, and failure-tag breakdown. Answers the open question from optimal-deepseek-harness-setup: *does the autonomy policy help at all?*

## Open decisions
- Code repo: this repo (public) for code and plans; task bank in a separate private repo (name TBD).
- Non-Qwen transfer model: Llama 3.x vs. OLMo.
- Proposer model: Astra (existing access) vs. alternatives; keep the author and reviewer passes separate.
