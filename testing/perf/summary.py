"""Print the key numbers of every results file (used for the report)."""
import json, sys
from pathlib import Path
R = Path(__file__).parent / "results"
for f in sorted(R.glob("*.json")):
    d = json.loads(f.read_text())
    print(f"== {f.name}" + (f"  recovery={d['recovery']}" if "recovery" in d else ""))
    for s in d["steps"]:
        ok = s["requests"] - s["errors"]
        print(f"  users={s['users']:4d} ok_rps={ok/s['seconds']:6.1f} p50={s['p50']} p95={s['p95']} p99={s['p99']} "
              f"err%={s['error_rate_pct']} pv/s={s['page_views_per_s']} page_p95={s['page_p95']} "
              f"MB/pv={s['mb_per_page_view']} MB/s={s['mb_per_s']} cpu={s['cpu']} api_rss={s['rss_mb_max']['api']}")
