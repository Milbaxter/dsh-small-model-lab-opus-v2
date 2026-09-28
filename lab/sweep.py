"""Resumable sweep runner: tasks x arms x k repetitions.

    python -m lab.sweep --sweep p2 --split dev,heldout --arms minimal,standard,autonomy --k 5

Runs are interleaved (rep -> shuffled tasks -> shuffled arms) so compared arms
share the same time window. A run whose result.json already has a grade is
skipped, so a killed sweep resumes where it stopped.

The task bank lives in a separate private repo (--tasks, default ../tasks or
$LAB_TASKS). Graders and hidden files never enter the agent's workspace.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from . import arms as armreg
from . import trace as tr

ROOT = Path(__file__).resolve().parent.parent
SB = ROOT / "lab" / "run.sb"
PY = sys.executable
PROXY = os.environ.get("LAB_PROXY", "http://127.0.0.1:18080")


def load_tasks(root: Path, splits: list[str], only: set[str] | None) -> list[dict]:
    tasks = []
    for f in sorted(root.glob("*/*/task.yaml")):
        t = yaml.safe_load(f.read_text())
        t["dir"] = f.parent
        if t["split"] in splits and (not only or t["id"] in only):
            tasks.append(t)
    return tasks


class Ledger:
    """Incremental reader of the proxy ledger, aggregated by run id."""

    def __init__(self, path: Path):
        self.path = path
        self.pos = 0
        self.by_run: dict[str, dict] = {}
        self.total = 0.0
        self.lock = threading.Lock()

    def refresh(self) -> None:
        with self.lock:
            if not self.path.exists():
                return
            with self.path.open() as f:
                f.seek(self.pos)
                for line in f:
                    if not line.endswith("\n"):
                        break
                    self.pos += len(line.encode())
                    d = json.loads(line)
                    cost = float(d.get("cost") or 0)
                    self.total += cost
                    r = self.by_run.setdefault(d["run"], {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                                          "cost": 0.0, "statuses": {}})
                    st = str(d.get("status"))
                    r["statuses"][st] = r["statuses"].get(st, 0) + 1
                    if st == "200":
                        r["calls"] += 1
                    r["prompt_tokens"] += d.get("prompt_tokens") or 0
                    r["completion_tokens"] += d.get("completion_tokens") or 0
                    r["cost"] += cost

    def usage(self, run_id: str) -> dict:
        self.refresh()
        return self.by_run.get(run_id, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0, "statuses": {}})


TASKS_ROOT = Path(os.environ.get("LAB_TASKS", str(ROOT.parent / "tasks"))).resolve()


def sandboxed(cmd: list[str], rundir: Path, tmp: Path, env: dict, timeout: float) -> tuple[int, bool, str]:
    params = {"RUNDIR": rundir, "TMP": tmp, "TASKS": TASKS_ROOT, "RUNS": (ROOT / "runs").resolve(),
              "REALHOME": Path.home(), "DOTENV": (ROOT.parent / ".env").resolve()}
    full = ["sandbox-exec", "-f", str(SB)] + [x for k, v in params.items() for x in ("-D", f"{k}={v}")] + cmd
    p = subprocess.Popen(full, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, False, out.decode(errors="replace")[-4000:]
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, _ = p.communicate()
        return -9, True, out.decode(errors="replace")[-4000:]


def base_env(tmp: Path, fakehome: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and not k.startswith("UPCLOUD")}
    env.update(HOME=str(fakehome), TMPDIR=str(tmp) + "/", DSH_TELEMETRY_MODE="DISABLED",
               DSH_PERMISSION_MODE="danger-full-access", PYTHONDONTWRITEBYTECODE="1",
               PATH=f"{Path(PY).parent}:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin")
    return env


def run_one(task: dict, arm: armreg.Arm, rep: int, sweep_dir: Path, sweep: str, ledger: Ledger) -> dict:
    rid = f"{sweep}.{arm.name}.{task['id']}.r{rep}"
    rdir = sweep_dir / arm.name / task["id"] / f"r{rep}"
    res_file = rdir / "result.json"
    if res_file.exists():
        prev = json.loads(res_file.read_text())
        if "tag" in prev:
            return prev
    if rdir.exists():
        shutil.rmtree(rdir)
    ws, home, tmp, fakehome = (rdir / x for x in ("ws", "home", "tmp", "fakehome"))
    for d in (ws, home, tmp, fakehome):
        d.mkdir(parents=True)
    ws = ws.resolve()
    src = task["dir"] / "workspace"
    if src.exists():
        shutil.copytree(src, ws, dirs_exist_ok=True, symlinks=True)
    if (task["dir"] / "setup.py").exists():
        subprocess.run([PY, str(task["dir"] / "setup.py"), str(ws), str(rep)], check=True, timeout=120)

    budget = task.get("budget", {})
    max_calls = int(budget.get("max_calls", 30))
    sess_timeout = float(budget.get("timeout", 480))
    faults_dir = ROOT / "runs" / "faults"
    faults_dir.mkdir(parents=True, exist_ok=True)
    (faults_dir / f"{rid}.json").write_text(json.dumps({"max_calls": max_calls * len(task["prompts"]),
                                                        **(task.get("api_faults") or {})}))

    env = base_env(tmp.resolve(), fakehome.resolve())
    env.update(LAB_BASE_URL=f"{PROXY}/r/{rid}/v1", LAB_API_KEY="lab-dummy")
    spec = {"prompts": task["prompts"], "home": str(home.resolve()), "ws": str(ws), "profile": arm.profile,
            "patches": arm.all_patches(), "session_timeout": sess_timeout,
            "result": str((rdir / "worker.json").resolve())}
    t0 = time.time()
    rc, killed, log = sandboxed([PY, str(ROOT / "lab" / "worker.py"), json.dumps(spec)], rdir.resolve(),
                                tmp.resolve(), env, sess_timeout * len(task["prompts"]) + 90)
    wall = time.time() - t0
    (rdir / "worker.log").write_text(log)
    result = json.loads((rdir / "worker.json").read_text()) if (rdir / "worker.json").exists() else {"sessions": []}

    # Grade outside the agent's view: hidden files are copied next to (not into) the workspace.
    gdir = (rdir / "grade").resolve()
    shutil.copytree(task["dir"], gdir, ignore=shutil.ignore_patterns("workspace"))
    genv = base_env(tmp.resolve(), fakehome.resolve())
    grc, gkilled, gout = sandboxed([PY, str(gdir / "grade.py"), str(ws), str(rdir.resolve() / "worker.json")],
                                   rdir.resolve(), tmp.resolve(), genv, 180)
    try:
        grade = json.loads(gout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        grade = {"pass": False, "detail": "grader output unparseable: " + gout[-500:]}
    passed = bool(grade.get("pass")) and not gkilled

    sessions = tr.read_sessions(home)
    with (rdir / "trace.jsonl").open("w") as f:
        for sid, evs in sessions.items():
            for e in evs:
                e["_session"] = sid
                f.write(json.dumps(e) + "\n")
    summary = tr.summarize(sessions)
    usage = ledger.usage(rid)
    tag = tr.tag(passed, result, summary, usage, killed)
    out = {"run": rid, "task": task["id"], "family": task["family"], "split": task["split"], "arm": arm.name,
           "rep": rep, "pass": passed, "tag": tag, "grade": grade, "wall": round(wall, 1), "usage": usage,
           "summary": {k: v for k, v in summary.items() if k != "calls"},
           "finish": [s.get("finish_reason") for s in result.get("sessions", [])],
           "final": (result.get("sessions") or [{}])[-1].get("final_response", "")[:1500],
           "errors": [s.get("error") for s in result.get("sessions", []) if s.get("error")]}
    (rdir / "calls.json").write_text(json.dumps(summary["calls"], indent=1))
    res_file.write_text(json.dumps(out, indent=1))
    for d in ("home", "tmp", "fakehome", "grade"):
        shutil.rmtree(rdir / d, ignore_errors=True)
    shutil.rmtree(ws, ignore_errors=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True)
    ap.add_argument("--split", default="dev")
    ap.add_argument("--arms", default="standard")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--parallel", type=int, default=6)
    ap.add_argument("--tasks", default=str(TASKS_ROOT))
    ap.add_argument("--only", default="")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--stop-at-usd", type=float, default=18.5, help="stop scheduling when ledger total passes this")
    a = ap.parse_args()

    tasks = load_tasks(Path(a.tasks), a.split.split(","), set(a.only.split(",")) if a.only else None)
    arms = [armreg.resolve(s) for s in a.arms.split(",")]
    sweep_dir = ROOT / "runs" / a.sweep
    sweep_dir.mkdir(parents=True, exist_ok=True)
    (sweep_dir / "meta.json").write_text(json.dumps({"args": vars(a), "arms": [vars(x) | {"patches": x.all_patches()} for x in arms],
                                                     "tasks": [t["id"] for t in tasks], "started": time.time()}, default=str, indent=1))
    rng = random.Random(a.seed)
    jobs = []
    for rep in range(1, a.k + 1):
        order = tasks[:]
        rng.shuffle(order)
        for t in order:
            aa = arms[:]
            rng.shuffle(aa)
            jobs += [(t, arm, rep) for arm in aa]
    ledger = Ledger(ROOT / "runs" / "ledger.jsonl")
    ledger.refresh()
    start_cost = ledger.total
    print(f"sweep {a.sweep}: {len(tasks)} tasks x {len(arms)} arms x k={a.k} = {len(jobs)} runs; ledger ${start_cost:.3f}", flush=True)
    done = 0
    stop = threading.Event()

    def guarded(job):
        if stop.is_set():
            return None
        ledger.refresh()
        if ledger.total >= a.stop_at_usd:
            stop.set()
            return None
        return run_one(*job, sweep_dir, a.sweep, ledger)

    with ThreadPoolExecutor(a.parallel) as ex:
        futs = [ex.submit(guarded, j) for j in jobs]
        for f in as_completed(futs):
            try:
                r = f.result()
            except Exception as e:  # keep the sweep alive; the run will be retried on resume
                print("RUN-ERROR", repr(e)[:300], flush=True)
                continue
            if r is None:
                continue
            done += 1
            print(f"[{done}/{len(jobs)}] {r['arm']:<12} {r['task']:<28} r{r['rep']} {r['tag']:<16} "
                  f"calls={r['usage']['calls']:<3} ${r['usage']['cost']:.4f} {r['wall']}s  sweep=${ledger.total - start_cost:.3f}",
                  flush=True)
    ledger.refresh()
    print(f"DONE sweep {a.sweep}: {done} runs, sweep cost ${ledger.total - start_cost:.3f}, ledger total ${ledger.total:.3f}"
          + ("  (STOPPED: budget)" if stop.is_set() else ""), flush=True)


if __name__ == "__main__":
    main()
