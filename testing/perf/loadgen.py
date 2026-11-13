"""Load generator for TraceBack: simulated students and guards using the site.

Each virtual user signs in with a seeded session (seed_load.py) and repeats
"flows" the way a person does in the browser: open the Found page (the list,
then its photos, 6 at a time like a browser), search, open an item, check
their claims, sometimes report a lost item (with a photo) or claim an item.
Guards open their staff pages.

    python loadgen.py --name baseline --test ramp  --users 5,10,25,50,100 --duration 40
    python loadgen.py --name baseline --test campus --users 100,250,500,1000 --think 5 --duration 60

Results: perf/results/<name>_<test>.json (one entry per step).
"""

import argparse
import asyncio
import json
import random
import statistics
import time
from pathlib import Path

import aiohttp
import psutil

HERE = Path(__file__).resolve().parent
API = "http://127.0.0.1:8000"
TIMEOUT = aiohttp.ClientTimeout(total=15)
WORDS = ["calculator", "wallet", "keys", "bottle", "charger", "earphones", "hoodie", "watch",
         "library", "black", "blue", "cycle", "spectacles", "phone", "notebook", "umbrella bag"]
CATS = ["electronics", "keys", "bags", "wallets-purses", "water-bottles", "clothing", "books-stationery"]
JPEG = (HERE / "phone_photo.jpg").read_bytes()  # a typical 2.9 MB phone photo

# (flow, weight). Weights are the share of page views of each kind.
STUDENT_FLOWS = [("browse_found", 30), ("browse_lost", 12), ("search", 18), ("open_item", 22),
                 ("my_pages", 10), ("report_lost", 4), ("claim_item", 2), ("returned_page", 2)]
GUARD_FLOWS = [("staff_items", 50), ("staff_claims", 50)]


class Recorder:
    def __init__(self):
        self.rows = []  # (t_end, endpoint, ms, status, bytes received)
        self.flows = []  # (t_end, flow, ms, ok)

    def add(self, endpoint, ms, status, nbytes=0):
        self.rows.append((time.monotonic(), endpoint, ms, status, nbytes))


async def call(rec, sess, method, path, endpoint, **kw):
    t = time.perf_counter()
    status = 0
    body = None
    nbytes = 0
    try:
        async with sess.request(method, API + path, **kw) as r:
            status = r.status
            raw = await r.read()
            nbytes = len(raw)
            if r.content_type == "application/json":
                body = json.loads(raw)
    except asyncio.TimeoutError:
        status = -1  # timed out
    except aiohttp.ClientError:
        status = -2  # connection refused/reset
    rec.add(endpoint, (time.perf_counter() - t) * 1000, status, nbytes)
    return status, body


THUMBS = False  # --thumbs: lists ask for ?size=thumb (the optimised frontend)


async def photos(rec, sess, items):
    """The browser loads the list's card photos, about 6 at a time."""
    q = "?size=thumb" if THUMBS else ""
    urls = [f"/items/{i['id']}/photos/{i['cover_photo_id']}{q}" for i in items if i.get("cover_photo_id")]
    sem = asyncio.Semaphore(6)

    async def one(u):
        async with sem:
            await call(rec, sess, "GET", u, "GET photo (thumb)" if THUMBS else "GET photo")

    await asyncio.gather(*(one(u) for u in urls))


async def flow(name, rec, sess, data, me):
    if name in ("browse_found", "browse_lost"):
        kind = "FOUND" if name == "browse_found" else "LOST"
        await call(rec, sess, "GET", "/auth/me", "GET /auth/me")
        await call(rec, sess, "GET", "/categories", "GET /categories")
        page = 1 if random.random() < 0.7 else random.randint(2, 15)
        s, b = await call(rec, sess, "GET", f"/items?kind={kind}&page={page}", "GET /items (list)")
        if s == 200:
            me["last"] = b["items"]
            await photos(rec, sess, b["items"])
    elif name == "search":
        kind = random.choice(["FOUND", "FOUND", "LOST"])
        if random.random() < 0.6:
            path = f"/items?kind={kind}&q={random.choice(WORDS)}"
        else:
            path = f"/items?kind={kind}&category={random.choice(CATS)}"
        s, b = await call(rec, sess, "GET", path, "GET /items (search/filter)")
        if s == 200:
            me["last"] = b["items"]
            await photos(rec, sess, b["items"])
    elif name == "open_item":
        pool = me.get("last") or []
        iid = random.choice(pool)["id"] if pool else random.choice(data["held_found_items"])
        s, b = await call(rec, sess, "GET", f"/items/{iid}", "GET /items/{id}")
        if s == 200:
            ids = (b.get("photo_ids") or [])[:3]
            if ids:  # the big photo
                await call(rec, sess, "GET", f"/items/{iid}/photos/{ids[0]}", "GET photo")
            for pid in ids[1:] if not THUMBS else (ids if len(ids) > 1 else []):
                # the strip of small photos under it (full size before thumbnails existed)
                q = "?size=thumb" if THUMBS else ""
                await call(rec, sess, "GET", f"/items/{iid}/photos/{pid}{q}", "GET photo (thumb)" if THUMBS else "GET photo")
    elif name == "my_pages":
        await call(rec, sess, "GET", "/claims/mine", "GET /claims/mine")
        await call(rec, sess, "GET", "/items/mine", "GET /items/mine")
    elif name == "returned_page":
        await call(rec, sess, "GET", "/items/returned", "GET /items/returned")
    elif name == "report_lost":
        t = random.choice(["Lost calculator", "Lost black wallet", "Lost hostel keys", "Lost blue bottle"])
        payload = {"title": t, "description": t + " near the lecture hall, has a sticker on it.",
                   "category_id": random.randint(1, 13), "colour": "black", "brand": None,
                   "location_text": "Lecture Hall Complex", "happened_on": time.strftime("%Y-%m-%d")}
        s, b = await call(rec, sess, "POST", "/items/lost", "POST /items/lost", json=payload)
        if s == 201 and random.random() < 0.5:
            form = aiohttp.FormData()
            form.add_field("file", JPEG, filename="photo.jpg", content_type="image/jpeg")
            await call(rec, sess, "POST", f"/items/{b['id']}/photos", "POST photo upload", data=form)
    elif name == "claim_item":
        iid = random.choice(data["held_found_items"])
        await call(rec, sess, "POST", f"/items/{iid}/claims", "POST claim",
                   json={"description": "It is mine: it has my initials on the back and a cracked corner."})
    elif name == "staff_items":
        await call(rec, sess, "GET", "/staff/items", "GET /staff/items")
    elif name == "staff_claims":
        await call(rec, sess, "GET", "/staff/claims", "GET /staff/claims")


async def user(idx, rec, data, stop_at, think, guard):
    token = (data["guards"][idx % len(data["guards"])] if guard
             else data["students"][idx % len(data["students"])])
    flows = GUARD_FLOWS if guard else STUDENT_FLOWS
    names, weights = zip(*flows)
    me = {}
    # One connection pool per user, like one browser.
    conn = aiohttp.TCPConnector(limit=6, force_close=False)
    async with aiohttp.ClientSession(cookies={"lf_session": token}, timeout=TIMEOUT, connector=conn) as sess:
        if think:
            await asyncio.sleep(random.uniform(0, think * 2))  # don't all start at once
        while time.monotonic() < stop_at:
            name = random.choices(names, weights)[0]
            t = time.perf_counter()
            n0 = len(rec.rows)
            await flow(name, rec, sess, data, me)
            ok = all(r[3] > 0 and r[3] < 500 for r in rec.rows[n0:])
            rec.flows.append((time.monotonic(), name, (time.perf_counter() - t) * 1000, ok))
            if think:
                await asyncio.sleep(random.expovariate(1 / think))


def server_procs():
    groups = {"api": [], "postgres": [], "website": [], "worker": []}
    for p in psutil.process_iter(["cmdline", "name"]):
        cmd = " ".join(p.info["cmdline"] or [])
        if "uvicorn" in cmd and "app.main" in cmd:
            groups["api"].append(p)
            groups["api"] += p.children(recursive=True)
        elif "app.worker" in cmd:
            groups["worker"].append(p)
        elif p.info["name"] in ("postgres",) or cmd.startswith("postgres"):
            groups["postgres"].append(p)
        elif "next-server" in cmd or "next start" in cmd:
            groups["website"].append(p)
    return groups


async def sampler(samples, stop):
    """Every second: CPU (100 = one full core) and memory of each part.
    Processes are looked up again each time, because PostgreSQL starts a new
    process for each new database connection."""
    me = psutil.Process()
    known = {}

    def tracked(p):
        if p.pid not in known:
            known[p.pid] = p
            try:
                p.cpu_percent(None)
            except psutil.Error:
                pass
        return known[p.pid]

    server_procs()
    psutil.cpu_percent(None)
    tracked(me)
    for ps in server_procs().values():
        for p in ps:
            tracked(p)
    while not stop.is_set():
        await asyncio.sleep(1)
        row = {"t": time.monotonic(), "total_cpu": psutil.cpu_percent(None) * psutil.cpu_count()}
        for g, ps in list(server_procs().items()) + [("loadgen", [me])]:
            cpu = rss = 0.0
            for p in ps:
                p = tracked(p)
                try:
                    cpu += p.cpu_percent(None)
                    rss += p.memory_info().rss
                except psutil.Error:
                    pass
            row[g + "_cpu"] = cpu
            row[g + "_rss_mb"] = rss / 2**20
        samples.append(row)


def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(values) - 1)
    return round(values[f] + (values[c] - values[f]) * (k - f), 1)


def summarise(rec, samples, t0, t1, users, think):
    rows = [r for r in rec.rows if t0 <= r[0] < t1]
    flows = [f for f in rec.flows if t0 <= f[0] < t1]
    dur = t1 - t0
    lat = [r[2] for r in rows]
    errs = [r for r in rows if r[3] >= 500 or r[3] < 0]
    by = {}
    for ep in sorted({r[1] for r in rows}):
        e = [r for r in rows if r[1] == ep]
        el = [r[2] for r in e]
        by[ep] = {"count": len(e), "rps": round(len(e) / dur, 1), "p50": pct(el, 50), "p95": pct(el, 95),
                  "p99": pct(el, 99), "mean": round(statistics.mean(el), 1),
                  "errors": sum(1 for r in e if r[3] >= 500 or r[3] < 0),
                  "client_4xx": sum(1 for r in e if 400 <= r[3] < 500),
                  "avg_kb": round(statistics.mean(r[4] for r in e) / 1024, 1)}
    smp = [s for s in samples if t0 <= s["t"] < t1]
    avg = lambda k: round(statistics.mean(s[k] for s in smp), 1) if smp else None  # noqa: E731
    mx = lambda k: round(max(s[k] for s in smp), 1) if smp else None  # noqa: E731
    fl = [f[2] for f in flows]
    return {
        "users": users, "think_s": think, "seconds": round(dur, 1),
        "requests": len(rows), "rps": round(len(rows) / dur, 1),
        "p50": pct(lat, 50), "p90": pct(lat, 90), "p95": pct(lat, 95), "p99": pct(lat, 99),
        "max": round(max(lat), 1) if lat else None,
        "errors": len(errs), "error_rate_pct": round(100 * len(errs) / max(1, len(rows)), 2),
        "timeouts": sum(1 for r in rows if r[3] == -1),
        "status_counts": {str(k): sum(1 for r in rows if r[3] == k) for k in sorted({r[3] for r in rows})},
        "mb_per_s": round(sum(r[4] for r in rows) / dur / 1e6, 2),
        "mb_per_page_view": round(sum(r[4] for r in rows) / max(1, len(flows)) / 1e6, 3),
        "page_views": len(flows), "page_views_per_s": round(len(flows) / dur, 1),
        "page_p50": pct(fl, 50), "page_p95": pct(fl, 95),
        "cpu": {k: avg(k + "_cpu") for k in ("api", "postgres", "website", "worker", "loadgen")} | {"total": avg("total_cpu")},
        "rss_mb_max": {k: mx(k + "_rss_mb") for k in ("api", "postgres", "website", "worker")},
        "by_endpoint": by,
    }


async def run_step(data, users, duration, think, warmup):
    rec, samples, stop = Recorder(), [], asyncio.Event()
    smp = asyncio.create_task(sampler(samples, stop))
    start = time.monotonic()
    stop_at = start + warmup + duration
    n_guards = users // 25  # about 1 guard per 25 students
    tasks = [asyncio.create_task(user(i, rec, data, stop_at, think, guard=i < n_guards)) for i in range(users)]
    await asyncio.gather(*tasks)
    stop.set()
    await smp
    return summarise(rec, samples, start + warmup, stop_at, users, think)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--test", required=True)
    ap.add_argument("--users", default="10")
    ap.add_argument("--duration", type=float, default=40)
    ap.add_argument("--warmup", type=float, default=8)
    ap.add_argument("--think", type=float, default=0)
    ap.add_argument("--pause", type=float, default=5)
    ap.add_argument("--thumbs", action="store_true", help="lists load thumbnails (after the optimisation)")
    ap.add_argument("--append", action="store_true", help="add to the results file instead of starting it again")
    ap.add_argument("--recovery", action="store_true", help="after the last step, time how long until the API answers again")
    a = ap.parse_args()
    global THUMBS
    THUMBS = a.thumbs
    data = json.loads((HERE / "run" / "tokens.json").read_text())
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    path = out / f"{a.name}_{a.test}.json"
    steps = json.loads(path.read_text())["steps"] if a.append and path.exists() else []
    for n in [int(x) for x in a.users.split(",")]:
        s = await run_step(data, n, a.duration, a.think, a.warmup)
        steps.append(s)
        print(f"{a.name}/{a.test} users={n:5d} rps={s['rps']:7.1f} p50={s['p50']} p95={s['p95']} p99={s['p99']} "
              f"err={s['error_rate_pct']}% to={s['timeouts']} pv/s={s['page_views_per_s']} MB/s={s['mb_per_s']} "
              f"cpu api={s['cpu']['api']} pg={s['cpu']['postgres']} gen={s['cpu']['loadgen']} tot={s['cpu']['total']}",
              flush=True)
        (out / f"{a.name}_{a.test}.json").write_text(json.dumps({"name": a.name, "test": a.test, "steps": steps}, indent=1))
        await asyncio.sleep(a.pause)
    if a.recovery:
        rec = await recovery(data)
        print(f"recovery: {rec}", flush=True)
        (out / f"{a.name}_{a.test}.json").write_text(
            json.dumps({"name": a.name, "test": a.test, "steps": steps, "recovery": rec}, indent=1))


async def recovery(data):
    """After the load stops: how long until a signed-in request works again?"""
    t0 = time.monotonic()
    async with aiohttp.ClientSession(cookies={"lf_session": data["students"][1]},
                                     timeout=aiohttp.ClientTimeout(total=5)) as s:
        while time.monotonic() - t0 < 600:
            try:
                async with s.get(API + "/auth/me") as r:
                    if r.status == 200:
                        return {"recovered": True, "seconds_after_load_stopped": round(time.monotonic() - t0, 1)}
            except (asyncio.TimeoutError, aiohttp.ClientError):
                pass
            await asyncio.sleep(2)
    return {"recovered": False, "seconds_after_load_stopped": 600}


if __name__ == "__main__":
    asyncio.run(main())
