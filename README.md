# Actuary open data collector

Every hour this repository records the first sighting of new pools on Robinhood Chain (chain id 4663) and, 24 to 30 hours later, checks each pool again. Actuary's Gate, Odds and Clone Radar read the files it produces.

- `data/state.json`: every pool seen, its first sighting and its 24-hour outcome.
- `data/shards/<xx>.json`: per-token records for pools with an outcome (xx = first two hex characters of the token address).
- `data/symbols.json`: normalised ticker → tokens using it, oldest first, with first-hour liquidity, volume and swaps.
- `data/odds.json`: the actuarial table: share of tokens still trading after 24 hours by first-hour shape.
- `data/births/<xx>.json`: for tokens this collector never saw (born before 14 Oct 2026, or missed between runs), the first pool read from chain logs and its first 21 minutes: swaps counted and whether liquidity was added. Actuary's Gate finds the birth and lists it at `https://actuaryai.pro/api/v1/births/pending`; `feeder/births.py` counts the window from public logs.
- `data/tickers/<xx>.json`: the first token to use each ticker since block 1, from every pool-creation log on the chain (`feeder/tickers.py`). Answers "is there an older token with this ticker?" for any birth we know.

Source of market data: GeckoTerminal's public API. Labels (`labels.py`) describe patterns consistent with the numbers, never accusations. Recorded since 14 Oct 2026; earlier history lives at github.com/coroner-hash/ai.

Raw files: `https://raw.githubusercontent.com/actuaryai/data/main/data/odds.json` (and the others). Used by https://actuaryai.pro.
