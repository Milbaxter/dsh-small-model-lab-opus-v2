"""Results tables and paired statistics.

    python -m lab.stats table --sweeps p2 [--split heldout]
    python -m lab.stats compare --a p2:standard --b it1:cand --split heldout
    python -m lab.stats tasks --sweeps calib            # per-task pass counts (calibration)

`--a/--b` take <sweep>:<arm>. Paired statistics are over tasks: each task's
pass rate over its k runs, difference B - A, 95% CI from a task-level bootstrap
(10,000 resamples). This is the promotion-gate statistic from docs/PLAN.md.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FAMILIES = ["completion", "recovery", "memory", "longctx"]


def load(sweep: str, arm: str | None = None, split: str | None = None) -> list[dict]:
    out = []
    for f in (ROOT / "runs" / sweep).glob("*/*/r*/result.json"):
        r = json.loads(f.read_text())
        if "tag" not in r or (arm and r["arm"] != arm) or (split and r["split"] not in split.split(",")):
            continue
        out.append(r)
    return out


def per_task(rs: list[dict]) -> dict[str, float]:
    d = defaultdict(list)
    for r in rs:
        d[r["task"]].append(r["pass"])
    return {t: float(np.mean(v)) for t, v in d.items()}


def boot_mean(x: np.ndarray, n: int = 10000, seed: int = 0) -> tuple[float, float, float]:
    if len(x) == 0:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), (n, len(x)))
    m = x[idx].mean(axis=1)
    return float(x.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def summary(rs: list[dict]) -> dict:
    pt = per_task(rs)
    mean, lo, hi = boot_mean(np.array(list(pt.values())))
    solved = sum(r["pass"] for r in rs)
    toks = sum(r["usage"]["prompt_tokens"] + r["usage"]["completion_tokens"] for r in rs)
    cost = sum(r["usage"]["cost"] for r in rs)
    fam = {}
    for f in FAMILIES:
        v = [p for t, p in pt.items() if any(r["task"] == t and r["family"] == f for r in rs)]
        fam[f] = float(np.mean(v)) if v else float("nan")
    return {"tasks": len(pt), "runs": len(rs), "pass": mean, "ci": (lo, hi), "families": fam,
            "tokens_per_run": toks / max(1, len(rs)), "tokens_per_solve": toks / solved if solved else float("inf"),
            "cost": cost, "tags": Counter(r["tag"] for r in rs)}


def paired(a: list[dict], b: list[dict]) -> dict:
    pa, pb = per_task(a), per_task(b)
    common = sorted(set(pa) & set(pb))
    d = np.array([pb[t] - pa[t] for t in common])
    mean, lo, hi = boot_mean(d)
    return {"n_tasks": len(common), "a": float(np.mean([pa[t] for t in common])) if common else float("nan"),
            "b": float(np.mean([pb[t] for t in common])) if common else float("nan"), "diff": mean, "ci": (lo, hi),
            "better": [t for t in common if pb[t] > pa[t]], "worse": [t for t in common if pb[t] < pa[t]]}


def fmt_pct(x: float) -> str:
    return "  n/a" if x != x else f"{100 * x:5.1f}"


def cmd_table(a) -> None:
    for sweep in a.sweeps.split(","):
        arms = sorted({r["arm"] for r in load(sweep)})
        for split in (a.split.split(",") if a.split else ["dev", "heldout", "transfer"]):
            print(f"\n## {sweep} / {split}")
            print(f"{'arm':<22} {'pass%':>6} {'95% CI':>15} " + " ".join(f"{f[:6]:>7}" for f in FAMILIES) +
                  f" {'tok/run':>8} {'tok/solve':>9} {'cost$':>7}  tags")
            for arm in arms:
                rs = load(sweep, arm, split)
                if not rs:
                    continue
                s = summary(rs)
                print(f"{arm:<22} {fmt_pct(s['pass']):>6} [{fmt_pct(s['ci'][0])},{fmt_pct(s['ci'][1])}] " +
                      " ".join(f"{fmt_pct(s['families'][f]):>7}" for f in FAMILIES) +
                      f" {s['tokens_per_run']:8.0f} {s['tokens_per_solve']:9.0f} {s['cost']:7.3f}  " +
                      ", ".join(f"{k}:{v}" for k, v in s["tags"].most_common()))


def cmd_compare(a) -> None:
    sa, aa = a.a.split(":")
    sb, ab = a.b.split(":")
    ra, rb = load(sa, aa, a.split), load(sb, ab, a.split)
    p = paired(ra, rb)
    sa_, sb_ = summary(ra), summary(rb)
    out = {"a": a.a, "b": a.b, "split": a.split, **p,
           "tokens_per_solve_a": sa_["tokens_per_solve"], "tokens_per_solve_b": sb_["tokens_per_solve"],
           "tokens_per_run_a": sa_["tokens_per_run"], "tokens_per_run_b": sb_["tokens_per_run"]}
    if not a.show_tasks:
        out.pop("better"); out.pop("worse")
    print(json.dumps(out, indent=1, default=float))


def cmd_tasks(a) -> None:
    rows = defaultdict(lambda: defaultdict(list))
    for sweep in a.sweeps.split(","):
        for r in load(sweep, a.arm, a.split):
            rows[(r["split"], r["family"], r["task"])][r["arm"]].append((r["pass"], r["tag"]))
    for (split, fam, task), arms in sorted(rows.items()):
        cells = []
        for arm, v in sorted(arms.items()):
            cells.append(f"{arm}={sum(p for p, _ in v)}/{len(v)} [{','.join(t for _, t in v)}]")
        print(f"{split:<8} {fam:<10} {task:<24} " + "  ".join(cells))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("table"); t.add_argument("--sweeps", required=True); t.add_argument("--split", default="")
    c = sub.add_parser("compare"); c.add_argument("--a", required=True); c.add_argument("--b", required=True)
    c.add_argument("--split", default="dev"); c.add_argument("--show-tasks", action="store_true")
    k = sub.add_parser("tasks"); k.add_argument("--sweeps", required=True); k.add_argument("--arm", default=None)
    k.add_argument("--split", default=None)
    a = ap.parse_args()
    {"table": cmd_table, "compare": cmd_compare, "tasks": cmd_tasks}[a.cmd](a)


if __name__ == "__main__":
    main()
