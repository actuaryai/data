"""Stage 1+2 of the dataset pipeline.

collect  : pull recent pools from GeckoTerminal, remember the FIRST sighting of each pool.
resolve  : for pools first seen >= 24h ago, fetch the pool again and store the outcome.

State lives in data/state.json  {pool_address: {"first": {...}, "outcome": {...}|null}}.
Run `python feeder/collect.py collect [pages]` hourly and `python feeder/collect.py resolve` daily
(GitHub Actions; public API, ~2s between calls).
"""
import json, sys, time, datetime, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from snapshot_pools import get, slim, BASE  # noqa: E402
from shards import write_shards, write_symbol_index  # noqa: E402
from odds import write_odds  # noqa: E402

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
STATE = DATA / "state.json"
NOW = lambda: datetime.datetime.now(datetime.timezone.utc)


def load():
    return json.loads(STATE.read_text(encoding="utf8")) if STATE.exists() else {}


def save(st):
    DATA.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1), encoding="utf8")


def metrics(row):
    tx = row.get("tx_h24") or {}
    f = lambda k: float(row[k]) if row.get(k) not in (None, "") else None
    return {
        "at": NOW().isoformat(timespec="seconds"),
        "liq_usd": f("liq_usd"), "fdv_usd": f("fdv_usd"), "vol_h24": f("vol_h24"),
        "buys_h24": tx.get("buys"), "sells_h24": tx.get("sells"),
    }


def collect(pages=5):
    st, added = load(), 0
    for page in range(1, pages + 1):
        d = get(f"{BASE}/new_pools?page={page}")
        if not d or "data" not in d:
            break
        for p in d["data"]:
            row = slim(p)
            if row["pool"] in st:
                continue
            st[row["pool"]] = {
                "token": row["base_token"], "name": row["name"], "symbol": row["name"].split(" / ")[0],
                "dex": row["dex"], "created": row["created"], "first": metrics(row), "outcome": None,
            }
            added += 1
        time.sleep(2.2)
    save(st)
    print(f"collect: +{added} pools, total {len(st)}")


def resolve(min_age_h=24, max_calls=60, budget_s=1020):
    """Re-check pools that are at least min_age_h old, 30 pools per API call (GeckoTerminal multi-pool endpoint).
    Oldest first; at most max_calls calls AND budget_s seconds per run (GitHub runner IPs get 429s from the API, so calls can be slow),
    which keeps a run inside the workflow timeout so it always saves and commits.
    The outcome stores age_h: how many hours after launch it was actually taken (>= min_age_h)."""
    st, done = load(), 0
    now = NOW()
    age = lambda rec: (now - datetime.datetime.fromisoformat(rec["created"].replace("Z", "+00:00"))).total_seconds() / 3600
    todo = sorted((a for a, rec in st.items() if not rec["outcome"] and age(rec) >= min_age_h), key=lambda a: st[a]["created"])
    calls, t0 = 0, time.time()
    for i in range(0, len(todo), 30):
        if calls >= max_calls or time.time() - t0 > budget_s:
            break
        batch = todo[i : i + 30]
        d = get(f"{BASE}/pools/multi/{','.join(batch)}")
        calls += 1
        time.sleep(2.2)
        if not d or "data" not in d:
            continue
        for p in d["data"]:
            addr = str(p["attributes"]["address"]).lower()
            rec = st.get(addr) or next((st[a] for a in batch if a.lower() == addr), None)
            if rec is not None and not rec["outcome"]:
                m = metrics(slim(p))
                m["age_h"] = round(age(rec), 1)
                rec["outcome"] = m
                done += 1
    save(st)
    print(f"resolve: {done} outcomes stored from {calls} calls ({len(todo)} waiting); shards changed: {write_shards(st)}; symbol index changed: {write_symbol_index(st)}; odds changed: {write_odds(st)}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "collect"
    if cmd == "collect":
        collect(int(sys.argv[2]) if len(sys.argv) > 2 else 5)
    else:
        resolve()
