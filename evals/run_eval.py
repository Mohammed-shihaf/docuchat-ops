"""End-to-end answer-quality eval against a running DocuChat instance.

Checks two things that matter for a RAG product:
  * accuracy     - answers contain the expected fact
  * groundedness - questions NOT covered by the document get "I don't know" (no hallucination)
  * citations    - every answered question cites the uploaded document

Usage: python evals/run_eval.py [--url http://localhost:8000] [--min-score 0.8]
Exit code is non-zero if the score is below --min-score, so CI can block a bad prompt/model change.
Stdlib only, so it runs anywhere.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DONT_KNOW = ("i don't know", "i do not know", "not in the context", "not provided", "no information")


def call(method: str, url: str, body: bytes | None = None, ctype: str = "application/json") -> dict:
    req = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": ctype})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:      # respect our own rate limiter
                time.sleep(20)
                continue
            raise
    raise RuntimeError("unreachable")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--min-score", type=float, default=0.8)
    ap.add_argument("--report", default="eval-report.json")
    args = ap.parse_args()

    data = json.loads((Path(__file__).parent / "dataset.json").read_text(encoding="utf-8"))
    doc = data["document"]
    sid = call("POST", f"{args.url}/sessions")["session_id"]
    try:
        call("POST", f"{args.url}/sessions/{sid}/files?name={doc['name']}", doc["text"].encode(), "application/octet-stream")
        results = []
        for case in data["cases"]:
            started = time.perf_counter()
            r = call("POST", f"{args.url}/sessions/{sid}/chat", json.dumps({"question": case["question"]}).encode())
            answer = r["answer"].lower()
            if case.get("expect_dont_know"):
                ok = any(p in answer for p in DONT_KNOW)
                kind = "groundedness"
            else:
                ok = any(e in answer for e in case["expect_any"]) and doc["name"] in r["sources"]
                kind = "accuracy"
            results.append({"question": case["question"], "kind": kind, "pass": ok,
                            "seconds": round(time.perf_counter() - started, 2), "answer": r["answer"][:300]})
            print(f"{'PASS' if ok else 'FAIL'}  [{kind}] {case['question']}")
    finally:
        call("DELETE", f"{args.url}/sessions/{sid}")  # leave nothing behind

    score = sum(r["pass"] for r in results) / len(results)
    by_kind = {k: round(sum(r["pass"] for r in results if r["kind"] == k) / max(1, sum(r["kind"] == k for r in results)), 2)
               for k in ("accuracy", "groundedness")}
    p95 = sorted(r["seconds"] for r in results)[max(0, int(len(results) * 0.95) - 1)]
    report = {"score": round(score, 3), **by_kind, "p95_seconds": p95, "results": results}
    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nscore={score:.0%}  accuracy={by_kind['accuracy']:.0%}  groundedness={by_kind['groundedness']:.0%}  p95={p95}s")
    return 0 if score >= args.min_score else 1


if __name__ == "__main__":
    sys.exit(main())
