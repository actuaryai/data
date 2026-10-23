"""Snapshot Robinhood Chain pools from GeckoTerminal into JSONL (one line per pool).

Usage: python feeder/snapshot_pools.py [pages] [kind]
  kind = new_pools (default) | trending_pools
Public API, no key. Be polite: ~2s between calls (30 req/min limit).
"""
import json, sys, time, datetime, urllib.request, pathlib

BASE = "https://api.geckoterminal.com/api/v2/networks/robinhood"
OUT = pathlib.Path(__file__).resolve().parent.parent / "data"


def get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "coroner-feeder/0.1"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # 429 etc.
            print("retry", i, e, file=sys.stderr)
            time.sleep(5 * (i + 1))
    return None


def slim(p):
    a = p["attributes"]
    rel = p.get("relationships", {})
    return {
        "pool": a["address"],
        "name": a["name"],
        "created": a["pool_created_at"],
        "base_token": rel.get("base_token", {}).get("data", {}).get("id", "").replace("robinhood_", ""),
        "quote_token": rel.get("quote_token", {}).get("data", {}).get("id", "").replace("robinhood_", ""),
        "dex": rel.get("dex", {}).get("data", {}).get("id"),
        "fdv_usd": a.get("fdv_usd"),
        "liq_usd": a.get("reserve_in_usd"),
        "vol_h24": a["volume_usd"]["h24"],
        "tx_h24": a.get("transactions", {}).get("h24"),
        "chg_h24": a.get("price_change_percentage", {}).get("h24"),
    }


def main():
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    kind = sys.argv[2] if len(sys.argv) > 2 else "new_pools"
    now = datetime.datetime.now(datetime.timezone.utc)
    OUT.mkdir(exist_ok=True)
    path = OUT / f"{kind}_{now:%Y%m%dT%H%M}Z.jsonl"
    n = 0
    with open(path, "w", encoding="utf8") as f:
        for page in range(1, pages + 1):
            d = get(f"{BASE}/{kind}?page={page}")
            if not d or "data" not in d:
                break
            for p in d["data"]:
                row = slim(p)
                row["snapshot_at"] = now.isoformat()
                f.write(json.dumps(row) + "\n")
                n += 1
            time.sleep(2.2)
    print(f"wrote {n} rows -> {path}")


if __name__ == "__main__":
    main()
