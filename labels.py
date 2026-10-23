"""Rule-based (weak) labels for Coroner training data. v0.

Labels describe a PATTERN CONSISTENT WITH the observed numbers. They are not accusations.
Input : state record from feeder/collect.py (first sighting + outcome >= 24h later) and optionally facts.
Output: {"status": alive|dead|unknown, "cause_pattern": ..., "evidence": {...}}
"""
import re

DEAD_VOL_USD = 50.0      # 24h volume below this ...
DEAD_TX = 2              # ... and at most this many swaps in 24h => dead
LP_GONE_RATIO = 0.10     # liquidity now < 10% of first sighting => liquidity withdrawn
PRICE_COLLAPSE = 0.10    # fdv now < 10% of first sighting => bled out
MIN_REAL_LIQ = 500.0     # ignore pools that never had meaningful liquidity
NEVER_LIVED_VOL = 500.0  # first-sighting 24h volume under this => never took off


def norm_symbol(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def find_clone(rec, state):
    """Earliest older pool with the same normalised symbol that had more volume."""
    me = norm_symbol(rec["symbol"])
    if len(me) < 3:
        return None
    best = None
    for addr, other in state.items():
        if other is rec or other["token"] == rec["token"]:
            continue
        if norm_symbol(other["symbol"]) == me and other["created"] < rec["created"]:
            if (other["first"].get("vol_h24") or 0) > (rec["first"].get("vol_h24") or 0):
                if best is None or other["created"] < best[1]:
                    best = (other["token"], other["created"])
    return best[0] if best else None


def label(rec, state=None, facts=None):
    o, f = rec.get("outcome"), rec["first"]
    ev = {"first.liq_usd": f.get("liq_usd"), "first.fdv_usd": f.get("fdv_usd"), "first.vol_h24": f.get("vol_h24")}
    clone_of = find_clone(rec, state) if state else None
    if clone_of:
        ev["clone_of"] = clone_of
    if not o:
        return {"status": "unknown", "cause_pattern": None, "clone_of": clone_of, "evidence": ev}
    ev.update({"now.liq_usd": o.get("liq_usd"), "now.fdv_usd": o.get("fdv_usd"), "now.vol_h24": o.get("vol_h24"),
               "now.swaps_h24": (o.get("buys_h24") or 0) + (o.get("sells_h24") or 0)})
    dead = (o.get("vol_h24") or 0) < DEAD_VOL_USD and ev["now.swaps_h24"] <= DEAD_TX
    if not dead:
        return {"status": "alive", "cause_pattern": "none", "clone_of": clone_of, "evidence": ev}

    liq_f, liq_n = f.get("liq_usd") or 0, o.get("liq_usd") or 0
    fdv_f, fdv_n = f.get("fdv_usd") or 0, o.get("fdv_usd") or 0
    if liq_f >= MIN_REAL_LIQ and liq_n < LP_GONE_RATIO * liq_f:
        cause = "lp_removed"
    elif fdv_f > 0 and fdv_n < PRICE_COLLAPSE * fdv_f:
        cause = "bled_out"
    elif (f.get("vol_h24") or 0) < NEVER_LIVED_VOL:
        cause = "never_lived"
    else:
        cause = "bled_out"
    if clone_of and cause == "never_lived":
        cause = "clone"
    if facts and facts.get("contract.selector_mint") and facts.get("contract.owner_renounced") is False:
        ev["contract.mint_with_live_owner"] = True
    return {"status": "dead", "cause_pattern": cause, "clone_of": clone_of, "evidence": ev}


if __name__ == "__main__":  # self-test with synthetic records
    now = "2026-10-14T00:00:00Z"
    mk = lambda **k: {"token": k.get("tok", "0xa"), "symbol": k.get("sym", "AAA"), "created": k.get("created", now),
                      "first": {"liq_usd": k["liq"], "fdv_usd": k["fdv"], "vol_h24": k["vol"]},
                      "outcome": {"liq_usd": k["liq_n"], "fdv_usd": k["fdv_n"], "vol_h24": k["vol_n"], "buys_h24": 0, "sells_h24": k.get("tx", 0)}}
    t = {
        "lp": (mk(liq=8000, fdv=90000, vol=4000, liq_n=10, fdv_n=500, vol_n=0), "lp_removed"),
        "bleed": (mk(liq=8000, fdv=90000, vol=4000, liq_n=7000, fdv_n=2000, vol_n=1), "bled_out"),
        "never": (mk(liq=3000, fdv=20000, vol=100, liq_n=3000, fdv_n=19000, vol_n=0), "never_lived"),
        "alive": (mk(liq=3000, fdv=20000, vol=100, liq_n=3000, fdv_n=19000, vol_n=900, tx=40), "none"),
    }
    for k, (rec, want) in t.items():
        got = label(rec)["cause_pattern"]
        assert got == want, (k, got, want)
    s = {"1": {"token": "0xold", "symbol": "$Cash-Cat", "created": "2026-07-01T00:00:00Z", "first": {"vol_h24": 9e5}},
         "2": mk(tok="0xnew", sym="CASHCAT", liq=3000, fdv=20000, vol=100, liq_n=3000, fdv_n=19000, vol_n=0)}
    assert label(s["2"], s)["cause_pattern"] == "clone" and label(s["2"], s)["clone_of"] == "0xold"
    print("labels self-test OK")
