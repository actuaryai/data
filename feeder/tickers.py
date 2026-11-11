"""Tickers: the first token to use each ticker on Robinhood Chain, read from chain logs since block 1.

The hourly collector only knows pools GeckoTerminal listed while it ran (from 14 Oct 2026), so "is there an older
token with this ticker?" had no answer for anything born earlier. The chain itself does: every pool-creation event
(Uniswap v4 Initialize, v2-style PairCreated, v3-style PoolCreated, from any emitter, and Pons launches) names its tokens, and symbol()
gives each token's ticker. Before 14 Oct the chain saw well over a million tokens, nearly all of them never listed
anywhere, so this keeps one entry per ticker: the token whose first pool came first.

  data/tickers/<xx>.json   { ticker: [token, block, unix_time] }   xx = first two characters of the normalised ticker
  data/tickers/meta.json   how far the scan has read

unix_time is interpolated between block timestamps read every 30,000 blocks (about 17 minutes), so it can be off by
a few minutes; block is exact. Usage: python feeder/tickers.py [max_seconds]   (public RPC, no key; resumes)
"""
import json, os, re, sys, time, datetime, pathlib, urllib.request

RPC = os.environ.get("TICKERS_RPC", "https://rpc.mainnet.chain.robinhood.com")
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "tickers"
META = OUT / "meta.json"
SPAN = 30_000          # the public RPC's getLogs limit
LAG = 2_000            # blocks to stay behind the tip

INIT = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"     # Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)
PAIR = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"     # PairCreated(address,address,address,uint256)
POOL = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"     # PoolCreated(address,address,uint24,int24,address)
PONS = "0x8d4aad4953d0ca700d468f3753aa14432d1b35b43ec6409f051fb6aa43a89607"     # Pons launch: token, pool, creator indexed; quote token in data
ZERO = "0x" + "0" * 40

_id = 0
_last = [0.0]
PACE = float(os.environ.get("TICKERS_PACE", "0.6"))  # seconds between requests; the public RPC answers 429 when pushed
MULTICALL = "0xca11bde05977b3631167028862be2a173976ca11"


def rpc_batch(calls, tries=8):
    """calls: [(method, params)] -> results in order; an item that errors comes back as an Exception."""
    global _id
    body = []
    for m, p in calls:
        _id += 1
        body.append({"jsonrpc": "2.0", "id": _id, "method": m, "params": p})
    for i in range(tries):
        wait = _last[0] + PACE - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(RPC, data=json.dumps(body).encode(), headers={"content-type": "application/json", "user-agent": "actuary-tickers/1"})
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.load(r)
            if isinstance(res, dict):
                raise RuntimeError(res.get("error"))
            by = {x["id"]: x for x in res}
            return [RuntimeError(by[b["id"]]["error"]) if "error" in by[b["id"]] else by[b["id"]]["result"] for b in body]
        except Exception as e:
            print("retry", i, str(e)[:120], file=sys.stderr)
            time.sleep(10 * (i + 1))
    raise RuntimeError("rpc failed")



hx = lambda n: hex(n)
addr = lambda topic: "0x" + topic[-40:].lower()
norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())


def rpc(method, params):
    r = rpc_batch([(method, params)])[0]
    if isinstance(r, Exception):
        raise r
    return r


def block_times(blocks):
    out = {}
    blocks = sorted(set(blocks))
    for i in range(0, len(blocks), 40):
        part = blocks[i : i + 40]
        for b, r in zip(part, rpc_batch([("eth_getBlockByNumber", [hx(b), False]) for b in part])):
            if not isinstance(r, Exception) and r:
                out[b] = int(r["timestamp"], 16)
    return out


def creations(lo, hi):
    """(block, logIndex, token) for every token named in a pool-creation log in [lo, hi]; a refused range is split."""
    ranges = [(a, min(a + SPAN - 1, hi)) for a in range(lo, hi + 1, SPAN)]
    out = []
    while ranges:
        part, ranges = ranges[:10], ranges[10:]
        res = rpc_batch([("eth_getLogs", [{"fromBlock": hx(a), "toBlock": hx(b), "topics": [[INIT, PAIR, POOL, PONS]]}]) for a, b in part])
        for (a, b), r in zip(part, res):
            if isinstance(r, Exception):
                if b - a < 100:
                    raise r
                m = (a + b) // 2
                ranges = [(a, m), (m + 1, b)] + ranges
                continue
            for log in r:
                t = log["topics"]
                if t[0] == INIT and len(t) == 4:
                    toks = (t[2], t[3])
                elif t[0] in (PAIR, POOL) and len(t) >= 3:
                    toks = (t[1], t[2])
                elif t[0] == PONS and len(t) == 4:
                    toks = (t[1], log["data"][2:66] or ZERO)
                else:
                    continue
                for tok in toks:
                    out.append((int(log["blockNumber"], 16), int(log["logIndex"], 16), addr(tok)))
    return out


def decode_symbol(h):
    if not h or h == "0x":
        return ""
    b = bytes.fromhex(h[2:])
    try:
        if len(b) >= 64 and int.from_bytes(b[:32], "big") == 32:
            n = int.from_bytes(b[32:64], "big")
            return b[64 : 64 + n].decode("utf8", "ignore")
        return b[:32].rstrip(b"\x00").decode("utf8", "ignore")  # bytes32 symbols
    except Exception:
        return ""


def _aggregate3(targets, data):
    """ABI-encode Multicall3.aggregate3([(target, allowFailure=true, data)]) for the same calldata on every target."""
    w = lambda n: format(n, "064x")
    cd = data[2:]
    n_bytes = len(cd) // 2
    padded = cd + "0" * ((64 - len(cd) % 64) % 64)
    elems = [w(int(t, 16)) + w(1) + w(96) + w(n_bytes) + padded for t in targets]
    offs, pos = [], 32 * len(elems)
    for e in elems:
        offs.append(w(pos))
        pos += len(e) // 2
    return "0x82ad56cb" + w(32) + w(len(elems)) + "".join(offs) + "".join(elems)


def _results(h, n):
    b = bytes.fromhex(h[2:])
    word = lambda i: int.from_bytes(b[i : i + 32], "big")
    base = word(0)
    count = word(base)
    out = []
    for k in range(min(n, count)):
        at = base + 32 + word(base + 32 + 32 * k)
        ok = word(at) == 1
        dat = at + word(at + 32)
        ln = word(dat)
        out.append("0x" + b[dat + 32 : dat + 32 + ln].hex() if ok else None)
    return out


def symbols(tokens):
    """symbol() for many tokens, 250 per Multicall3 call; a token that reverts gets an empty ticker."""
    out = {}
    for i in range(0, len(tokens), 250):
        part = tokens[i : i + 250]
        try:
            res = _results(rpc("eth_call", [{"to": MULTICALL, "data": _aggregate3(part, "0x95d89b41")}, "latest"]), len(part))
        except Exception as e:
            print("symbols batch failed", str(e)[:100], file=sys.stderr)
            res = [None] * len(part)
        for tok, r in zip(part, res):
            out[tok] = decode_symbol(r) if r else ""
    return out


def load():
    meta = json.loads(META.read_text()) if META.exists() else {"chain_id": 4663, "from_block": 1, "scanned_to": 0}
    first = {}
    for f in OUT.glob("*.json"):
        if f.name != "meta.json":
            first.update(json.loads(f.read_text()))
    return meta, first


def save(meta, first, touched):
    OUT.mkdir(parents=True, exist_ok=True)
    keys = {t[:2] for t in touched}
    shards = {}
    for t, v in first.items():
        if t[:2] in keys:
            shards.setdefault(t[:2], {})[t] = v
    for k, d in shards.items():
        (OUT / f"{k}.json").write_text(json.dumps(d, separators=(",", ":"), sort_keys=True))
    meta["tickers"] = len(first)
    META.write_text(json.dumps(meta, indent=1, sort_keys=True))


def run(max_seconds):
    t0 = time.time()
    meta, first = load()
    latest = int(rpc("eth_blockNumber", []), 16)
    tip = latest - LAG
    lo = meta["scanned_to"] + 1
    step = 300_000
    known = {v[0] for v in first.values()}
    while lo <= tip and time.time() - t0 < max_seconds:
        hi = min(lo + step - 1, tip)
        seen = {}
        for blk, li, tok in creations(lo, hi):
            if tok != ZERO and (tok not in seen or (blk, li) < seen[tok]):
                seen[tok] = (blk, li)
        fresh = [t for t in seen if t not in known]
        syms = symbols(fresh)
        bounds = list(range(lo, hi + 1, SPAN)) + [hi]
        bt = block_times(bounds)
        pts = sorted(bt.items())

        def when(b):
            for (b0, t0_), (b1, t1) in zip(pts, pts[1:]):
                if b0 <= b <= b1:
                    return int(t0_ + (t1 - t0_) * (b - b0) / max(1, b1 - b0))
            return pts[-1][1] if pts else None

        touched = set()
        for tok in fresh:
            tick = norm(syms.get(tok, ""))
            if len(tick) < 2:
                continue
            blk = seen[tok][0]
            cur = first.get(tick)
            if cur is None or blk < cur[1]:
                first[tick] = [tok, blk, when(blk)]
                touched.add(tick)
        meta["scanned_to"] = hi
        meta["scanned_to_time"] = datetime.datetime.fromtimestamp(bt[hi], datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if bt.get(hi) else meta.get("scanned_to_time")
        meta["method"] = "first token per normalised ticker, from Initialize/PairCreated/PoolCreated/Pons launch logs and symbol(); block exact, time interpolated"
        save(meta, first, touched)
        known.update(fresh)
        print(f"{lo}-{hi}: {len(fresh)} tokens read, {len(touched)} tickers set, {len(first)} tickers, {int(time.time() - t0)} s", flush=True)
        lo = hi + 1
    print(f"tickers: scanned to {meta['scanned_to']} ({meta.get('scanned_to_time')}), tip {tip}, {len(first)} tickers")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 900)
