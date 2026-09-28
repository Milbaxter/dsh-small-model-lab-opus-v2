"""Leakage check: candidate plugin text must share no task-specific strings with
any task (dev, held-out, transfer). Prints only a verdict and the offending
shingles/identifiers for DEV; for held-out/transfer only a count (the proposer
must not learn held-out content).

    python -m lab.leakage harness/candidates/<name>
"""
import re, sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT.parent / "tasks"
WORD = re.compile(r"[A-Za-z0-9_\-]+")


def shingles(text, n=6):
    w = [x.lower() for x in WORD.findall(text)]
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def ids(text):
    return set(re.findall(r"\b[A-Z]{2,}-\d{3,}\b|\b[\w\-]+\.(?:py|csv|json|txt|md|log|conf|job|db)\b", text))


def main():
    cand = Path(sys.argv[1])
    text = "\n".join(p.read_text(errors="ignore") for p in cand.rglob("*") if p.is_file() and p.suffix in (".js", ".yml", ".yaml", ".md", ".txt"))
    cs, ci = shingles(text), ids(text)
    bad = 0
    for tf in sorted(TASKS.glob("*/*/task.yaml")):
        t = yaml.safe_load(tf.read_text())
        if t["split"] == "smoke":
            continue
        ttext = "\n".join(t["prompts"]) + "\n" + "\n".join(str(p.relative_to(tf.parent / "workspace")) for p in (tf.parent / "workspace").rglob("*"))
        hit = (cs & shingles(ttext)) | (ci & ids(ttext))
        if hit:
            bad += 1
            print(f"LEAK {t['split']}: " + (f"{t['id']}: {sorted(hit)[:5]}" if t["split"] == "dev" else "(details hidden)"))
    print("LEAKAGE CHECK:", "FAIL" if bad else "PASS", f"({bad} tasks)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
