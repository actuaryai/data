"""data/odds.json: the actuarial table. How many tokens were still trading 24 hours after birth, by what they looked like
in their first hour (our first sighting is a median 21 minutes after the pool is created).

Only pools re-checked 24 to 30 hours after creation count, so every token is measured on the same clock.
"Still trading" is labels.py "alive": at least $50 of volume or 3 swaps in the 24 hours before the re-check.
Dead tokens keep their cause pattern (never_lived, bled_out, lp_removed; "clone" counts as never_lived here).
Cohorts are DEX group x first liquidity band x first-hour swap band; the page falls back to wider cohorts when one is thin.
"""
import json, pathlib, sys, re, datetime, collections

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import labels  # noqa: E402

DATA = ROOT / "data"
LIQ = [100, 5_000, 20_000, 100_000]       # bands: <100, 100-5K, 5K-20K, 20K-100K, 100K+
SWAPS = [5, 20, 100, 500]                 # bands: <5, 5-19, 20-99, 100-499, 500+
VOL = [100, 1_000, 10_000, 100_000]
DEX = {"pons-v2": "pons", "uniswap-v4-robinhood": "v4", "uniswap-v3-robinhood": "v3", "uniswap-v2-robinhood": "v2"}
CAUSE = {"never_lived": 0, "clone": 0, "bled_out": 1, "lp_removed": 2}


def band(v, edges):
    return sum(v >= e for e in edges)


def write_odds(state):
    first_of = {}
    for pool, rec in state.items():
        tok = (rec.get("token") or "").lower()
        if len(tok) == 42 and tok.startswith("0x") and not tok.startswith("0x00000000"):
            if tok not in first_of or rec["created"] < first_of[tok]["created"]:
                first_of[tok] = rec
    sym_first = {}
    for tok, rec in first_of.items():
        s = labels.norm_symbol(rec.get("symbol"))
        if s and (s not in sym_first or rec["created"] < sym_first[s][0]):
            sym_first[s] = (rec["created"], tok)

    cohorts = collections.defaultdict(lambda: [0, 0, 0, 0, 0])  # n, alive, never_lived, bled_out, lp_removed
    factors = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    total = [0, 0, 0, 0, 0]
    days = []
    for tok, rec in first_of.items():
        o, f = rec.get("outcome"), rec.get("first") or {}
        if not o or not (24 <= (o.get("age_h") or 0) < 30):
            continue
        lab = labels.label(rec)
        alive = lab["status"] == "alive"
        swaps = (f.get("buys_h24") or 0) + (f.get("sells_h24") or 0)
        dex = DEX.get(rec.get("dex"), "other")
        lb, sb = band(f.get("liq_usd") or 0, LIQ), band(swaps, SWAPS)
        s = labels.norm_symbol(rec.get("symbol"))
        orig = not s or sym_first.get(s, (None, tok))[1] == tok
        hour = int(rec["created"][11:13]) // 4
        days.append(rec["created"][:10])
        row = [1, int(alive), 0, 0, 0]
        if not alive:
            row[2 + CAUSE.get(lab["cause_pattern"], 1)] = 1
        for key in (f"{dex}|{lb}|{sb}", f"*|{lb}|{sb}", f"{dex}|*|{sb}", f"*|*|{sb}", f"{dex}|*|*"):
            c = cohorts[key]
            for i in range(5):
                c[i] += row[i]
        for i in range(5):
            total[i] += row[i]
        for name, k in (("swaps", sb), ("liq", lb), ("vol", band(f.get("vol_h24") or 0, VOL)), ("dex", dex),
                        ("hour", hour), ("ticker", "original" if orig else "copy")):
            factors[name][k][0] += 1
            factors[name][k][1] += int(alive)

    out = {
        "asof": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "born": [min(days), max(days)] if days else None,
        "rule": "still trading = at least $50 volume or 3 swaps in the 24 h before a re-check made 24 to 30 h after birth",
        "bands": {"liq": LIQ, "swaps": SWAPS, "vol": VOL},
        "total": total,
        "factors": {k: {str(b): v for b, v in sorted(d.items(), key=lambda x: str(x[0]))} for k, d in factors.items()},
        "cohorts": dict(cohorts),
    }
    text = json.dumps(out, separators=(",", ":"), sort_keys=True)
    path = DATA / "odds.json"
    old = path.read_text(encoding="utf8") if path.exists() else ""
    if re.sub(r'"asof":"[^"]*"', "", old) == re.sub(r'"asof":"[^"]*"', "", text):
        return False
    path.write_text(text, encoding="utf8")
    return True


if __name__ == "__main__":
    st = json.load(open(DATA / "state.json", encoding="utf8"))
    print("odds changed:", write_odds(st))
    d = json.load(open(DATA / "odds.json", encoding="utf8"))
    n, a = d["total"][:2]
    print(f"born {d['born']}: {a}/{n} still trading = {100 * a / n:.1f}%; cohorts {len(d['cohorts'])}")
    for k, v in d["factors"].items():
        print(k, {b: f"{x[1]}/{x[0]} {100 * x[1] / x[0]:.0f}%" for b, x in v.items()})
