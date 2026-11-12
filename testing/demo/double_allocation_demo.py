"""Live demo: two guards approve two different claims on the SAME item at the
same instant. TraceBack must approve exactly one (FR-CON-01, SCRUM-44, CON-06).

Uses only the Python standard library, so it runs on any laptop:

    python testing/demo/double_allocation_demo.py ^
        --api https://16-176-186-149.sslip.io/api ^
        --guard1 <lf_session cookie of guard 1> --guard2 <lf_session cookie of guard 2> ^
        --claim1 12 --claim2 13

Setup before the demo (in the browser):
  1. Two students claim the same found item (two claims, e.g. CLM-0012 and CLM-0013).
  2. A guard presses "Start review" on both, so both are UNDER_REVIEW.
  3. Sign in as two different guards in two browser profiles and copy each one's
     "lf_session" cookie (DevTools -> Application -> Cookies).

The script fires both approvals behind a barrier (one thread and one connection
each, released together), prints both answers, then reads both claims back.
Expected: one 200 (APPROVED) and one 409 ("Another claim on this item is
already approved"), and exactly one APPROVED claim afterwards.
"""

import argparse
import json
import threading
import time
import urllib.error
import urllib.request


def call(api: str, cookie: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(api.rstrip("/") + path, data=data, method=method,
                                 headers={"Cookie": f"lf_session={cookie}",
                                          "Content-Type": "application/json",
                                          "User-Agent": "traceback-demo"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def claim_id(value: str) -> int:
    """Accepts 12, CLM-0012 or clm-12."""
    return int(value.upper().removeprefix("CLM-"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", required=True, help="e.g. https://16-176-186-149.sslip.io/api")
    p.add_argument("--guard1", required=True, help="lf_session cookie of the first guard")
    p.add_argument("--guard2", required=True, help="lf_session cookie of the second guard")
    p.add_argument("--claim1", required=True, help="first claim (id or CLM-xxxx)")
    p.add_argument("--claim2", required=True, help="second claim on the same item")
    a = p.parse_args()
    c1, c2 = claim_id(a.claim1), claim_id(a.claim2)

    for cookie, cid in ((a.guard1, c1), (a.guard2, c2)):
        status, body = call(a.api, cookie, "GET", f"/claims/{cid}")
        if status != 200:
            raise SystemExit(f"Cannot read claim {cid} as this guard: HTTP {status} {body}")
        print(f"Before: {body.get('ref', cid)}  status={body.get('status')}  item={body.get('item', {}).get('ref', '?')}")

    jobs = [(a.guard1, c1, "Guard 1"), (a.guard2, c2, "Guard 2")]
    barrier = threading.Barrier(len(jobs))
    results: list = [None] * len(jobs)

    def run(i: int) -> None:
        cookie, cid, who = jobs[i]
        barrier.wait()  # both requests leave at the same instant
        start = time.perf_counter()
        status, body = call(a.api, cookie, "POST", f"/staff/claims/{cid}/approve", {"note": None})
        results[i] = (who, cid, status, body, time.perf_counter() - start)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(jobs))]
    print("\nFiring both approvals at the same instant...")
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for who, cid, status, body, secs in results:
        detail = body.get("status") if status == 200 else body.get("detail", body)
        print(f"  {who}: approve claim {cid} -> HTTP {status}  ({secs * 1000:.0f} ms)  {detail}")

    print("\nAfter:")
    approved = 0
    for cookie, cid, _ in jobs:
        status, body = call(a.api, cookie, "GET", f"/claims/{cid}")
        approved += body.get("status") == "APPROVED"
        print(f"  {body.get('ref', cid)}  status={body.get('status')}")
    print(f"\nApproved claims on this item: {approved}  ->  "
          + ("PASS: no double allocation" if approved == 1 else "FAIL"))


if __name__ == "__main__":
    main()
