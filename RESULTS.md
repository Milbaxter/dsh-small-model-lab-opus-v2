# Results: DSH small-model lab (Opus side)

Status 2026-09-28 13:36 EEST: **stopped early at the owner's request.** P0 and P1 are done. P2 ran to k=2 for all arms; the partial k=3 runs are excluded. **P3 (improvement loop) was not run: 0 of the allowed 5 iterations.** One candidate (the stop guard) is built and mechanically tested but **not evaluated**. The champion therefore remains **plain DSH (`standard`)**.

## Setup (pinned for every arm)
- **Model:** `qwen/qwen3-8b` via OpenRouter, provider Alibaba only (the model's only provider there), `allow_fallbacks: false`.
- **Mode:** non-thinking, temperature 0.7, top_p 0.8, top_k 20, max_tokens 4096.
- **Context:** 32,768-token window (Qwen3-8B native, no YaRN), enforced by the local proxy with the standard `context_length_exceeded` error.
- **Pinning:** all settings are forced by `lab/orproxy.py`, which also logs tokens and cost per run and holds the real API key. DSH only ever sees a dummy key.
- **DSH:** Python SDK `deepseek-harness-sdk 0.1.5rc1`, custom `openai-completions` provider (`harness/arms/lab-provider.patch.yml`).
- **Disabled in every arm:** session-log upload and OTel telemetry. Web tools are also off (tasks are offline, and web search needs a DeepSeek key).
- **Isolation:** every run gets a fresh DSH home, workspace and fake `$HOME`, and runs inside a macOS seatbelt sandbox. It can only write its own run directory, cannot read the task bank, other runs or secrets, and has network access only to the proxy. DSH's inner sandbox is off (`danger-full-access`) for all arms, since approval prompts can't be answered headlessly.
- **Arms:**
  - `minimal` = shipped `sdk-minimal` profile (one bash tool) plus the pi-ai adapter
  - `standard` = shipped `sdk` profile
  - `autonomy` = `standard` + autonomy-policy plugin, verbatim from optimal-deepseek-harness-setup

## Task bank (private: Milbaxter/dsh-tasks-opus-v2)
- **74 tasks, 4 families:**

  | Split | Tasks | Completion | Recovery | Memory | Long-context |
  |---|---|---|---|---|---|
  | Dev | 27 | 10 | 7 | 6 | 4 |
  | Held-out | 31 | 11 | 8 | 7 | 5 |
  | Transfer (different domains) | 16 | 5 | 4 | 4 | 3 |

- **Grading:** programmatic hidden graders. Every grader is verified to fail on the starting state and pass on the reference solution (`src/verify.py`: 74/74 OK).
- **Faults:** the recovery family has workspace faults (rate-limited or flaky commands, permission denied, malformed config, moved input, missing dependency, hanging command, stale lock, merge conflict, renamed tool, encoding, BOM) and proxy-injected API 429/500/503 errors.
- **Memory tasks:** 2–3 fresh sessions each.
- **Long-context tasks:** procedurally generated, and they overflow a 32k window if read naively.
- **Frozen:** held-out and transfer at task-bank commit `b96edba` before any loop iteration. Hashes only are in `docs/freeze.json`.
- **Calibration miss:** the baseline is about 10% (see below), far below the 30–60% target. I did not ease the tasks. The dominant failure is behavioral (the model ends its turn describing a fix instead of applying it, even on trivial tasks), so easier tasks would not have fixed it; a harness change could.

## P2 baselines (k=2 per task, paired over tasks, 95% bootstrap CI)

| Split | minimal | standard | standard + autonomy-policy |
|---|---|---|---|
| Dev (27) | 0.0% | 9.3% [0.0, 20.4] | 9.3% [0.0, 20.4] |
| Held-out (31) | 0.0% | 4.8% [0.0, 12.9] | 3.2% [0.0, 9.7] |
| Transfer (16) | 0.0% | 9.4% [0.0, 25.0] | 9.4% [0.0, 18.8] |
| Tokens per run (dev) | 1.5k | 73k | 78k |

Paired differences:

| Comparison | Dev | Held-out | Transfer |
|---|---|---|---|
| standard − minimal | +9.3 [0.0, 20.4] | +4.8 [0.0, 12.9] | +9.4 [0.0, 25.0] |
| autonomy − standard | 0.0 [−5.6, 5.6] | −1.6 [−4.8, 0.0] | 0.0 [−12.5, 12.5] |

The top failure tag is `STOPPED_EARLY` in every arm and split. It is almost the only failure for `minimal`.

**Answer to the open question from optimal-deepseek-harness-setup:** on this model and bank, the autonomy-policy plugin shows **no measurable benefit** over plain DSH. The held-out point estimate is −1.6 points, the CI touches zero, and it costs about 7% more tokens.

## P3 improvement loop

| Iteration | Change | Dev | Held-out | Decision |
|---|---|---|---|---|
| none run | — | — | — | — |

Prepared but not evaluated:
- **`harness/candidates/stop-guard`:** a bounded Stop hook (`agent/turn-stopping` → `agent.steer`, at most 2 nudges per turn). It targets `STOPPED_EARLY`, the top dev failure tag.
- **Mechanics check:** on 3 dev tasks it fired and the model resumed work. This is not evidence of a gain.
- **Gate check:** it passes the leakage check. No gate has been run.

## Champion and cross-evaluation
- **Champion:** plain DSH (`standard`), because no candidate passed the gate. Nothing needs installing.
- **Bundle:** `bundle/dsh-lab-opus-stop-guard` packages the **unvalidated** stop guard as a DSH bundle. Install with `dsh plugin --profile <p> add file:$PWD/bundle/dsh-lab-opus-stop-guard`, or use `--patch bundle/dsh-lab-opus-stop-guard/direct.patch.yml`.
- **One command:** `OPENAI_COMPAT_BASE_URL=… OPENAI_COMPAT_API_KEY=… ./run.sh <workspace> "<prompt>"`.
- **Evaluating an external profile on my held-out split:** `python -m lab.sweep --sweep xeval --split heldout --arms standard,ext:/path/to/bundle --k 5`. It accepts a dir with `arm.yaml` or a plain DSH bundle package (`package.json` → `dsh.bundle.patch`). Then run `python -m lab.stats compare --a xeval:standard --b xeval:ext-<name> --split heldout`.
- **External benchmark:** a Terminal-Bench subset (20 easy tasks) was pre-registered in `docs/terminal-bench-preregistration.json`. The in-container runner (`lab/tbench.py`) works: `hello-world` passed. It was not run, because there is no champion to compare.

## Spend and time
- **Spend:** $4.63 on this lab's ledger (`runs/ledger.jsonl`), within the $20 cap. That covers calibration $1.05, the thinking-mode pilot $0.44, P2 about $3.0, and smoke/mechanics tests. The OpenRouter account total ($13.42) includes other users of the same key.
- **Thinking-mode pilot:** thinking cost 2.3× per run and was 3.8× slower, with no pass-rate gain (1/10 vs 1/10). The owner chose non-thinking.
- **Wall-clock:** about 2 h 40 m of model time (10:57–13:36 EEST).

## Honest read
- **What is real:** DSH `standard` beats `minimal`. Minimal scored 0/148 runs, although with k=2 the task-level CI lower bound is exactly 0. Autonomy-policy vs standard is null.
- **Noise level:** everything else is within noise. At a ~5–10% baseline with k=2, the CIs are ±10 points.
- **Main finding:** the bottleneck for Qwen3-8B in DSH is stopping early (it describes the fix and ends the turn), not knowledge or long context. That is a harness-addressable target, and the stop guard is the natural first iteration. It is untested.
- **Low baseline:** a baseline this low limits statistical power. Any follow-up should run k=5 and consider a slightly easier dev tier.

## Blockers and deviations
- **UpCloud:** the core, RAM and IPv4 quotas are exhausted, and a new IPv6-only box was unreachable from this Mac. Everything ran locally, as the owner decided, and the unused box was deleted.
- **Repo name clash:** Astra's session was pushing into `dsh-small-model-lab-opus` / `dsh-tasks-opus`, so this work moved to `-opus-v2`. None of Astra's task files were read.
- **Network outage:** a ~13-minute outage on the Mac invalidated 46 calibration runs, which were tagged INFRA. The proxy now waits out connectivity failures.
- **Stopped at the owner's request:** the run stopped before k=5 and before any loop iteration.
