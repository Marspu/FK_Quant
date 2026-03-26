# LOF T+0 Grid Strategy For XtQuant

This workspace contains a ready-to-run A-share LOF T+0 grid strategy built on top of MiniQMT / XtQuant.

Files:

- `lof_t0_grid_xtquant.py`: main strategy script
- `lof_t0_grid_replay.py`: offline csv replay / factor screening tool
- `config.example.json`: example configuration
- `signal_hook_example.py`: example custom signal hook

What the strategy does:

1. Connects to `XtQuantTrader`, subscribes the account, and syncs cash / positions.
2. Uses `xtdata.get_full_tick()` and optional `subscribe_whole_quote()` to read the latest LOF quotes.
3. Anchors the grid around `last_close` or a custom anchor price.
4. Computes a target intraday inventory around the base position:
   - price below anchor -> buy grid units
   - price above anchor -> sell grid units
5. Sends orders through `order_stock()` using:
   - `xtconstant.STOCK_BUY`
   - `xtconstant.STOCK_SELL`
   - `xtconstant.FIX_PRICE`
6. Cancels stale pending orders with `cancel_order_stock()`.
7. After `close_back_to_base_time`, gradually restores the intraday position back to the base position.

## Important Notes

- Use only symbols that you have independently confirmed are eligible for T+0 secondary-market trading.
- `base_position = null` means "use the starting live position as the T+0 base inventory".
- If you start from `base_position = 0`, the strategy becomes long-only unless same-day bought shares are sellable on that product.
- The example config sets `dry_run = true`. Keep it that way for the first connection test.

## Quick Start

1. Make sure MiniQMT is running and the target account is logged in.
2. Copy `config.example.json` to a local config file and edit:
   - `account.miniqmt_dir`
   - `account.account_id`
   - the LOF `symbol`
   - your risk parameters
3. Run:

```bash
python3 /Users/zhiwei.bu/Documents/量化/lof_t0_grid_xtquant.py --config /Users/zhiwei.bu/Documents/量化/config.example.json
```

## Strategy Parameters

- `grid_pct`: base grid spacing
- `order_volume`: shares per grid action
- `max_intraday_long_volume`: max extra long inventory above the base position
- `max_intraday_short_volume`: max inventory reduction below the base position
- `max_order_volume`: cap per order
- `max_position_value`: hard cap on symbol market value
- `max_daily_buy_amount`: daily buy notional cap
- `stop_new_order_time`: stop opening new grid legs after this time
- `close_back_to_base_time`: only reduce back to the base position after this time

## AI / Signal Enhancement

The script already includes a built-in adaptive signal layer using recent quote momentum, range position, spread, top-of-book imbalance, and a blended `101 Formulaic Alphas` style price-volume bundle.

If you want a custom AI signal, provide:

- `ai.signal_hook_file`
- `ai.signal_hook_func`

The hook will receive:

```python
{
    "symbol": "161129.SZ",
    "features": {
        "ret_5": ...,
        "ret_20": ...,
        "ret_60": ...,
        "volatility": ...,
        "range_pos": ...,
        "spread_pct": ...,
        "imbalance": ...
    },
    "state": {
        "base_position": ...,
        "current_position": ...,
        "can_use_volume": ...,
        "anchor_price": ...,
        "last_price": ...
    }
}
```

Return either:

- a float in `[-1, 1]`, or
- a dict like `{"score": 0.35, "grid_multiplier": 1.2}`

Additional built-in `ai` parameters:

- `formulaic_alpha_enabled`: enable / disable the Alpha101 bundle
- `formulaic_alpha_blend`: blend ratio between heuristic score and Alpha101 composite
- `formulaic_alpha_bar_seconds`: tick-to-bar aggregation interval for Alpha101
- `formulaic_alpha_lookback`: trailing lookback window used by time-series ranked alphas
- `formulaic_alpha_min_bars`: minimum bars before Alpha101 starts producing signals
- `formulaic_alpha_weights`: per-alpha weight map for the composite signal

## Offline Replay

Use the replay script to screen factors and inspect how the `Alpha101` bundle interacts with the grid logic before live deployment.

Expected csv columns:

- required: `timestamp`, `last_price`
- recommended: `bid1`, `ask1`, `bid_vol1`, `ask_vol1`, `last_close`, `total_volume`, `total_amount`
- optional: `symbol`

Example:

```bash
python3 /Users/zhiwei.bu/Documents/量化/lof_t0_grid_replay.py \
  --csv /path/to/161129_ticks.csv \
  --config /Users/zhiwei.bu/Documents/量化/config.example.json \
  --symbol 161129.SZ \
  --initial-cash 200000 \
  --base-position 2000 \
  --forward-seconds 300 \
  --signal-csv /tmp/lof_signals.csv \
  --report-json /tmp/lof_report.json
```

Replay output includes:

- trade count, turnover, ending position, PnL
- per-signal snapshots with `alpha101_composite` and individual alpha values
- forward-return Pearson IC ranking for `score`, `alpha101_composite`, and each `alpha101_*` feature

## Verification

The current workspace does not have `xtquant` installed, so live connectivity cannot be validated locally here. Syntax validation and offline replay can be run locally; execute the main strategy in your MiniQMT Python environment for live verification.
