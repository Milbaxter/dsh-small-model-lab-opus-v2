"""Promotion gate (docs/PLAN.md). All must hold:
 1 dev improves (paired mean diff > 0)
 2 held-out paired bootstrap 95% CI of per-task diff entirely > 0
 3 no significant transfer regression (transfer CI upper bound not < 0)
 4 beats the matched-budget control on held-out (paired mean diff candidate - control > 0)
 5 tokens per solved task on held-out <= 1.25x champion, unless held-out gain >= 10 points
 6 leakage check passes

    python -m lab.gate --champ p2:standard --cand it1:cand-x --control it1:ctrl-x --leak harness/candidates/x
    (dev/held-out/transfer are read from the named sweeps; a sweep may be "a+b" to merge)
"""
import argparse, json, subprocess, sys
from pathlib import Path
from .stats import load, paired, summary

ROOT = Path(__file__).resolve().parent.parent


def L(ref, split):
    sweeps, arm = ref.split(":")
    out = []
    for s in sweeps.split("+"):
        out += load(s, arm, split)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--champ", required=True); ap.add_argument("--cand", required=True)
    ap.add_argument("--control", default=""); ap.add_argument("--leak", default="")
    a = ap.parse_args()
    res, ok = {}, {}
    d = paired(L(a.champ, "dev"), L(a.cand, "dev"))
    res["dev"] = d; ok["1_dev_improves"] = d["n_tasks"] > 0 and d["diff"] > 0
    h = paired(L(a.champ, "heldout"), L(a.cand, "heldout"))
    res["heldout"] = h; ok["2_heldout_ci_gt_0"] = h["n_tasks"] > 0 and h["ci"][0] > 0
    t = paired(L(a.champ, "transfer"), L(a.cand, "transfer"))
    res["transfer"] = t; ok["3_no_transfer_regression"] = t["n_tasks"] > 0 and not (t["ci"][1] < 0)
    if a.control:
        c = paired(L(a.control, "heldout"), L(a.cand, "heldout"))
        res["vs_control_heldout"] = c; ok["4_beats_matched_budget_control"] = c["n_tasks"] > 0 and c["diff"] > 0
    else:
        ok["4_beats_matched_budget_control"] = False
    sc, sd = summary(L(a.champ, "heldout")), summary(L(a.cand, "heldout"))
    res["tokens_per_solve"] = {"champ": sc["tokens_per_solve"], "cand": sd["tokens_per_solve"]}
    ok["5_token_cost"] = sd["tokens_per_solve"] <= 1.25 * sc["tokens_per_solve"] or h["diff"] >= 0.10
    if a.leak:
        ok["6_leakage"] = subprocess.run([sys.executable, "-m", "lab.leakage", a.leak], cwd=ROOT, capture_output=True).returncode == 0
    for k in ("dev", "heldout", "transfer", "vs_control_heldout"):
        if k in res:
            r = res[k]; r.pop("better", None); r.pop("worse", None)
    print(json.dumps({"results": res, "gates": ok, "PROMOTE": all(ok.values())}, indent=1, default=float))


if __name__ == "__main__":
    main()
