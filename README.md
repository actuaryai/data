# Actuary open data collector

Every hour this repository records the first sighting of new pools on Robinhood Chain (chain id 4663) and, 24 to 30 hours later, checks each pool again. Actuary's Gate, Odds and Clone Radar read the files it produces.

- `data/state.json`: every pool seen, its first sighting and its 24-hour outcome.
- `data/shards/<xx>.json`: per-token records for pools with an outcome (xx = first two hex characters of the token address).
- `data/symbols.json`: normalised ticker → tokens using it, oldest first, with first-hour liquidity, volume and swaps.
- `data/odds.json`: the actuarial table: share of tokens still trading after 24 hours by first-hour shape.

Source of market data: GeckoTerminal's public API. Labels (`labels.py`) describe patterns consistent with the numbers, never accusations. Recorded since 14 Oct 2026; earlier history lives at github.com/coroner-hash/ai.

Raw files: `https://raw.githubusercontent.com/actuaryai/data/main/data/odds.json` (and the others). Used by https://actuaryai.pro.
