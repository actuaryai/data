"""Births: first-window counts for tokens the market-API collector never saw.

Actuary's Gate finds such a token's birth itself (deploy block, first pool) and lists it at
https://actuaryai.pro/api/v1/births/pending. A Worker cannot read wide log ranges, so this script does the rest from
public chain logs and publishes it here:

  - token with a known first pool: count swaps, and see whether liquidity was added, in the pool's first WINDOW_MIN
    minutes (WINDOW_MIN matches the collector's median first-sighting delay, so the numbers compare).
  - token with no pool in the 60 blocks after deploy: look further, up to SEARCH_DAYS after deploy.

  data/births/<xx>.json      { token: {"block", "born_at", "dex", "pool", "swaps", "liquidity_added"} }   xx = token[2:4]
  data/births/_search.json   { token: last block searched } for tokens with no pool found yet

Usage: python feeder/births.py [max_seconds]   (public RPC, no key)
"""
import json, os, sys, time, pathlib, urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from tickers import rpc_batch, rpc, hx, addr  # noqa: E402  (same paced JSON-RPC client)

PENDING = os.environ.get("BIRTHS_PENDING", "https://actuaryai.pro/api/v1/births/pending")
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "births"
SEARCH = OUT / "_search.json"
SPAN = 30_000
WINDOW_MIN = 21
SEARCH_DAYS = 7

INIT = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"
PAIR = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
POOL = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
PONS = "0x8d4aad4953d0ca700d468f3753aa14432d1b35b43ec6409f051fb6aa43a89607"
V4SWAP = "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"
V4LIQ = "0xf208f4912782fd25c7f114ca3723a2d5dd6f3bcc3ac8db5af63baa85f711d5ec"
V2SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
V3SWAP = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
V2MINT = "0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f"
V3MINT = "0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde"
PONS_SWAP = "0xec36bf571f136799e8dc0b0b8bea4b04d8bd3d43de838aab0d5fc21d4cbfc455"

DEX = {
    "0x8366a39cc670b4001a1121b8f6a443a643e40951": "uniswap-v4-robinhood",
    "0x8bceaa40b9acdfaedf85adf4ff01f5ad6517937f": "uniswap-v2-robinhood",
    "0x1f7d7550b1b028f7571e69a784071f0205fd2efa": "uniswap-v3-robinhood",
    "0x7ed598bcef8bd9edd8c97a195c6d13f40801ec7e": "pons-v2",
}


def topic(a):
    return "0x" + "0" * 24 + a[2:].lower()


def parse(log, token):
    t = [x.lower() for x in log["topics"]]
    d = log["data"][2:]
    em = log["address"].lower()
    tok = topic(token)
    base = {"block": int(log["blockNumber"], 16), "index": int(log["logIndex"], 16), "emitter": em}
    if t[0] == INIT and len(t) == 4 and tok in (t[2], t[3]):
        return {**base, "pool": t[1], "dex": DEX.get(em, "v4:" + em), "kind": "v4"}
    if t[0] == PAIR and len(t) == 3 and tok in (t[1], t[2]) and len(d) >= 64:
        return {**base, "pool": "0x" + d[24:64], "dex": DEX.get(em, "v2:" + em), "kind": "v2"}
    if t[0] == POOL and len(t) == 4 and tok in (t[1], t[2]) and len(d) >= 128:
        return {**base, "pool": "0x" + d[88:128], "dex": DEX.get(em, "v3:" + em), "kind": "v3"}
    if t[0] == PONS and len(t) == 4 and t[1] == tok:
        return {**base, "pool": "0x" + t[2][26:], "dex": DEX.get(em, "pons:" + em), "kind": "pons"}
    return None


def first_pool(token, lo, hi):
    tok = topic(token)
    for a in range(lo, hi + 1, SPAN * 5):
        ranges = [(x, min(x + SPAN - 1, hi)) for x in range(a, min(a + SPAN * 5, hi + 1), SPAN)]
        calls = []
        for x, y in ranges:
            r = {"fromBlock": hx(x), "toBlock": hx(y)}
            calls += [("eth_getLogs", [{**r, "topics": [[PAIR, POOL, PONS], tok]}]), ("eth_getLogs", [{**r, "topics": [[PAIR, POOL], None, tok]}]),
                      ("eth_getLogs", [{**r, "topics": [INIT, None, tok]}]), ("eth_getLogs", [{**r, "topics": [INIT, None, None, tok]}])]
        res = rpc_batch(calls)
        found = sorted((p for r in res if not isinstance(r, Exception) for p in (parse(l, token) for l in r) if p), key=lambda p: (p["block"], p["index"]))
        if any(isinstance(r, Exception) for r in res):
            return None, a - 1
        if found:
            return found[0], found[0]["block"]
    return None, hi


def kind_of(dex):
    return "pons" if dex.startswith("pons") else "v4" if "v4" in dex else "v3" if "v3" in dex else "v2"


def emitter_of(dex):
    return next((k for k, v in DEX.items() if v == dex), dex.split(":")[1] if ":" in dex else "")


def window(rec, latest):
    """swaps and liquidity adds in the first WINDOW_MIN minutes; None while the window is still open."""
    b = rec["block"]
    probe = min(b + 20_000, latest)
    ts = rpc_batch([("eth_getBlockByNumber", [hx(b), False]), ("eth_getBlockByNumber", [hx(probe), False])])
    if any(isinstance(x, Exception) or not x for x in ts):
        return None
    t0, t1 = int(ts[0]["timestamp"], 16), int(ts[1]["timestamp"], 16)
    rate = (probe - b) / (t1 - t0) if probe > b and t1 > t0 else 30
    end = b + int(rate * WINDOW_MIN * 60) + 1
    if end > latest:
        return None
    kind, pool, em = rec["kind"], rec["pool"], rec["emitter"]
    calls = []
    for a in range(b, end + 1, SPAN):
        r = {"fromBlock": hx(a), "toBlock": hx(min(a + SPAN - 1, end))}
        if kind == "v4":
            calls.append(("eth_getLogs", [{**r, "address": em, "topics": [[V4SWAP, V4LIQ], pool]}]))
        elif kind == "pons":
            calls.append(("eth_getLogs", [{**r, "address": pool, "topics": [[PONS_SWAP]]}]))
        else:
            calls.append(("eth_getLogs", [{**r, "address": pool, "topics": [[V2SWAP, V3SWAP, V2MINT, V3MINT]]}]))
    res = rpc_batch(calls)
    if any(isinstance(r, Exception) for r in res):
        return None
    swaps, liq = 0, kind == "pons"  # a Pons launch seeds its pool with the supply in the launch transaction
    for l in (l for r in res for l in r):
        t = l["topics"][0].lower()
        if t in (V2SWAP, V3SWAP, V4SWAP, PONS_SWAP):
            swaps += 1
        elif t in (V2MINT, V3MINT):
            liq = True
        elif t == V4LIQ:
            w = l["data"][2:][128:192]  # liquidityDelta
            if len(w) == 64 and int(w, 16) != 0 and int(w[0], 16) < 8:
                liq = True
    return {"born_at": t0, "swaps": swaps, "liquidity_added": liq}


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def run(max_seconds):
    t0 = time.time()
    req = urllib.request.Request(PENDING, headers={"user-agent": "actuary-births/1", "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        pending = json.load(r)["pending"]
    latest = int(rpc("eth_blockNumber", []), 16) - 50
    OUT.mkdir(parents=True, exist_ok=True)
    search = load(SEARCH, {})
    shards, done, searched = {}, 0, 0
    for p in pending:
        if time.time() - t0 > max_seconds:
            break
        tok = p["token"].lower()
        k = tok[2:4]
        shard = shards.setdefault(k, load(OUT / f"{k}.json", {}))
        if tok in shard:
            continue
        if p.get("block"):
            rec = {"block": p["block"], "pool": p["pool"], "dex": p["dex"], "kind": p.get("kind") or kind_of(p["dex"]), "emitter": p.get("emitter") or emitter_of(p["dex"])}
        else:
            if not p.get("deploy_block"):
                continue
            lo = max(search.get(tok, 0) + 1, (p.get("scanned_to") or p["deploy_block"]) + 1)
            per_day = 86400 * 30  # generous block rate; the search stops at the tip anyway
            hi = min(p["deploy_block"] + SEARCH_DAYS * per_day, latest)
            if lo > hi:
                continue
            rec, upto = first_pool(tok, lo, min(hi, lo + SPAN * 60))
            searched += 1
            if not rec:
                search[tok] = upto
                continue
            search.pop(tok, None)
        w = window(rec, latest)
        if not w:
            continue
        shard[tok] = {"block": rec["block"], "born_at": w["born_at"], "dex": rec["dex"], "pool": rec["pool"], "swaps": w["swaps"], "liquidity_added": w["liquidity_added"]}
        done += 1
    for k, d in shards.items():
        (OUT / f"{k}.json").write_text(json.dumps(d, separators=(",", ":"), sort_keys=True))
    SEARCH.write_text(json.dumps(search, separators=(",", ":"), sort_keys=True))
    print(f"births: {len(pending)} pending, {done} windows published, {searched} pool searches, {int(time.time() - t0)} s")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 600)
