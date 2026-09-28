"""Pinning proxy between DSH and OpenRouter.

DSH's pi-ai adapter talks to http://127.0.0.1:<port>/r/<run_id>/v1 as an
OpenAI-compatible gateway. The proxy:

* forces the pinned model/provider/sampling settings on every request, so no
  harness arm can change them (and no bigger model can ever be substituted);
* records per-request usage and cost to a JSONL ledger keyed by run id;
* refuses requests once the ledger total reaches the budget cap;
* can inject API-level faults for a run (used by some recovery tasks).

Only stdlib + httpx. The API key is read from the environment, never logged.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

UPSTREAM = "https://openrouter.ai/api/v1/chat/completions"

# Pinned model layer. Changing any of these invalidates comparisons with
# earlier sweeps; record them in every experiment record.
PIN = {
    "model": "qwen/qwen3-8b",
    "provider": {"order": ["alibaba"], "allow_fallbacks": False, "require_parameters": True},
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "reasoning": {"enabled": False},
    "max_tokens_cap": 4096,
}

CONTEXT_WINDOW = 32768  # Qwen3-8B native context (no YaRN); enforced here.

_TOK = None


def estimate_prompt_tokens(body: dict) -> int:
    """Approximate chat-template token count with the real Qwen3 tokenizer."""
    global _TOK
    if _TOK is None:
        from tokenizers import Tokenizer
        _TOK = Tokenizer.from_file(str(Path(__file__).parent / "assets" / "qwen3-tokenizer.json"))
    parts = []
    for m in body.get("messages", []):
        c = m.get("content")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
        parts.append(f"<|im_start|>{m.get('role')}\n{c or ''}")
        for tc in m.get("tool_calls") or []:
            parts.append(json.dumps(tc.get("function", {})))
    if body.get("tools"):
        parts.append(json.dumps(body["tools"]))
    return len(_TOK.encode("\n".join(parts)).ids) + 4 * len(body.get("messages", []))


RUN_RE = re.compile(r"^/r/([A-Za-z0-9_.:-]+)/v1(/.*)$")


class Ledger:
    def __init__(self, path: Path, cap_usd: float):
        self.path = path
        self.cap = cap_usd
        self.lock = threading.Lock()
        self.total = 0.0
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    self.total += float(json.loads(line).get("cost") or 0)
                except ValueError:
                    pass

    def over(self) -> bool:
        return self.total >= self.cap

    def add(self, rec: dict) -> None:
        with self.lock:
            self.total += float(rec.get("cost") or 0)
            with self.path.open("a") as f:
                f.write(json.dumps(rec) + "\n")


class Faults:
    """Per-run API fault plans: faults_dir/<run_id>.json = {"fail_calls": {"3": 429}}."""

    def __init__(self, root: Path | None):
        self.root = root
        self.counts: dict[str, int] = {}
        self.lock = threading.Lock()

    def next(self, run_id: str) -> int | str | None:
        """Return an injected HTTP status, "budget" when the run's call budget is spent, or None."""
        with self.lock:
            n = self.counts.get(run_id, 0) + 1
            self.counts[run_id] = n
        if not self.root:
            return None
        f = self.root / f"{run_id}.json"
        if not f.exists():
            return None
        plan = json.loads(f.read_text())
        if plan.get("max_calls") and n > int(plan["max_calls"]):
            return "budget"
        return plan.get("fail_calls", {}).get(str(n))


def make_handler(ledger: Ledger, faults: Faults, client: httpx.Client, key: str):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet
            pass

        def _json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            m = RUN_RE.match(self.path)
            if m and m.group(2) == "/models":
                return self._json(200, {"object": "list", "data": [{"id": PIN["model"], "object": "model"}]})
            if self.path == "/health":
                return self._json(200, {"ok": True, "spent": round(ledger.total, 4), "cap": ledger.cap})
            self._json(404, {"error": {"message": "not found"}})

        def do_POST(self):
            m = RUN_RE.match(self.path)
            if not m or m.group(2) != "/chat/completions":
                return self._json(404, {"error": {"message": "not found"}})
            run_id = m.group(1)
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            t0 = time.time()
            rec = {"ts": t0, "run": run_id, "req_model": body.get("model")}

            if ledger.over():
                rec.update(status="budget_refused")
                ledger.add(rec)
                return self._json(402, {"error": {"message": "lab budget cap reached", "code": 402}})

            fault = faults.next(run_id)
            if fault == "budget":
                rec.update(status="step_budget")
                ledger.add(rec)
                return self._json(400, {"error": {"message": "lab step budget exhausted for this task attempt",
                                                  "type": "invalid_request_error", "code": "step_budget"}})
            if fault:
                rec.update(status=f"injected_{fault}")
                ledger.add(rec)
                msg = {429: "Rate limit exceeded, retry later", 500: "Internal server error", 503: "Service unavailable"}
                return self._json(int(fault), {"error": {"message": msg.get(int(fault), "error"), "code": int(fault)}})

            if body.get("model") not in (PIN["model"], "qwen3-8b"):
                rec.update(status="model_refused")
                ledger.add(rec)
                return self._json(400, {"error": {"message": f"model {body.get('model')!r} not allowed"}})

            est = estimate_prompt_tokens(body)
            rec["est_tokens"] = est
            out_cap = min(int(body.get("max_tokens") or body.get("max_completion_tokens") or PIN["max_tokens_cap"]), PIN["max_tokens_cap"])
            if est + out_cap > CONTEXT_WINDOW:
                rec.update(status="context_overflow")
                ledger.add(rec)
                return self._json(400, {"error": {
                    "message": f"This model's maximum context length is {CONTEXT_WINDOW} tokens. However, you requested "
                               f"{est + out_cap} tokens ({est} in the messages, {out_cap} in the completion). "
                               "Please reduce the length of the messages or completion.",
                    "type": "invalid_request_error", "param": "messages", "code": "context_length_exceeded"}})

            body["model"] = PIN["model"]
            body["provider"] = PIN["provider"]
            body["temperature"] = PIN["temperature"]
            body["top_p"] = PIN["top_p"]
            body["top_k"] = PIN["top_k"]
            body["reasoning"] = PIN["reasoning"]
            body.pop("reasoning_effort", None)
            for f in ("max_tokens", "max_completion_tokens"):
                if f in body:
                    body[f] = min(int(body[f]), PIN["max_tokens_cap"])
            if "max_tokens" not in body and "max_completion_tokens" not in body:
                body["max_tokens"] = PIN["max_tokens_cap"]
            body["usage"] = {"include": True}
            stream = bool(body.get("stream"))
            if stream:
                body["stream_options"] = {"include_usage": True}

            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                       "X-Title": "dsh-small-model-lab-opus"}
            usage = {}
            provider = None
            try:
                with client.stream("POST", UPSTREAM, json=body, headers=headers) as r:
                    if r.status_code != 200 or not stream:
                        data = r.read()
                        try:
                            j = json.loads(data)
                            usage = j.get("usage") or {}
                            provider = j.get("provider")
                        except ValueError:
                            pass
                        self.send_response(r.status_code)
                        self.send_header("Content-Type", r.headers.get("content-type", "application/json"))
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        rec.update(status=r.status_code)
                        if r.status_code != 200:
                            rec["error"] = data[:300].decode(errors="replace")
                    else:
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Cache-Control", "no-cache")
                        self.send_header("Transfer-Encoding", "chunked")
                        self.end_headers()
                        for line in r.iter_lines():
                            if line.startswith("data: ") and line[6:].strip() not in ("", "[DONE]"):
                                try:
                                    j = json.loads(line[6:])
                                    if j.get("usage"):
                                        usage = j["usage"]
                                    provider = j.get("provider") or provider
                                    if "error" in j:
                                        rec["error"] = json.dumps(j["error"])[:300]
                                except ValueError:
                                    pass
                            chunk = (line + "\n").encode()
                            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.write(b"0\r\n\r\n")
                        rec.update(status=200)
            except (httpx.HTTPError, BrokenPipeError, ConnectionResetError) as e:
                rec.update(status="proxy_error", error=repr(e)[:300])
                try:
                    self._json(502, {"error": {"message": f"upstream error: {e!r}"[:300]}})
                except Exception:
                    pass
            rec.update(
                provider=provider,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                cached_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
                cost=usage.get("cost"),
                secs=round(time.time() - t0, 2),
            )
            ledger.add(rec)

    return H


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--ledger", default="runs/ledger.jsonl")
    ap.add_argument("--cap", type=float, default=19.0, help="hard USD cap across all sweeps")
    ap.add_argument("--faults", default="runs/faults")
    a = ap.parse_args()
    key = os.environ["OPENROUTER_API_KEY"]
    ledger_path = Path(a.ledger)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    fdir = Path(a.faults)
    fdir.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(ledger_path, a.cap)
    client = httpx.Client(timeout=httpx.Timeout(300, connect=20), limits=httpx.Limits(max_connections=64))
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(ledger, Faults(fdir), client, key))
    srv.daemon_threads = True
    print(f"orproxy on 127.0.0.1:{a.port} spent={ledger.total:.4f} cap={a.cap}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
