"""Session-log extraction, trace summaries and failure tagging."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import zstandard

TAGS = ["PASS", "INFRA", "TIMEOUT", "MAX_TURNS", "CONTEXT_OVERFLOW", "IDLE_LOOP",
        "WRONG_VERIFY", "BAD_EDIT", "REFUSAL", "REASONING"]

CLAIM_RE = re.compile(r"\b(done|complete[d]?|success(fully)?|fixed|all tests pass|passes|passed|resolved|finished|created)\b", re.I)


def read_sessions(home: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in sorted(home.glob("sessions/*/*/session.v3.jsonl*")):
        raw = f.read_bytes()
        if f.suffix == ".zstd":
            raw = zstandard.ZstdDecompressor().stream_reader(raw).read()
        events = [json.loads(line) for line in raw.decode(errors="replace").splitlines() if line.strip()]
        out[f.parent.name] = events
    return out


def summarize(sessions: dict[str, list[dict]]) -> dict:
    calls = []
    steps = 0
    compactions = 0
    tool_errors = 0
    for sid, events in sessions.items():
        for e in events:
            t = e.get("type", "")
            d = e.get("data", {})
            if t == "step/start":
                steps += 1
            elif t == "tool/call":
                calls.append({"s": sid, "name": d.get("name"), "args": (d.get("arguments") or "")[:400]})
            elif t == "tool/result":
                for c in (d.get("message") or {}).get("content", []):
                    if c.get("isError"):
                        tool_errors += 1
                        if calls:
                            calls[-1]["error"] = True
            elif "compact" in t:
                compactions += 1
    rep = Counter((c["name"], c["args"]) for c in calls)
    max_repeat = max(rep.values()) if rep else 0
    return {
        "steps": steps,
        "tool_calls": len(calls),
        "tool_errors": tool_errors,
        "tools_used": dict(Counter(c["name"] for c in calls)),
        "max_identical_call_repeat": max_repeat,
        "compaction_events": compactions,
        "calls": calls[:200],
    }


def tag(passed: bool, result: dict, summary: dict, usage: dict, killed: bool) -> str:
    if passed:
        return "PASS"
    sessions = result.get("sessions", [])
    statuses = usage.get("statuses", {})
    if killed:
        return "TIMEOUT"
    if not sessions or any(s.get("finish_reason") == "exception" and "budget" not in (s.get("error") or "")
                           for s in sessions) or statuses.get("proxy_error") or statuses.get("budget_refused"):
        return "INFRA"
    if statuses.get("step_budget"):
        return "MAX_TURNS"
    if statuses.get("context_overflow"):
        return "CONTEXT_OVERFLOW"
    if summary["max_identical_call_repeat"] >= 3:
        return "IDLE_LOOP"
    if summary["tool_calls"] == 0:
        return "REFUSAL"
    final = (sessions[-1].get("final_response") or "")
    if CLAIM_RE.search(final):
        return "WRONG_VERIFY"
    edit_errors = sum(1 for c in summary["calls"] if c.get("error") and c["name"] in ("edit", "write", "str_replace_editor", "multi_edit"))
    if edit_errors >= 2:
        return "BAD_EDIT"
    return "REASONING"
