# Iteration N: <name>

- Date:
- Iteration number:
- Change type (add / edit / remove plugin, tool description, configuration):

## Target failure and baseline evidence (dev only)
## Hypothesis and disconfirmation criterion
## Configs
- Champion: <arm> @ <lab commit>
- Candidate: <arm> @ <lab commit>
- Model / provider / sampling: qwen/qwen3-8b, OpenRouter, provider Alibaba only (no fallbacks), non-thinking, T=0.7, top_p=0.8, top_k=20, max_tokens 4096, 32k context enforced by proxy
- Tasks: task-bank commit b96edba (held-out/transfer frozen); dev 27 / held-out 31 / transfer 16
- Budgets and repetitions: k = 5; step budgets per task.yaml
## Results
| Tier | Champion | Candidate | Paired diff | 95% CI |
|---|---|---|---|---|
| Smoke (dev subset, k=1) | | | | |
| Dev | | | | |
| Held-out | | | | |
| Transfer | | | | |
| Matched-budget control (held-out) | | | | |

- Tokens per solved task; plugin prompt overhead:
- Failure-tag shift (dev):
- Newly solved / regressed dev tasks:
## Decision: promote / reject / revise / insufficient evidence
## Limitations
## Trace locations
