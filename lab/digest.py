"""Proposer input: DEV failure digest only. Held-out/transfer: aggregate numbers only.

    python -m lab.digest --sweep p2 --arm standard [--task c-lru] [--max-tasks 30]
"""
import argparse, json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True); ap.add_argument("--arm", required=True)
    ap.add_argument("--task", default=""); ap.add_argument("--calls", type=int, default=14)
    a = ap.parse_args()
    runs = [json.loads(f.read_text()) for f in (ROOT / "runs" / a.sweep / a.arm).glob("*/r*/result.json")]
    agg = defaultdict(list)
    for r in runs:
        agg[r["split"]].append(r["pass"])
    for s, v in sorted(agg.items()):
        print(f"{s:<9} aggregate pass {sum(v)}/{len(v)} = {100 * sum(v) / max(1, len(v)):.1f}%")
    dev = [r for r in runs if r["split"] == "dev"]          # never held-out / transfer details
    print("\nDEV failure tags:", dict(Counter(r["tag"] for r in dev if not r["pass"]).most_common()))
    by = defaultdict(list)
    for r in dev:
        by[r["task"]].append(r)
    for t, rs in sorted(by.items()):
        if a.task and t != a.task:
            continue
        print(f"\n### {t} ({rs[0]['family']}): {sum(r['pass'] for r in rs)}/{len(rs)}  tags={dict(Counter(r['tag'] for r in rs))}")
        if a.task:
            for r in sorted(rs, key=lambda r: r["rep"]):
                if r["pass"]:
                    continue
                d = ROOT / "runs" / a.sweep / a.arm / t / f"r{r['rep']}"
                calls = json.loads((d / "calls.json").read_text())
                print(f"-- r{r['rep']} {r['tag']} grade: {r['grade'].get('detail', '')[:200]!r}")
                for c in calls[: a.calls]:
                    print(f"   {c['s']} {c['name']}: {c['args'][:160]}{'  [ERR]' if c.get('error') else ''}")
                print(f"   FINAL: {r['final'][:400]!r}")


if __name__ == "__main__":
    main()
