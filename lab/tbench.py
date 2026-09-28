"""External check: run DSH arms on the pre-registered Terminal-Bench subset.

    python -m lab.tbench --sweep tb --arms standard,candidates/champion --k 3

Each attempt runs inside the task's own Docker image (Terminal-Bench
original-tasks). DSH runs in the container from a relocatable Python mounted
read-only at /opt/dshpy (not on the agent's PATH), talks to the same pinning
proxy via host.docker.internal, and the task is graded by the task's own
run-tests.sh (all tests must pass). Same worker, arm patches, model pins and
ledger as the private-bank runs.

Setup once:  lab/tbench_setup.sh  (clones terminal-bench at the pinned commit,
builds ~/dsh-lab/tb-py with deepseek-harness-sdk for linux/aarch64).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from . import arms as armreg
from . import trace as tr
from .sweep import Ledger

ROOT = Path(__file__).resolve().parent.parent
TB = Path(os.environ.get("LAB_TBENCH", str(ROOT.parent / "tbench")))
TBPY = Path(os.environ.get("LAB_TBPY", str(ROOT.parent / "tb-py")))
PREREG = json.loads((ROOT / "docs" / "terminal-bench-preregistration.json").read_text())
_build_lock = threading.Lock()
_built: set[str] = set()


def sh(cmd: list[str], timeout: float | None = None, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def image(task: str) -> str:
    tag = f"tb-{task}"
    with _build_lock:
        if tag not in _built:
            r = sh(["docker", "build", "-q", "-t", tag, str(TB / "original-tasks" / task)], timeout=1800)
            if r.returncode != 0:
                raise RuntimeError(f"build {task}: {r.stderr[-800:]}")
            _built.add(tag)
    return tag


def container_path(p: Path, arm_dirs: dict[Path, str]) -> str:
    for host, ctr in arm_dirs.items():
        try:
            return ctr + "/" + str(p.resolve().relative_to(host))
        except ValueError:
            continue
    raise ValueError(f"patch {p} is outside mounted dirs")


def run_one(task: str, arm: armreg.Arm, rep: int, sweep: str, ledger: Ledger) -> dict:
    rid = f"{sweep}.{arm.name}.{task}.r{rep}"
    rdir = ROOT / "runs" / sweep / arm.name / task / f"r{rep}"
    res_file = rdir / "result.json"
    if res_file.exists() and "tag" in (prev := json.loads(res_file.read_text())) and prev["tag"] != "INFRA":
        return prev
    shutil.rmtree(rdir, ignore_errors=True)
    (rdir / "home").mkdir(parents=True)
    meta = yaml.safe_load((TB / "original-tasks" / task / "task.yaml").read_text())
    img = image(task)
    workdir = sh(["docker", "image", "inspect", img, "-f", "{{.Config.WorkingDir}}"]).stdout.strip() or "/app"

    mounts = {(ROOT / "harness").resolve(): "/lab/harness"}
    ext_dirs = {p.resolve().parent for p in arm.patches if not str(p.resolve()).startswith(str((ROOT / "harness").resolve()))}
    for i, d in enumerate(sorted(ext_dirs)):
        mounts[d] = f"/lab/ext{i}"
    vol = []
    for host, ctr in mounts.items():
        vol += ["-v", f"{host}:{ctr}:ro"]
    name = rid.replace(".", "-").replace(":", "-")[:120]
    sh(["docker", "rm", "-f", name])
    r = sh(["docker", "run", "-d", "--name", name, "--cpus", "2", "--memory", "4g",
            "-v", f"{TBPY}:/opt/dshpy:ro", "-v", f"{(ROOT / 'lab' / 'worker.py').resolve()}:/lab/worker.py:ro",
            "-v", f"{(rdir / 'home').resolve()}:/dshhome", *vol, img, "sh", "-c", "sleep infinity"])
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-500:])
    faults = ROOT / "runs" / "faults"
    faults.mkdir(parents=True, exist_ok=True)
    (faults / f"{rid}.json").write_text(json.dumps({"max_calls": 40, "mode": "nothink"}))
    tmo = float(meta.get("max_agent_timeout_sec", 900))
    spec = {"prompts": [meta["instruction"]], "home": "/dshhome", "ws": workdir, "profile": arm.profile,
            "patches": [container_path(Path(p), mounts) for p in arm.all_patches()],
            "session_timeout": tmo, "result": "/dshhome/worker.json"}
    env = {"LAB_BASE_URL": f"http://host.docker.internal:18080/r/{rid}/v1", "LAB_API_KEY": "lab-dummy",
           "DSH_TELEMETRY_MODE": "DISABLED", "DSH_PERMISSION_MODE": "danger-full-access",
           "LAB_CONTEXT_WINDOW": "32768", "LAB_MAX_TOKENS": "4096", "HOME": "/root"}
    envargs = [x for k, v in env.items() for x in ("-e", f"{k}={v}")]
    t0 = time.time()
    killed = False
    try:
        w = sh(["docker", "exec", "-w", workdir, *envargs, name, "/opt/dshpy/python/bin/python3", "/lab/worker.py",
                json.dumps(spec)], timeout=tmo + 120)
        log = w.stdout + w.stderr
    except subprocess.TimeoutExpired:
        killed, log = True, "timeout"
    wall = time.time() - t0
    (rdir / "worker.log").write_text(log[-6000:])

    # Grade with the task's own tests (copied in only now, as Terminal-Bench does).
    sh(["docker", "cp", str(TB / "original-tasks" / task / "tests"), f"{name}:/tests"])
    sh(["docker", "cp", str(TB / "original-tasks" / task / "run-tests.sh"), f"{name}:/tests/run-tests.sh"])
    try:
        g = sh(["docker", "exec", "-w", workdir, "-e", "TEST_DIR=/tests", name, "bash", "/tests/run-tests.sh"],
               timeout=float(meta.get("max_test_timeout_sec", 180)) + 300)
        gout = g.stdout + g.stderr
        passed = g.returncode == 0 and " passed" in gout and " failed" not in gout and " error" not in gout.lower().split("short test summary")[-1]
    except subprocess.TimeoutExpired:
        gout, passed = "test timeout", False
    (rdir / "tests.log").write_text(gout[-6000:])
    sh(["docker", "rm", "-f", name])

    result = json.loads((rdir / "home" / "worker.json").read_text()) if (rdir / "home" / "worker.json").exists() else {"sessions": []}
    sessions = tr.read_sessions(rdir / "home")
    summary = tr.summarize(sessions)
    with (rdir / "trace.jsonl").open("w") as f:
        for sid, evs in sessions.items():
            for e in evs:
                f.write(json.dumps(e) + "\n")
    usage = ledger.usage(rid)
    out = {"run": rid, "task": task, "family": "terminal-bench", "split": "tbench", "arm": arm.name, "rep": rep,
           "pass": passed, "tag": tr.tag(passed, result, summary, usage, killed), "grade": {"detail": gout[-800:]},
           "wall": round(wall, 1), "usage": usage, "summary": {k: v for k, v in summary.items() if k != "calls"},
           "finish": [s.get("finish_reason") for s in result.get("sessions", [])],
           "final": (result.get("sessions") or [{}])[-1].get("final_response", "")[:1500],
           "errors": [s.get("error") for s in result.get("sessions", []) if s.get("error")]}
    res_file.write_text(json.dumps(out, indent=1))
    shutil.rmtree(rdir / "home", ignore_errors=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", default="tb")
    ap.add_argument("--arms", default="standard")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--only", default="")
    ap.add_argument("--stop-at-usd", type=float, default=19.0)
    a = ap.parse_args()
    tasks = [t for t in PREREG["tasks"] if not a.only or t in a.only.split(",")]
    arms = [armreg.resolve(s) for s in a.arms.split(",")]
    rng = random.Random(1)
    jobs = []
    for rep in range(1, a.k + 1):
        order = tasks[:]
        rng.shuffle(order)
        for t in order:
            aa = arms[:]
            rng.shuffle(aa)
            jobs += [(t, arm, rep) for arm in aa]
    ledger = Ledger(ROOT / "runs" / "ledger.jsonl")
    print(f"tbench {a.sweep}: {len(tasks)} tasks x {len(arms)} arms x k={a.k} = {len(jobs)} runs", flush=True)
    cost = 0.0
    with ThreadPoolExecutor(a.parallel) as ex:
        futs = []
        for j in jobs:
            futs.append(ex.submit(lambda j=j: None if (ledger.refresh() or ledger.total >= a.stop_at_usd) else run_one(*j, a.sweep, ledger)))
        for i, f in enumerate(as_completed(futs), 1):
            try:
                r = f.result()
            except Exception as e:
                print("RUN-ERROR", repr(e)[:400], flush=True)
                continue
            if r:
                cost += r["usage"]["cost"]
                print(f"[{i}/{len(jobs)}] {r['arm']:<16} {r['task']:<30} r{r['rep']} {r['tag']:<14} calls={r['usage']['calls']} "
                      f"${r['usage']['cost']:.4f} {r['wall']}s sweep=${cost:.3f}", flush=True)
    print(f"DONE tbench {a.sweep}: cost ${cost:.3f}", flush=True)


if __name__ == "__main__":
    main()
