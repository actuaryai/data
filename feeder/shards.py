"""Write small per-prefix shards so a browser can fetch one token's record without downloading everything.

data/shards/<xx>.json  where xx = first two hex chars of the token address (after 0x).
Content: { "<token>": [ {pool, symbol, name, created, dex, first, outcome}, ... ] }
Only shards whose content changed are rewritten, to keep commits small.
"""
import json, pathlib, collections

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
KEEP = ("liq_usd", "fdv_usd", "vol_h24", "buys_h24", "sells_h24", "at", "age_h")


def slim(snap):
    return {k: snap.get(k) for k in KEEP} if snap else None


def write_shards(state):
    shards = collections.defaultdict(lambda: collections.defaultdict(list))
    for pool, rec in state.items():
        tok = rec["token"].lower()
        if not tok.startswith("0x") or len(tok) != 42 or not rec.get("outcome"):
            continue  # only pools with a 24h outcome can have an autopsy
        shards[tok[2:4]][tok].append({
            "pool": pool, "symbol": rec["symbol"], "name": rec["name"], "created": rec["created"], "dex": rec.get("dex"),
            "first": slim(rec["first"]), "outcome": slim(rec.get("outcome")),
        })
    out = DATA / "shards"
    out.mkdir(parents=True, exist_ok=True)
    changed = 0
    for prefix, tokens in shards.items():
        text = json.dumps(tokens, separators=(",", ":"), sort_keys=True)
        path = out / f"{prefix}.json"
        if not path.exists() or path.read_text(encoding="utf8") != text:
            path.write_text(text, encoding="utf8")
            changed += 1
    return changed


def _norm(s):
    import re
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def write_symbol_index(state):
    """data/symbols.json: normalised ticker -> tokens using it, oldest first.
    Each entry: [token, first_seen, dex, first_liq_usd, first_vol_h24, first_swaps]. Lets the Telegram bot spot copy-cat tickers
    and show a token's first sighting without fetching the whole state."""
    by_token = {}
    for pool, rec in state.items():
        tok = (rec.get("token") or "").lower()
        if not tok.startswith("0x") or len(tok) != 42:
            continue
        f = rec.get("first") or {}
        cur = by_token.get(tok)
        if cur is None or rec["created"] < cur[1]:
            by_token[tok] = [tok, rec["created"], rec.get("dex"), f.get("liq_usd"), f.get("vol_h24"), rec.get("symbol") or "",
                            (f.get("buys_h24") or 0) + (f.get("sells_h24") or 0)]
    index = {}
    for tok, e in by_token.items():
        k = _norm(e[5])
        if len(k) < 2:
            continue
        index.setdefault(k, []).append(e[:5] + [e[6]])
    for k in index:
        index[k].sort(key=lambda e: e[1])
    text = json.dumps(index, separators=(",", ":"), sort_keys=True)
    path = DATA / "symbols.json"
    if not path.exists() or path.read_text(encoding="utf8") != text:
        path.write_text(text, encoding="utf8")
        return True
    return False
