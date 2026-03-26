#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
import csv
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import lof_t0_grid_xtquant as live


TIME_COLUMNS = ("ts", "timestamp", "time", "datetime", "date_time")
LAST_PRICE_COLUMNS = ("last_price", "lastPrice", "price", "close")
BID1_COLUMNS = ("bid1", "bid_price1", "bidPrice1", "bidPrice")
ASK1_COLUMNS = ("ask1", "ask_price1", "askPrice1", "askPrice")
BID_VOL1_COLUMNS = ("bid_vol1", "bidVol1", "bidVolume1", "bidVol")
ASK_VOL1_COLUMNS = ("ask_vol1", "askVol1", "askVolume1", "askVol")
LAST_CLOSE_COLUMNS = ("last_close", "lastClose", "pre_close", "prev_close")
TOTAL_VOLUME_COLUMNS = ("total_volume", "totalVolume", "volume", "tradeVol")
TOTAL_AMOUNT_COLUMNS = ("total_amount", "totalAmount", "amount", "turnover", "tradeAmount")
SYMBOL_COLUMNS = ("symbol", "stock_code", "stockCode", "code")


@dataclass
class ReplayTrade:
    ts: float
    side: str
    price: float
    volume: int
    reason: str
    score: float
    position: int
    cash: float


@dataclass
class SignalSnapshot:
    ts: float
    price: float
    score: float
    grid_pct: float
    position: int
    features: Dict[str, float]


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _pick(row: Dict[str, str], names: Sequence[str], default: str = "") -> str:
    for name in names:
        if name in row and row[name] not in ("", None):
            return str(row[name])
    return default


def _parse_clock(value: str) -> dt_time:
    parts = value.split(":")
    if len(parts) == 2:
        parts.append("0")
    if len(parts) != 3:
        raise ValueError(f"invalid clock value: {value}")
    return dt_time(hour=int(parts[0]), minute=int(parts[1]), second=int(parts[2]))


def _parse_timestamp(value: str) -> Optional[float]:
    text = str(value).strip()
    if not text:
        return None
    try:
        raw = float(text)
    except ValueError:
        pass
    else:
        if raw > 10**12:
            return raw / 1000.0
        if raw > 10**9:
            return raw
        return None

    normalized = text.replace("T", " ")
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y%m%d %H:%M:%S",
        "%Y%m%d%H%M%S",
    ):
        try:
            return datetime.strptime(normalized, fmt).timestamp()
        except ValueError:
            continue
    return None


def _pearson(values_x: Sequence[float], values_y: Sequence[float]) -> float:
    if len(values_x) != len(values_y) or len(values_x) < 2:
        return 0.0
    mean_x = sum(values_x) / len(values_x)
    mean_y = sum(values_y) / len(values_y)
    var_x = sum((item - mean_x) ** 2 for item in values_x)
    var_y = sum((item - mean_y) ** 2 for item in values_y)
    if var_x <= 0 or var_y <= 0:
        return 0.0
    cov = sum((values_x[idx] - mean_x) * (values_y[idx] - mean_y) for idx in range(len(values_x)))
    return cov / math.sqrt(var_x * var_y)


def _in_window(now: datetime, start_clock: Optional[dt_time], end_clock: Optional[dt_time]) -> bool:
    current = now.time()
    if start_clock is not None and current < start_clock:
        return False
    if end_clock is not None and current > end_clock:
        return False
    return True


def _build_offline_config(
    config_path: Optional[Path],
    symbol: str,
    initial_cash: float,
    base_position: Optional[int],
) -> live.StrategyConfig:
    if config_path is not None:
        payload = live._load_json(config_path)
        if payload.get("symbols"):
            payload["symbols"][0]["symbol"] = symbol
            if base_position is not None:
                payload["symbols"][0]["base_position"] = base_position
        config = live.StrategyConfig.from_dict(payload)
        if config.symbols:
            return config

    normalized_symbol = live._normalize_symbol(symbol)
    payload: Dict[str, Any] = {
        "account": {
            "miniqmt_dir": "",
            "account_id": "offline",
            "account_type": "STOCK",
            "session_id": None,
        },
        "runtime": {
            "poll_interval_s": 1.0,
            "sync_interval_s": 3.0,
            "signal_refresh_s": 5.0,
            "order_timeout_s": 12.0,
            "pending_clear_grace_s": 3.0,
            "stop_new_order_time": "14:55:00",
            "close_back_to_base_time": "14:57:00",
            "log_level": "INFO",
            "dry_run": True,
            "strategy_name": "lof_t0_grid_offline",
            "remark_prefix": "offline",
            "use_whole_quote_subscribe": False,
        },
        "ai": {
            "signal_hook_file": "",
            "signal_hook_func": "predict_signal",
        },
        "symbols": [
            {
                "symbol": normalized_symbol,
                "enabled": True,
                "base_position": base_position,
                "anchor_mode": "last_close",
                "anchor_price": None,
                "order_volume": 100,
                "lot_size": 100,
                "grid_pct": 0.006,
                "min_grid_pct": 0.004,
                "max_grid_pct": 0.015,
                "max_intraday_long_volume": 3000,
                "max_intraday_short_volume": 3000,
                "max_order_volume": 300,
                "slippage_ticks": 1,
                "price_tick": 0.001,
                "max_spread_pct": 0.003,
                "cash_reserve": min(10000.0, max(0.0, initial_cash * 0.1)),
                "max_position_value": 150000.0,
                "max_daily_buy_amount": 100000.0,
                "max_orders_per_day": 60,
                "cooldown_s": 3.0,
                "use_adaptive_signal": True,
                "signal_bias_steps": 2,
                "stop_loss_pct": 0.03,
                "close_back_to_base": True,
            }
        ],
    }
    return live.StrategyConfig.from_dict(payload)


def _row_to_quote(row: Dict[str, str], fallback_symbol: str) -> Optional[Tuple[str, live.QuotePoint]]:
    raw_ts = _pick(row, TIME_COLUMNS)
    ts = _parse_timestamp(raw_ts)
    if ts is None:
        return None
    last_price = _to_float(_pick(row, LAST_PRICE_COLUMNS), 0.0)
    if last_price <= 0:
        return None
    symbol = _pick(row, SYMBOL_COLUMNS, fallback_symbol).strip() or fallback_symbol
    try:
        symbol = live._normalize_symbol(symbol)
    except ValueError:
        symbol = fallback_symbol
    bid1 = _to_float(_pick(row, BID1_COLUMNS), last_price)
    ask1 = _to_float(_pick(row, ASK1_COLUMNS), last_price)
    return symbol, live.QuotePoint(
        ts=ts,
        last_price=last_price,
        bid1=bid1,
        ask1=ask1,
        bid_vol1=_to_float(_pick(row, BID_VOL1_COLUMNS), 0.0),
        ask_vol1=_to_float(_pick(row, ASK_VOL1_COLUMNS), 0.0),
        last_close=_to_float(_pick(row, LAST_CLOSE_COLUMNS), last_price),
        total_volume=_to_float(_pick(row, TOTAL_VOLUME_COLUMNS), 0.0),
        total_amount=_to_float(_pick(row, TOTAL_AMOUNT_COLUMNS), 0.0),
    )


def _reset_trade_day(strategy: live.LofT0GridStrategy, state: live.SymbolState) -> None:
    state.daily_buy_amount = 0.0
    state.daily_sell_amount = 0.0
    state.daily_order_count = 0
    state.pending_order = None
    state.quote_history.clear()
    state.latest_quote = None
    state.last_signal_score = 0.0
    state.last_signal_ts = 0.0
    state.last_order_ts = 0.0
    state.dynamic_grid_pct = state.config.grid_pct
    if state.config.base_position is None:
        state.base_position = live._align_volume(state.current_position, state.config.lot_size)
    else:
        state.base_position = live._align_volume(state.config.base_position, state.config.lot_size)
    state.anchor_price = float(state.config.anchor_price) if state.config.anchor_price is not None else 0.0


def _fill_order(
    strategy: live.LofT0GridStrategy,
    state: live.SymbolState,
    side: str,
    volume: int,
    reason: str,
    trades: List[ReplayTrade],
) -> None:
    quote = state.latest_quote
    if quote is None or volume <= 0:
        return
    price = strategy._calc_order_price(state, quote, side)
    notional = price * volume
    if side == "BUY":
        strategy.available_cash -= notional
        state.current_position += volume
        state.can_use_volume += volume
        state.daily_buy_amount += notional
    else:
        strategy.available_cash += notional
        state.current_position -= volume
        state.can_use_volume = max(0, state.can_use_volume - volume)
        state.daily_sell_amount += notional
    state.last_order_ts = quote.ts
    state.daily_order_count += 1
    trades.append(
        ReplayTrade(
            ts=quote.ts,
            side=side,
            price=price,
            volume=volume,
            reason=reason,
            score=state.last_signal_score,
            position=state.current_position,
            cash=strategy.available_cash,
        )
    )


def _process_quote(
    strategy: live.LofT0GridStrategy,
    state: live.SymbolState,
    quote: live.QuotePoint,
    signal_snapshots: List[SignalSnapshot],
    trades: List[ReplayTrade],
    session_start: Optional[dt_time],
    session_end: Optional[dt_time],
) -> None:
    now = datetime.fromtimestamp(quote.ts)
    if not _in_window(now, session_start, session_end):
        return

    state.latest_quote = quote
    state.quote_history.append(quote)
    if state.anchor_price <= 0:
        state.anchor_price = strategy._choose_anchor_price(state, quote)

    score = state.last_signal_score
    grid_pct = state.dynamic_grid_pct or state.config.grid_pct
    if state.config.use_adaptive_signal:
        if quote.ts - state.last_signal_ts >= strategy.config.runtime.signal_refresh_s or state.last_signal_ts <= 0:
            score, grid_pct, features = strategy.signal_engine.compute(state)
            state.last_signal_score = score
            state.dynamic_grid_pct = grid_pct
            state.last_signal_ts = quote.ts
            signal_snapshots.append(
                SignalSnapshot(
                    ts=quote.ts,
                    price=quote.last_price,
                    score=score,
                    grid_pct=grid_pct,
                    position=state.current_position,
                    features=dict(features),
                )
            )
    else:
        state.last_signal_score = 0.0
        state.dynamic_grid_pct = state.config.grid_pct

    if quote.ts - state.last_order_ts < state.config.cooldown_s:
        return

    if now.time() >= strategy.config.runtime.close_back_to_base_clock():
        diff = state.current_position - state.base_position
        if abs(diff) < state.config.lot_size:
            return
        volume = min(abs(diff), state.config.max_order_volume)
        volume = live._align_volume(volume, state.config.lot_size)
        if diff > 0 and volume >= state.config.lot_size and strategy._can_sell(state, volume):
            _fill_order(strategy, state, "SELL", volume, "close_to_base", trades)
        elif diff < 0 and volume >= state.config.lot_size and strategy._can_buy(state, volume, quote.last_price):
            _fill_order(strategy, state, "BUY", volume, "close_to_base", trades)
        return

    if now.time() >= strategy.config.runtime.stop_new_order_clock():
        return

    if quote.bid1 > 0 and quote.ask1 > 0:
        spread_pct = (quote.ask1 - quote.bid1) / max(quote.last_price, 1e-9)
        if spread_pct > state.config.max_spread_pct:
            return

    target_steps = strategy._calc_target_steps(state, quote.last_price, state.dynamic_grid_pct or state.config.grid_pct, state.last_signal_score)
    step_diff = target_steps - state.actual_steps()
    if step_diff == 0:
        return

    unit = state.unit_volume()
    if step_diff > 0:
        volume = min(step_diff * unit, state.config.max_order_volume)
        volume = live._align_volume(volume, state.config.lot_size)
        if volume >= state.config.lot_size and strategy._can_buy(state, volume, quote.last_price):
            _fill_order(strategy, state, "BUY", volume, f"grid_buy|score={state.last_signal_score:.3f}", trades)
    else:
        volume = min(abs(step_diff) * unit, state.config.max_order_volume)
        volume = live._align_volume(min(volume, state.can_use_volume), state.config.lot_size)
        if volume >= state.config.lot_size and strategy._can_sell(state, volume):
            _fill_order(strategy, state, "SELL", volume, f"grid_sell|score={state.last_signal_score:.3f}", trades)


def _write_signal_csv(path: Path, snapshots: Sequence[SignalSnapshot]) -> None:
    if not snapshots:
        return
    feature_keys = sorted({key for snapshot in snapshots for key in snapshot.features})
    rows: List[Dict[str, Any]] = []
    for snapshot in snapshots:
        row: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(snapshot.ts).isoformat(sep=" "),
            "ts": snapshot.ts,
            "price": snapshot.price,
            "score": snapshot.score,
            "grid_pct": snapshot.grid_pct,
            "position": snapshot.position,
        }
        for key in feature_keys:
            row[key] = snapshot.features.get(key, "")
        rows.append(row)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _compute_factor_ic(
    snapshots: Sequence[SignalSnapshot],
    horizon_seconds: int,
) -> Dict[str, float]:
    if len(snapshots) < 3:
        return {}
    timestamps = [snapshot.ts for snapshot in snapshots]
    prices = [snapshot.price for snapshot in snapshots]
    forward_returns: List[Optional[float]] = []
    for idx, ts in enumerate(timestamps):
        target = ts + horizon_seconds
        future_idx = bisect.bisect_left(timestamps, target, lo=idx + 1)
        if future_idx >= len(prices):
            forward_returns.append(None)
            continue
        forward_returns.append(live._safe_pct_return(prices[idx], prices[future_idx]))

    feature_keys = sorted({key for snapshot in snapshots for key in snapshot.features if key.startswith("alpha101_")})
    result: Dict[str, float] = {}
    for key in feature_keys + ["score"]:
        values_x: List[float] = []
        values_y: List[float] = []
        for idx, snapshot in enumerate(snapshots):
            future_ret = forward_returns[idx]
            if future_ret is None:
                continue
            if key == "score":
                feature_value = snapshot.score
            else:
                if key not in snapshot.features:
                    continue
                feature_value = _to_float(snapshot.features[key], 0.0)
            values_x.append(feature_value)
            values_y.append(future_ret)
        if len(values_x) >= 5:
            result[key] = _pearson(values_x, values_y)
    return result


def _build_report(
    strategy: live.LofT0GridStrategy,
    state: live.SymbolState,
    trades: Sequence[ReplayTrade],
    snapshots: Sequence[SignalSnapshot],
    start_price: float,
    end_price: float,
    initial_cash: float,
    initial_position: int,
    forward_seconds: int,
) -> Dict[str, Any]:
    start_equity = initial_cash + initial_position * start_price
    end_equity = strategy.available_cash + state.current_position * end_price
    turnover = sum(trade.price * trade.volume for trade in trades)
    factor_ic = _compute_factor_ic(snapshots, forward_seconds)
    best_ic = sorted(factor_ic.items(), key=lambda item: abs(item[1]), reverse=True)[:10]
    return {
        "summary": {
            "start_price": start_price,
            "end_price": end_price,
            "initial_cash": initial_cash,
            "ending_cash": strategy.available_cash,
            "initial_position": initial_position,
            "base_position": state.base_position,
            "ending_position": state.current_position,
            "start_equity": start_equity,
            "end_equity": end_equity,
            "pnl": end_equity - start_equity,
            "return_pct": live._safe_pct_return(start_equity, end_equity),
            "trades": len(trades),
            "turnover": turnover,
            "daily_buy_amount": state.daily_buy_amount,
            "daily_sell_amount": state.daily_sell_amount,
            "signal_samples": len(snapshots),
        },
        "top_factor_ic": [{"name": name, "ic": value} for name, value in best_ic],
        "factor_ic": factor_ic,
        "recent_trades": [
            {
                "timestamp": datetime.fromtimestamp(trade.ts).isoformat(sep=" "),
                "side": trade.side,
                "price": trade.price,
                "volume": trade.volume,
                "reason": trade.reason,
                "score": trade.score,
                "position": trade.position,
                "cash": trade.cash,
            }
            for trade in trades[-10:]
        ],
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline replay for LOF T+0 grid strategy and Alpha101 signals")
    parser.add_argument("--csv", required=True, help="Path to tick csv file")
    parser.add_argument("--config", help="Path to strategy json config")
    parser.add_argument("--symbol", default="161129.SZ", help="Fallback symbol if csv has no symbol column")
    parser.add_argument("--initial-cash", type=float, default=150000.0, help="Starting cash for replay")
    parser.add_argument("--base-position", type=int, default=0, help="Starting base position")
    parser.add_argument("--start-time", default="09:30:00", help="Replay start clock")
    parser.add_argument("--end-time", default="15:00:00", help="Replay end clock")
    parser.add_argument("--forward-seconds", type=int, default=300, help="Forward horizon for factor IC")
    parser.add_argument("--signal-csv", help="Optional output csv for signal snapshots")
    parser.add_argument("--report-json", help="Optional output json report")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    csv_path = Path(args.csv).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve() if args.config else None
    config = _build_offline_config(config_path, args.symbol, args.initial_cash, args.base_position)
    live.setup_logging(config.runtime.log_level)

    strategy = live.LofT0GridStrategy(config)
    symbol = strategy.symbols[0]
    state = strategy.states[symbol]
    strategy.available_cash = args.initial_cash
    state.current_position = live._align_volume(args.base_position, state.config.lot_size)
    state.can_use_volume = state.current_position
    state.base_position = state.current_position if state.config.base_position is None else live._align_volume(state.config.base_position, state.config.lot_size)
    state.price_tick = state.config.price_tick or 0.001
    state.dynamic_grid_pct = state.config.grid_pct
    initial_position = state.current_position

    session_start = _parse_clock(args.start_time) if args.start_time else None
    session_end = _parse_clock(args.end_time) if args.end_time else None
    snapshots: List[SignalSnapshot] = []
    trades: List[ReplayTrade] = []
    current_trade_day: Optional[datetime.date] = None
    start_price = 0.0
    end_price = 0.0

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            parsed = _row_to_quote(row, symbol)
            if parsed is None:
                continue
            row_symbol, quote = parsed
            if row_symbol != symbol:
                continue
            trade_day = datetime.fromtimestamp(quote.ts).date()
            if current_trade_day != trade_day:
                current_trade_day = trade_day
                _reset_trade_day(strategy, state)
            if start_price <= 0:
                start_price = quote.last_price
            end_price = quote.last_price
            _process_quote(strategy, state, quote, snapshots, trades, session_start, session_end)

    if start_price <= 0 or end_price <= 0:
        raise RuntimeError(f"no valid rows loaded from {csv_path}")

    report = _build_report(
        strategy=strategy,
        state=state,
        trades=trades,
        snapshots=snapshots,
        start_price=start_price,
        end_price=end_price,
        initial_cash=args.initial_cash,
        initial_position=initial_position,
        forward_seconds=max(1, args.forward_seconds),
    )

    if args.signal_csv:
        _write_signal_csv(Path(args.signal_csv).expanduser().resolve(), snapshots)
    if args.report_json:
        report_path = Path(args.report_json).expanduser().resolve()
        with report_path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)

    summary = report["summary"]
    logging.info(
        "replay complete pnl=%.2f return=%.4f trades=%s signals=%s",
        summary["pnl"],
        summary["return_pct"],
        summary["trades"],
        summary["signal_samples"],
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
