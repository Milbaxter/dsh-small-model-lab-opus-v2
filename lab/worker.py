"""Run one task attempt with one arm. Executed *inside* the seatbelt sandbox.

Each session in the task is a fresh DSH process and a fresh session id that
shares the same DSH home and workspace (so cross-session memory has to come
from the harness or from files, exactly as for a real user).
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback


def main() -> None:
    spec = json.loads(sys.argv[1])
    from deepseek_harness import DeepSeekHarness

    out = {"sessions": []}
    for i, prompt in enumerate(spec["prompts"]):
        t0 = time.time()
        rec = {"index": i}
        try:
            with DeepSeekHarness(
                dsh_home=spec["home"],
                cwd=spec["ws"],
                provider="lab",
                model="qwen/qwen3-8b",
                max_tokens=int(os.environ.get("LAB_MAX_TOKENS", "4096")),
                profile=spec["profile"],
                patches=tuple(spec["patches"]),
                request_timeout_seconds=spec["session_timeout"],
                initialize_timeout_seconds=90,
            ) as h:
                r = h.run(prompt, session_id=f"s{i + 1}")
            rec.update(finish_reason=r.finish_reason, final_response=r.final_response)
        except Exception as e:  # recorded, tagged later
            rec.update(finish_reason="exception", error=f"{type(e).__name__}: {e}"[:2000],
                       tb=traceback.format_exc()[-3000:])
        rec["secs"] = round(time.time() - t0, 1)
        out["sessions"].append(rec)
        with open(spec["result"], "w") as f:
            json.dump(out, f, indent=1)


if __name__ == "__main__":
    os.umask(0o022)
    main()
