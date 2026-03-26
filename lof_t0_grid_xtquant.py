#!/usr/bin/env python3
# -*- coding: gbk -*-
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import math
import os
import signal
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

try:
    from xtquant import xtconstant, xtdata
    from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
    from xtquant.xttype import StockAccount
except Exception as exc:  # pragma: no cover - runtime dependency
    xtconstant = None
    xtdata = None
    XtQuantTrader = None
    StockAccount = None

    class XtQuantTraderCallback:  # type: ignore[no-redef]
        pass

    XT_IMPORT_ERROR: Optional[Exception] = exc
else:
    XT_IMPORT_ERROR = None


# Strategy-file defaults.
# These values make the file runnable without an external json config.
DEFAULT_ACCOUNT_ID = "8887271027"
DEFAULT_ACCOUNT_TYPE = "STOCK"
DEFAULT_SYMBOL = "161129"
DEFAULT_MINIQMT_DIR = ""
DEFAULT_SESSION_ID: Optional[int] = None
DEFAULT_DRY_RUN = True
DEFAULT_BASE_POSITION: Optional[int] = None
DEFAULT_ORDER_VOLUME = 100
DEFAULT_GRID_PCT = 0.006
DEFAULT_MAX_INTRADAY_LONG = 3000
DEFAULT_MAX_INTRADAY_SHORT = 3000
DEFAULT_MAX_POSITION_VALUE = 150000.0
DEFAULT_MAX_DAILY_BUY_AMOUNT = 100000.0
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOCAL_OVERRIDE_FILE = Path(__file__).with_suffix(".local.json")
DEFAULT_DRIVER_STOCK = "161129.SZ"
DEFAULT_DRIVER_PERIOD = "tick"
DEFAULT_ALPHA101_ENABLED = True
DEFAULT_ALPHA101_BLEND = 0.45
DEFAULT_ALPHA101_BAR_SECONDS = 60
DEFAULT_ALPHA101_LOOKBACK = 20
DEFAULT_ALPHA101_MIN_BARS = 30
DEFAULT_ALPHA101_WEIGHTS: Dict[str, float] = {
    "alpha001": 0.08,
    "alpha002": 0.11,
    "alpha003": 0.05,
    "alpha004": 0.05,
    "alpha005": 0.06,
    "alpha006": 0.05,
    "alpha007": 0.07,
    "alpha008": 0.05,
    "alpha009": 0.06,
    "alpha010": 0.05,
    "alpha011": 0.04,
    "alpha012": 0.07,
    "alpha013": 0.04,
    "alpha014": 0.04,
    "alpha016": 0.04,
    "alpha017": 0.04,
    "alpha018": 0.05,
    "alpha026": 0.06,
    "alpha041": 0.07,
    "alpha042": 0.04,
    "alpha043": 0.07,
    "alpha044": 0.04,
    "alpha053": 0.03,
    "alpha054": 0.03,
    "alpha055": 0.04,
    "alpha101": 0.08,
}


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _field(obj: Any, *names: str, default: Any = None) -> Any:
    if obj is None:
        return default
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


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


def _to_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        return str(value)
    except Exception:
        return default


def _normalize_symbol(symbol: str) -> str:
    text = symbol.strip().upper()
    if not text:
        raise ValueError("empty symbol")
    if text.isdigit() and len(text) == 6:
        market = "SH" if text.startswith(("5", "6", "9")) else "SZ"
        return f"{text}.{market}"
    if "." in text:
        left, right = text.split(".", 1)
        if right in {"SH", "SZ"}:
            return f"{left}.{right}"
        if left in {"SH", "SZ"}:
            return f"{right}.{left}"
    if text.startswith("SH") or text.startswith("SZ"):
        return f"{text[2:]}.{text[:2]}"
    raise ValueError(f"unsupported symbol format: {symbol}")


def _parse_xt_timestamp(value: Any, default_ts: float) -> float:
    if value is None:
        return default_ts
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default_ts
        try:
            raw = float(text)
        except ValueError:
            normalized = text.replace("T", " ").replace("/", "-")
            for fmt in (
                "%Y%m%d %H:%M:%S.%f",
                "%Y%m%d %H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y%m%d%H%M%S",
            ):
                try:
                    return datetime.strptime(normalized, fmt).timestamp()
                except ValueError:
                    continue
            return default_ts
    else:
        try:
            raw = float(value)
        except Exception:
            return default_ts

    if raw > 10**12:
        return raw / 1000.0
    if raw > 10**9:
        return raw
    return default_ts


def _record_to_dict(record: Any) -> Optional[Dict[str, Any]]:
    if record is None:
        return None
    if isinstance(record, dict):
        return record

    dtype = getattr(record, "dtype", None)
    names = getattr(dtype, "names", None)
    if names:
        mapped: Dict[str, Any] = {}
        for name in names:
            value = record[name]
            if hasattr(value, "item"):
                try:
                    value = value.item()
                except Exception:
                    pass
            mapped[str(name)] = value
        return mapped
    return None


def _parse_clock(value: str) -> dt_time:
    parts = value.split(":")
    if len(parts) == 2:
        parts.append("0")
    if len(parts) != 3:
        raise ValueError(f"invalid time string: {value}")
    hour, minute, second = (int(x) for x in parts)
    return dt_time(hour=hour, minute=minute, second=second)


def _in_a_share_session(now: datetime) -> bool:
    current = now.time()
    in_morning = dt_time(9, 30) <= current < dt_time(11, 30)
    in_afternoon = dt_time(13, 0) <= current < dt_time(15, 0)
    return in_morning or in_afternoon


def _align_volume(volume: int, lot_size: int) -> int:
    if lot_size <= 0:
        return max(0, volume)
    return max(0, volume // lot_size * lot_size)


def _align_price(price: float, tick_size: float, side: str) -> float:
    if tick_size <= 0:
        return round(price, 3)
    scaled = price / tick_size
    if side == "BUY":
        aligned = math.ceil(scaled - 1e-9) * tick_size
    else:
        aligned = math.floor(scaled + 1e-9) * tick_size
    return round(max(aligned, tick_size), 6)


def _extract_best_level(values: Any) -> float:
    if isinstance(values, (list, tuple)):
        for item in values:
            num = _to_float(item, 0.0)
            if num > 0:
                return num
        return 0.0
    return _to_float(values, 0.0)


def _mean(values: List[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _stdev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    var = sum((item - avg) ** 2 for item in values) / (len(values) - 1)
    return math.sqrt(max(var, 0.0))


def _safe_log_return(base: float, value: float) -> float:
    if base <= 0 or value <= 0:
        return 0.0
    return math.log(value / base)


def _safe_pct_return(base: float, value: float) -> float:
    if base <= 0:
        return 0.0
    return value / base - 1.0


def _safe_ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    if abs(denominator) <= 1e-12:
        return default
    return numerator / denominator


def _sign(value: float) -> float:
    if value > 0:
        return 1.0
    if value < 0:
        return -1.0
    return 0.0


def _sum_window(values: List[float], window: int) -> float:
    if not values:
        return 0.0
    if window <= 0 or window >= len(values):
        return sum(values)
    return sum(values[-window:])


def _delta(values: List[float], periods: int = 1) -> float:
    if not values or periods <= 0 or len(values) <= periods:
        return 0.0
    return values[-1] - values[-1 - periods]


def _window_mean(values: List[float], window: int) -> float:
    if not values:
        return 0.0
    if window <= 0 or window >= len(values):
        return _mean(values)
    return _mean(values[-window:])


def _window_std(values: List[float], window: int) -> float:
    if not values:
        return 0.0
    if window <= 0 or window >= len(values):
        return _stdev(values)
    return _stdev(values[-window:])


def _ts_min(values: List[float], window: int) -> float:
    if not values:
        return 0.0
    if window <= 0 or window >= len(values):
        return min(values)
    return min(values[-window:])


def _ts_max(values: List[float], window: int) -> float:
    if not values:
        return 0.0
    if window <= 0 or window >= len(values):
        return max(values)
    return max(values[-window:])


def _signed_power(value: float, power: float) -> float:
    return math.copysign(abs(value) ** power, value)


def _ts_argmax(values: List[float], window: int) -> float:
    if not values:
        return 0.0
    start = 0 if window <= 0 else max(0, len(values) - window)
    sample = values[start:]
    if not sample:
        return 0.0
    index = max(range(len(sample)), key=lambda idx: sample[idx])
    if len(sample) == 1:
        return 0.0
    return index / (len(sample) - 1)


def _percentile_rank(values: List[float], target: float) -> float:
    if not values:
        return 0.5
    lower = sum(1 for item in values if item < target)
    equal = sum(1 for item in values if item == target)
    return (lower + 0.5 * equal) / len(values)


def _rolling_percentile_series(values: List[float], window: int) -> List[float]:
    if not values:
        return []
    ranks: List[float] = []
    for idx, value in enumerate(values):
        start = 0 if window <= 0 else max(0, idx - window + 1)
        ranks.append(_percentile_rank(values[start : idx + 1], value))
    return ranks


def _ts_rank(values: List[float], window: int) -> float:
    if not values:
        return 0.5
    start = 0 if window <= 0 else max(0, len(values) - window)
    sample = values[start:]
    return _percentile_rank(sample, sample[-1])


def _rolling_corr(values_x: List[float], values_y: List[float], window: int) -> float:
    length = min(len(values_x), len(values_y))
    if length < 2:
        return 0.0
    if window > 0:
        length = min(length, window)
    xs = values_x[-length:]
    ys = values_y[-length:]
    mean_x = _mean(xs)
    mean_y = _mean(ys)
    denom_x = sum((item - mean_x) ** 2 for item in xs)
    denom_y = sum((item - mean_y) ** 2 for item in ys)
    if denom_x <= 0 or denom_y <= 0:
        return 0.0
    cov = sum((xs[idx] - mean_x) * (ys[idx] - mean_y) for idx in range(length))
    return cov / math.sqrt(denom_x * denom_y)


def _rolling_cov(values_x: List[float], values_y: List[float], window: int) -> float:
    length = min(len(values_x), len(values_y))
    if length < 2:
        return 0.0
    if window > 0:
        length = min(length, window)
    xs = values_x[-length:]
    ys = values_y[-length:]
    mean_x = _mean(xs)
    mean_y = _mean(ys)
    return sum((xs[idx] - mean_x) * (ys[idx] - mean_y) for idx in range(length)) / max(length - 1, 1)


def _rolling_corr_series(values_x: List[float], values_y: List[float], window: int) -> List[float]:
    length = min(len(values_x), len(values_y))
    result: List[float] = []
    for idx in range(length):
        result.append(_rolling_corr(values_x[: idx + 1], values_y[: idx + 1], window))
    return result


def _rolling_cov_series(values_x: List[float], values_y: List[float], window: int) -> List[float]:
    length = min(len(values_x), len(values_y))
    result: List[float] = []
    for idx in range(length):
        result.append(_rolling_cov(values_x[: idx + 1], values_y[: idx + 1], window))
    return result


def _center_rank(value: float) -> float:
    return _clamp(value * 2.0 - 1.0, -1.0, 1.0)


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _discover_miniqmt_dir(explicit_path: str = "") -> str:
    candidates: List[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path).expanduser())

    for env_name in ("MINIQMT_DIR", "XTQUANT_MINIQMT_DIR", "QMT_USERDATA_DIR"):
        env_value = os.getenv(env_name, "").strip()
        if env_value:
            candidates.append(Path(env_value).expanduser())

    if xtdata is not None:
        data_dir = getattr(xtdata, "data_dir", "")
        if data_dir:
            candidates.append(Path(str(data_dir)).expanduser())

    script_path = Path(__file__).resolve()
    for parent in [script_path.parent, *script_path.parents]:
        if parent.name == "userdata_mini":
            candidates.append(parent)
            break
        child = parent / "userdata_mini"
        if child.exists():
            candidates.append(child)

    home = Path.home()
    candidates.extend(
        [
            home / "userdata_mini",
            home / "Documents" / "userdata_mini",
            home / "Desktop" / "userdata_mini",
            home / "Documents" / "MiniQMT" / "userdata_mini",
            home / "Documents" / "QMT" / "userdata_mini",
            Path("/Applications/MiniQMT.app/Contents/Resources/userdata_mini"),
        ]
    )

    seen = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists() and candidate.is_dir():
            return str(candidate)

    raise RuntimeError(
        "Unable to locate MiniQMT userdata_mini automatically. "
        "Please set DEFAULT_MINIQMT_DIR or export MINIQMT_DIR."
    )


def _build_embedded_config_payload() -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "account": {
            "miniqmt_dir": _discover_miniqmt_dir(DEFAULT_MINIQMT_DIR),
            "account_id": DEFAULT_ACCOUNT_ID,
            "account_type": DEFAULT_ACCOUNT_TYPE,
            "session_id": DEFAULT_SESSION_ID,
        },
        "runtime": {
            "poll_interval_s": 1.0,
            "sync_interval_s": 3.0,
            "signal_refresh_s": 5.0,
            "order_timeout_s": 12.0,
            "pending_clear_grace_s": 3.0,
            "stop_new_order_time": "14:55:00",
            "close_back_to_base_time": "14:57:00",
            "log_level": DEFAULT_LOG_LEVEL,
            "dry_run": DEFAULT_DRY_RUN,
            "strategy_name": "lof_t0_grid",
            "remark_prefix": "lof_t0",
            "use_whole_quote_subscribe": True,
        },
        "ai": {
            "signal_hook_file": "",
            "signal_hook_func": "predict_signal",
            "formulaic_alpha_enabled": DEFAULT_ALPHA101_ENABLED,
            "formulaic_alpha_blend": DEFAULT_ALPHA101_BLEND,
            "formulaic_alpha_bar_seconds": DEFAULT_ALPHA101_BAR_SECONDS,
            "formulaic_alpha_lookback": DEFAULT_ALPHA101_LOOKBACK,
            "formulaic_alpha_min_bars": DEFAULT_ALPHA101_MIN_BARS,
            "formulaic_alpha_weights": dict(DEFAULT_ALPHA101_WEIGHTS),
        },
        "symbols": [
            {
                "symbol": DEFAULT_SYMBOL,
                "enabled": True,
                "base_position": DEFAULT_BASE_POSITION,
                "anchor_mode": "last_close",
                "anchor_price": None,
                "order_volume": DEFAULT_ORDER_VOLUME,
                "lot_size": 100,
                "grid_pct": DEFAULT_GRID_PCT,
                "min_grid_pct": 0.004,
                "max_grid_pct": 0.015,
                "max_intraday_long_volume": DEFAULT_MAX_INTRADAY_LONG,
                "max_intraday_short_volume": DEFAULT_MAX_INTRADAY_SHORT,
                "max_order_volume": 300,
                "slippage_ticks": 1,
                "price_tick": 0.001,
                "max_spread_pct": 0.003,
                "cash_reserve": 10000.0,
                "max_position_value": DEFAULT_MAX_POSITION_VALUE,
                "max_daily_buy_amount": DEFAULT_MAX_DAILY_BUY_AMOUNT,
                "max_orders_per_day": 60,
                "cooldown_s": 3.0,
                "use_adaptive_signal": True,
                "signal_bias_steps": 2,
                "stop_loss_pct": 0.03,
                "close_back_to_base": True,
            }
        ],
    }
    if DEFAULT_LOCAL_OVERRIDE_FILE.exists():
        payload = _deep_merge_dict(payload, _load_json(DEFAULT_LOCAL_OVERRIDE_FILE))
    return payload


def build_default_config() -> "StrategyConfig":
    return StrategyConfig.from_dict(_build_embedded_config_payload())


@dataclass
class AccountConfig:
    miniqmt_dir: str
    account_id: str
    account_type: str = "STOCK"
    session_id: Optional[int] = None


@dataclass
class RuntimeConfig:
    poll_interval_s: float = 1.0
    sync_interval_s: float = 3.0
    signal_refresh_s: float = 5.0
    order_timeout_s: float = 12.0
    pending_clear_grace_s: float = 3.0
    stop_new_order_time: str = "14:55:00"
    close_back_to_base_time: str = "14:57:00"
    log_level: str = "INFO"
    dry_run: bool = True
    strategy_name: str = "lof_t0_grid"
    remark_prefix: str = "lof_t0"
    use_whole_quote_subscribe: bool = True

    def stop_new_order_clock(self) -> dt_time:
        return _parse_clock(self.stop_new_order_time)

    def close_back_to_base_clock(self) -> dt_time:
        return _parse_clock(self.close_back_to_base_time)


@dataclass
class AIConfig:
    signal_hook_file: str = ""
    signal_hook_func: str = "predict_signal"
    formulaic_alpha_enabled: bool = DEFAULT_ALPHA101_ENABLED
    formulaic_alpha_blend: float = DEFAULT_ALPHA101_BLEND
    formulaic_alpha_bar_seconds: int = DEFAULT_ALPHA101_BAR_SECONDS
    formulaic_alpha_lookback: int = DEFAULT_ALPHA101_LOOKBACK
    formulaic_alpha_min_bars: int = DEFAULT_ALPHA101_MIN_BARS
    formulaic_alpha_weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_ALPHA101_WEIGHTS))


@dataclass
class SymbolConfig:
    symbol: str
    enabled: bool = True
    base_position: Optional[int] = None
    anchor_mode: str = "last_close"
    anchor_price: Optional[float] = None
    order_volume: int = 100
    lot_size: int = 100
    grid_pct: float = 0.006
    min_grid_pct: float = 0.004
    max_grid_pct: float = 0.018
    max_intraday_long_volume: int = 3000
    max_intraday_short_volume: int = 3000
    max_order_volume: int = 200
    slippage_ticks: int = 1
    price_tick: Optional[float] = None
    max_spread_pct: float = 0.003
    cash_reserve: float = 10000.0
    max_position_value: Optional[float] = None
    max_daily_buy_amount: Optional[float] = None
    max_orders_per_day: int = 80
    cooldown_s: float = 3.0
    use_adaptive_signal: bool = True
    signal_bias_steps: int = 2
    stop_loss_pct: float = 0.03
    close_back_to_base: bool = True

    @property
    def normalized_symbol(self) -> str:
        return _normalize_symbol(self.symbol)


@dataclass
class QuotePoint:
    ts: float
    last_price: float
    bid1: float
    ask1: float
    bid_vol1: float
    ask_vol1: float
    last_close: float
    total_volume: float = 0.0
    total_amount: float = 0.0


@dataclass
class BarPoint:
    bucket_ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    amount: float = 0.0
    vwap: float = 0.0


@dataclass
class PendingOrder:
    order_id: int
    side: str
    volume: int
    price: float
    created_ts: float
    filled_volume: int = 0


@dataclass
class SymbolState:
    config: SymbolConfig
    base_position: int = 0
    current_position: int = 0
    can_use_volume: int = 0
    avg_price: float = 0.0
    market_value: float = 0.0
    anchor_price: float = 0.0
    last_signal_score: float = 0.0
    dynamic_grid_pct: float = 0.0
    last_signal_ts: float = 0.0
    last_order_ts: float = 0.0
    quote_history: Deque[QuotePoint] = field(default_factory=lambda: deque(maxlen=14400))
    latest_quote: Optional[QuotePoint] = None
    pending_order: Optional[PendingOrder] = None
    daily_buy_amount: float = 0.0
    daily_sell_amount: float = 0.0
    daily_order_count: int = 0
    price_tick: float = 0.001
    up_limit: float = 0.0
    down_limit: float = 0.0
    instrument_name: str = ""

    def unit_volume(self) -> int:
        return max(self.config.lot_size, _align_volume(self.config.order_volume, self.config.lot_size))

    def long_steps_limit(self) -> int:
        return max(0, self.config.max_intraday_long_volume // self.unit_volume())

    def short_steps_limit(self) -> int:
        return max(0, self.config.max_intraday_short_volume // self.unit_volume())

    def actual_steps(self) -> int:
        unit = self.unit_volume()
        if unit <= 0:
            return 0
        raw = (self.current_position - self.base_position) / unit
        return int(round(raw))


@dataclass
class StrategyConfig:
    account: AccountConfig
    runtime: RuntimeConfig
    symbols: List[SymbolConfig]
    ai: AIConfig

    @staticmethod
    def from_dict(payload: Dict[str, Any]) -> "StrategyConfig":
        account = AccountConfig(**payload["account"])
        runtime = RuntimeConfig(**payload.get("runtime", {}))
        ai = AIConfig(**payload.get("ai", {}))
        symbols = [SymbolConfig(**item) for item in payload["symbols"]]
        return StrategyConfig(account=account, runtime=runtime, symbols=symbols, ai=ai)


class SignalEngine:
    def __init__(self, ai_config: AIConfig) -> None:
        self.ai_config = ai_config
        self.hook: Optional[Callable[[Dict[str, Any]], Any]] = None
        if ai_config.signal_hook_file:
            self.hook = self._load_hook(Path(ai_config.signal_hook_file), ai_config.signal_hook_func)

    @staticmethod
    def _load_hook(path: Path, func_name: str) -> Optional[Callable[[Dict[str, Any]], Any]]:
        if not path.exists():
            logging.warning("signal hook file not found: %s", path)
            return None
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            logging.warning("failed to load signal hook from %s", path)
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn = getattr(module, func_name, None)
        if fn is None or not callable(fn):
            logging.warning("signal hook function %s not found in %s", func_name, path)
            return None
        return fn

    def _build_bars(self, history: List[QuotePoint]) -> List[BarPoint]:
        if not history:
            return []
        bar_seconds = max(15, int(self.ai_config.formulaic_alpha_bar_seconds))
        max_bars = max(int(self.ai_config.formulaic_alpha_min_bars) * 4, 120)
        bars: List[BarPoint] = []
        current: Optional[BarPoint] = None
        prev_quote: Optional[QuotePoint] = None

        for quote in history:
            if quote.last_price <= 0:
                continue
            bucket_ts = int(quote.ts // bar_seconds) * bar_seconds
            if current is None or current.bucket_ts != bucket_ts:
                if current is not None:
                    if current.vwap <= 0:
                        current.vwap = current.close
                    bars.append(current)
                current = BarPoint(
                    bucket_ts=bucket_ts,
                    open=quote.last_price,
                    high=quote.last_price,
                    low=quote.last_price,
                    close=quote.last_price,
                    vwap=quote.last_price,
                )
            else:
                current.high = max(current.high, quote.last_price)
                current.low = min(current.low, quote.last_price)
                current.close = quote.last_price

            volume_delta = 0.0
            amount_delta = 0.0
            if prev_quote is not None:
                if quote.total_volume > 0 and prev_quote.total_volume > 0:
                    volume_delta = max(0.0, quote.total_volume - prev_quote.total_volume)
                if quote.total_amount > 0 and prev_quote.total_amount > 0:
                    amount_delta = max(0.0, quote.total_amount - prev_quote.total_amount)

            current.volume += volume_delta
            current.amount += amount_delta
            if current.volume > 0 and current.amount > 0:
                current.vwap = current.amount / current.volume
            prev_quote = quote

        if current is not None:
            if current.vwap <= 0:
                current.vwap = current.close
            bars.append(current)

        if len(bars) > max_bars:
            bars = bars[-max_bars:]
        return bars

    def _compute_formulaic_alpha_bundle(self, history: List[QuotePoint]) -> Tuple[float, Dict[str, float]]:
        if not self.ai_config.formulaic_alpha_enabled:
            return 0.0, {}

        bars = self._build_bars(history)
        min_bars = max(12, int(self.ai_config.formulaic_alpha_min_bars))
        if len(bars) < min_bars:
            return 0.0, {}

        opens = [bar.open for bar in bars]
        highs = [bar.high for bar in bars]
        lows = [bar.low for bar in bars]
        closes = [bar.close for bar in bars]
        volumes = [max(0.0, bar.volume) for bar in bars]
        vwaps = [bar.vwap if bar.vwap > 0 else bar.close for bar in bars]
        if _sum_window(volumes, len(volumes)) <= 0:
            return 0.0, {}

        lookback = max(5, int(self.ai_config.formulaic_alpha_lookback))
        returns = [0.0]
        for idx in range(1, len(closes)):
            returns.append(_safe_pct_return(closes[idx - 1], closes[idx]))
        bar_volatility = max(_stdev(returns[-20:]), 1e-4)

        def _center_ts_rank(values: List[float], window: int) -> float:
            return _center_rank(_ts_rank(values, window))

        log_volumes = [math.log(max(volume, 1.0)) for volume in volumes]
        delta_log_vol_2 = [0.0] * len(log_volumes)
        for idx in range(2, len(log_volumes)):
            delta_log_vol_2[idx] = log_volumes[idx] - log_volumes[idx - 2]
        close_open_ret = [
            _safe_pct_return(opens[idx], closes[idx]) if opens[idx] > 0 else 0.0
            for idx in range(len(closes))
        ]

        # The paper uses cross-sectional ranks; this single-symbol strategy
        # replaces them with trailing time-series percentile ranks.
        rank_close = _rolling_percentile_series(closes, lookback)
        rank_delta_log_vol_2 = _rolling_percentile_series(delta_log_vol_2, lookback)
        rank_close_open_ret = _rolling_percentile_series(close_open_ret, lookback)
        rank_open = _rolling_percentile_series(opens, lookback)
        rank_high = _rolling_percentile_series(highs, lookback)
        rank_volume = _rolling_percentile_series(volumes, lookback)
        rank_low = _rolling_percentile_series(lows, lookback)

        open_minus_vwap_mean: List[float] = []
        close_minus_vwap: List[float] = []
        close_minus_open: List[float] = []
        close_open_abs: List[float] = []
        vwap_minus_close: List[float] = []
        vwap_plus_close: List[float] = []
        adv20_series: List[float] = []
        volume_adv_ratio: List[float] = []
        delta_close_1_series: List[float] = []
        delta_close_7_series: List[float] = []
        delta_returns_3_series: List[float] = []
        delta_volume_3_series: List[float] = []
        delta_delta_close_1_series: List[float] = []
        abs_delta_close_7_series: List[float] = []
        ts_rank_close_10_series: List[float] = []
        ts_rank_volume_adv_5_series: List[float] = []
        ts_max_vwap_close_3_series: List[float] = []
        ts_min_vwap_close_3_series: List[float] = []
        alpha008_base_series: List[float] = []
        alpha018_combo_series: List[float] = []
        alpha053_base_series: List[float] = []
        alpha055_range_series: List[float] = []

        for idx in range(len(closes)):
            start = max(0, idx - 9)
            open_minus_vwap_mean.append(opens[idx] - _mean(vwaps[start : idx + 1]))
            close_minus_vwap.append(closes[idx] - vwaps[idx])
            close_minus_open.append(closes[idx] - opens[idx])
            close_open_abs.append(abs(closes[idx] - opens[idx]))
            vwap_minus_close.append(vwaps[idx] - closes[idx])
            vwap_plus_close.append(vwaps[idx] + closes[idx])

            adv20 = _window_mean(volumes[: idx + 1], 20)
            adv20_series.append(adv20)
            volume_adv_ratio.append(_safe_ratio(volumes[idx], max(adv20, 1.0), 0.0))

            delta_close_1 = closes[idx] - closes[idx - 1] if idx >= 1 else 0.0
            delta_close_7 = closes[idx] - closes[idx - 7] if idx >= 7 else 0.0
            delta_returns_3 = returns[idx] - returns[idx - 3] if idx >= 3 else 0.0
            delta_volume_3 = volumes[idx] - volumes[idx - 3] if idx >= 3 else 0.0
            delta_delta_close_1 = (
                delta_close_1 - (closes[idx - 1] - closes[idx - 2])
                if idx >= 2
                else 0.0
            )
            delta_close_1_series.append(delta_close_1)
            delta_close_7_series.append(delta_close_7)
            delta_returns_3_series.append(delta_returns_3)
            delta_volume_3_series.append(delta_volume_3)
            delta_delta_close_1_series.append(delta_delta_close_1)
            abs_delta_close_7_series.append(abs(delta_close_7))

            ts_rank_close_10_series.append(_ts_rank(closes[: idx + 1], 10))
            ts_rank_volume_adv_5_series.append(_ts_rank(volume_adv_ratio[: idx + 1], 5))
            ts_max_vwap_close_3_series.append(_ts_max(vwap_minus_close, 3))
            ts_min_vwap_close_3_series.append(_ts_min(vwap_minus_close, 3))

            alpha008_base = _sum_window(opens[: idx + 1], 5) * _sum_window(returns[: idx + 1], 5)
            alpha008_base_series.append(alpha008_base)

            alpha018_combo_series.append(
                _window_std(close_open_abs[: idx + 1], 5)
                + close_minus_open[idx]
                + _rolling_corr(closes[: idx + 1], opens[: idx + 1], 10)
            )

            alpha053_base_series.append(
                _safe_ratio(
                    (closes[idx] - lows[idx]) - (highs[idx] - closes[idx]),
                    closes[idx] - lows[idx],
                    0.0,
                )
            )

            low_12 = _ts_min(lows[: idx + 1], 12)
            high_12 = _ts_max(highs[: idx + 1], 12)
            alpha055_range_series.append(
                _safe_ratio(closes[idx] - low_12, high_12 - low_12, 0.5)
            )

        ts_rank_volume_5 = [_ts_rank(volumes[: idx + 1], 5) for idx in range(len(volumes))]
        ts_rank_high_5 = [_ts_rank(highs[: idx + 1], 5) for idx in range(len(highs))]
        corr_rank_vol_high = _rolling_corr_series(ts_rank_volume_5, ts_rank_high_5, 5)
        cov_rank_close_volume = _rolling_cov_series(rank_close, rank_volume, 5)
        cov_rank_high_volume = _rolling_cov_series(rank_high, rank_volume, 5)
        rank_vwap_minus_close = _rolling_percentile_series(vwap_minus_close, lookback)
        rank_vwap_plus_close = _rolling_percentile_series(vwap_plus_close, lookback)
        rank_alpha055_range = _rolling_percentile_series(alpha055_range_series, lookback)
        alpha008_signal_series: List[float] = []
        alpha009_signal_series: List[float] = []
        alpha010_signal_series: List[float] = []
        for idx in range(len(closes)):
            delayed_alpha008 = alpha008_base_series[idx - 10] if idx >= 10 else alpha008_base_series[0]
            alpha008_signal_series.append(alpha008_base_series[idx] - delayed_alpha008)

            delta_close_slice = delta_close_1_series[: idx + 1]
            if 0.0 < _ts_min(delta_close_slice, 5):
                alpha009_signal_series.append(delta_close_slice[-1])
            elif _ts_max(delta_close_slice, 5) < 0.0:
                alpha009_signal_series.append(delta_close_slice[-1])
            else:
                alpha009_signal_series.append(-delta_close_slice[-1])

            if 0.0 < _ts_min(delta_close_slice, 4):
                alpha010_signal_series.append(delta_close_slice[-1])
            elif _ts_max(delta_close_slice, 4) < 0.0:
                alpha010_signal_series.append(delta_close_slice[-1])
            else:
                alpha010_signal_series.append(-delta_close_slice[-1])

        alpha001_source = [
            _window_std(returns[: idx + 1], 20) if returns[idx] < 0 else closes[idx]
            for idx in range(len(closes))
        ]
        alpha001_signed_power = [_signed_power(value, 2.0) for value in alpha001_source]
        alpha041_raw = math.sqrt(max(highs[-1] * lows[-1], 0.0)) - vwaps[-1]
        alpha042_ratio = _safe_ratio(rank_vwap_minus_close[-1], max(rank_vwap_plus_close[-1], 0.2), 1.0)
        alpha053_delta = alpha053_base_series[-1] - alpha053_base_series[-10] if len(alpha053_base_series) >= 10 else 0.0
        alpha054_raw = -_safe_ratio(
            (lows[-1] - closes[-1]) * (opens[-1] ** 5),
            (lows[-1] - highs[-1]) * max(closes[-1] ** 5, 1e-12),
            0.0,
        )
        alpha101_raw = (closes[-1] - opens[-1]) / ((highs[-1] - lows[-1]) + 0.001)

        alpha_values = {
            "alpha001": _clamp((_ts_argmax(alpha001_signed_power, 5) - 0.5) * 2.0, -1.0, 1.0),
            "alpha002": _clamp(-_rolling_corr(rank_delta_log_vol_2, rank_close_open_ret, 6), -1.0, 1.0),
            "alpha003": _clamp(-_rolling_corr(rank_open, rank_volume, 10), -1.0, 1.0),
            "alpha004": _clamp(-_center_rank(_ts_rank(rank_low, 9)), -1.0, 1.0),
            "alpha005": _clamp(
                _center_rank(_ts_rank(open_minus_vwap_mean, lookback))
                * -abs(_center_rank(_ts_rank(close_minus_vwap, lookback))),
                -1.0,
                1.0,
            ),
            "alpha006": _clamp(-_rolling_corr(opens, volumes, 10), -1.0, 1.0),
            "alpha007": _clamp(
                (-_center_rank(_ts_rank(abs_delta_close_7_series, 60)) * _sign(delta_close_7_series[-1]))
                if volume_adv_ratio[-1] > 1.0
                else -1.0,
                -1.0,
                1.0,
            ),
            "alpha008": _clamp(-_center_ts_rank(alpha008_signal_series, lookback), -1.0, 1.0),
            "alpha009": _clamp(math.tanh(_safe_ratio(alpha009_signal_series[-1], max(closes[-1], 1e-6) * bar_volatility, 0.0)), -1.0, 1.0),
            "alpha010": _clamp(_center_ts_rank(alpha010_signal_series, lookback), -1.0, 1.0),
            "alpha011": _clamp(
                (
                    _center_ts_rank(ts_max_vwap_close_3_series, lookback)
                    + _center_ts_rank(ts_min_vwap_close_3_series, lookback)
                )
                * _center_ts_rank(delta_volume_3_series, lookback),
                -1.0,
                1.0,
            ),
            "alpha012": _clamp(
                _sign(volumes[-1] - volumes[-2]) * (-math.tanh(returns[-1] / bar_volatility))
                if len(volumes) >= 2
                else 0.0,
                -1.0,
                1.0,
            ),
            "alpha013": _clamp(-_center_ts_rank(cov_rank_close_volume, lookback), -1.0, 1.0),
            "alpha014": _clamp(
                (-_center_ts_rank(delta_returns_3_series, lookback)) * _rolling_corr(opens, volumes, 10),
                -1.0,
                1.0,
            ),
            "alpha016": _clamp(-_center_ts_rank(cov_rank_high_volume, lookback), -1.0, 1.0),
            "alpha017": _clamp(
                (-_center_ts_rank(ts_rank_close_10_series, lookback))
                * _center_ts_rank(delta_delta_close_1_series, lookback)
                * _center_ts_rank(ts_rank_volume_adv_5_series, lookback),
                -1.0,
                1.0,
            ),
            "alpha018": _clamp(-_center_ts_rank(alpha018_combo_series, lookback), -1.0, 1.0),
            "alpha026": _clamp(-_ts_max(corr_rank_vol_high, 3), -1.0, 1.0),
            "alpha041": _clamp(math.tanh(_safe_ratio(alpha041_raw, max(vwaps[-1], 1e-6), 0.0) * 50.0), -1.0, 1.0),
            "alpha042": _clamp(math.tanh((alpha042_ratio - 1.0) * 2.0), -1.0, 1.0),
            "alpha043": _clamp(
                _center_rank(_ts_rank(volume_adv_ratio, 20)) * _center_rank(_ts_rank([-value for value in delta_close_7_series], 8)),
                -1.0,
                1.0,
            ),
            "alpha044": _clamp(-_rolling_corr(highs, rank_volume, 5), -1.0, 1.0),
            "alpha053": _clamp(math.tanh(-alpha053_delta), -1.0, 1.0),
            "alpha054": _clamp(math.tanh(alpha054_raw - 1.0), -1.0, 1.0),
            "alpha055": _clamp(-_rolling_corr(rank_alpha055_range, rank_volume, 6), -1.0, 1.0),
            "alpha101": _clamp(math.tanh(alpha101_raw), -1.0, 1.0),
        }

        weights = dict(self.ai_config.formulaic_alpha_weights or {})
        weighted_sum = 0.0
        total_weight = 0.0
        for name, raw_value in alpha_values.items():
            weight = float(weights.get(name, 0.0))
            if weight == 0.0:
                continue
            weighted_sum += weight * raw_value
            total_weight += abs(weight)

        if total_weight <= 0:
            total_weight = float(len(alpha_values))
            weighted_sum = _sum_window(list(alpha_values.values()), len(alpha_values))

        composite = _clamp(weighted_sum / max(total_weight, 1e-6), -1.0, 1.0)
        features = {
            "alpha101_composite": composite,
            "alpha101_bar_count": float(len(bars)),
            "alpha101_bar_volatility": bar_volatility,
        }
        for name, value in alpha_values.items():
            features[f"alpha101_{name}"] = value
        return composite, features

    def compute(self, state: SymbolState) -> Tuple[float, float, Dict[str, float]]:
        history = list(state.quote_history)
        if len(history) < 10:
            return 0.0, state.config.grid_pct, {}

        prices = [item.last_price for item in history if item.last_price > 0]
        if len(prices) < 10:
            return 0.0, state.config.grid_pct, {}

        last_price = prices[-1]
        ret_5 = last_price / prices[max(0, len(prices) - 6)] - 1.0
        ret_20 = last_price / prices[max(0, len(prices) - 21)] - 1.0
        ret_60 = last_price / prices[max(0, len(prices) - 61)] - 1.0
        log_returns = [
            _safe_log_return(prices[idx - 1], prices[idx])
            for idx in range(1, len(prices))
            if prices[idx - 1] > 0 and prices[idx] > 0
        ]
        short_returns = log_returns[-30:]
        volatility = _stdev(short_returns)
        recent_window = prices[-60:] if len(prices) >= 60 else prices
        floor_price = min(recent_window)
        ceil_price = max(recent_window)
        if ceil_price - floor_price > 1e-9:
            range_pos = (last_price - floor_price) / (ceil_price - floor_price) * 2.0 - 1.0
        else:
            range_pos = 0.0

        quote = history[-1]
        spread_mid = (quote.bid1 + quote.ask1) / 2.0 if quote.bid1 > 0 and quote.ask1 > 0 else max(last_price, 1e-9)
        spread_pct = (quote.ask1 - quote.bid1) / spread_mid if quote.bid1 > 0 and quote.ask1 > 0 else 0.0
        total_book = max(quote.bid_vol1 + quote.ask_vol1, 1.0)
        imbalance = (quote.bid_vol1 - quote.ask_vol1) / total_book

        features = {
            "ret_5": ret_5,
            "ret_20": ret_20,
            "ret_60": ret_60,
            "volatility": volatility,
            "range_pos": range_pos,
            "spread_pct": spread_pct,
            "imbalance": imbalance,
        }

        score = self._heuristic_score(features, state.config.grid_pct)
        formulaic_score, formulaic_features = self._compute_formulaic_alpha_bundle(history)
        if formulaic_features:
            features.update(formulaic_features)
            blend = _clamp(self.ai_config.formulaic_alpha_blend, 0.0, 1.0)
            score = _clamp((1.0 - blend) * score + blend * formulaic_score, -1.0, 1.0)
        grid_multiplier = 1.0 + min(0.8, max(0.0, volatility / max(state.config.grid_pct, 1e-6)) * 0.25)

        if self.hook is not None:
            context = {
                "symbol": state.config.normalized_symbol,
                "features": features,
                "state": {
                    "base_position": state.base_position,
                    "current_position": state.current_position,
                    "can_use_volume": state.can_use_volume,
                    "anchor_price": state.anchor_price,
                    "last_price": last_price,
                },
            }
            try:
                custom = self.hook(context)
                if isinstance(custom, dict):
                    score = float(custom.get("score", score))
                    grid_multiplier = float(custom.get("grid_multiplier", grid_multiplier))
                elif custom is not None:
                    score = float(custom)
            except Exception as exc:
                logging.warning("signal hook failed for %s: %s", state.config.normalized_symbol, exc)

        score = _clamp(score, -1.0, 1.0)
        grid_pct = _clamp(
            state.config.grid_pct * max(0.5, grid_multiplier),
            state.config.min_grid_pct,
            state.config.max_grid_pct,
        )
        return score, grid_pct, features

    @staticmethod
    def _heuristic_score(features: Dict[str, float], grid_pct: float) -> float:
        base = max(grid_pct, 1e-6)
        fast = features["ret_5"] / base
        mid = features["ret_20"] / (base * 1.5)
        slow = features["ret_60"] / (base * 2.0)
        spread_penalty = min(1.0, features["spread_pct"] / max(base, 1e-6))
        raw = (
            0.35 * math.tanh(fast)
            + 0.25 * math.tanh(mid)
            + 0.15 * math.tanh(slow)
            + 0.15 * features["range_pos"]
            + 0.20 * features["imbalance"]
            - 0.10 * spread_penalty
        )
        return _clamp(raw, -1.0, 1.0)


class StrategyCallback(XtQuantTraderCallback):
    def __init__(self, strategy: "LofT0GridStrategy") -> None:
        self.strategy = strategy

    def on_disconnected(self) -> None:
        self.strategy.on_disconnected()

    def on_account_status(self, status: Any) -> None:
        self.strategy.on_account_status(status)

    def on_stock_order(self, order: Any) -> None:
        self.strategy.on_order_update(order)

    def on_stock_trade(self, trade: Any) -> None:
        self.strategy.on_trade_update(trade)

    def on_order_error(self, order_error: Any) -> None:
        self.strategy.on_order_error(order_error)

    def on_cancel_error(self, cancel_error: Any) -> None:
        self.strategy.on_cancel_error(cancel_error)

    def on_order_stock_async_response(self, response: Any) -> None:
        logging.info("async order response received: %s", response)


class LofT0GridStrategy:
    FINAL_ORDER_STATUSES = {53, 54, 56, 57}

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.signal_engine = SignalEngine(config.ai)
        self.states: Dict[str, SymbolState] = {
            item.normalized_symbol: SymbolState(config=item)
            for item in config.symbols
            if item.enabled
        }
        self.symbols = list(self.states.keys())
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.need_sync = True
        self.last_sync_ts = 0.0
        self.last_order_sync_ts = 0.0
        self.quote_sub_ids: List[int] = []
        self.quote_subscription_mode = "none"
        self.available_cash = 0.0
        self.trader: Any = None
        self.account: Any = None
        self.current_trade_day = datetime.now().date()

    def connect(self) -> None:
        if XT_IMPORT_ERROR is not None:
            raise RuntimeError(
                "xtquant is not available. Please run this script in a MiniQMT/XtQuant environment."
            ) from XT_IMPORT_ERROR

        session_id = self.config.account.session_id or int(time.time())
        self.trader = XtQuantTrader(self.config.account.miniqmt_dir, session_id)
        callback = StrategyCallback(self)
        self.trader.register_callback(callback)
        self.trader.start()

        connect_result = self.trader.connect()
        if connect_result != 0:
            raise RuntimeError(f"xt_trader.connect failed: {connect_result}")

        try:
            self.account = StockAccount(
                self.config.account.account_id, self.config.account.account_type
            )
        except TypeError:
            self.account = StockAccount(self.config.account.account_id)

        subscribe_result = self.trader.subscribe(self.account)
        if subscribe_result != 0:
            raise RuntimeError(f"xt_trader.subscribe failed: {subscribe_result}")

        self._subscribe_market_data()

    def _subscribe_market_data(self) -> None:
        self.quote_sub_ids = []
        self.quote_subscription_mode = "none"

        if self.config.runtime.use_whole_quote_subscribe and hasattr(xtdata, "subscribe_whole_quote"):
            try:
                sub_id = xtdata.subscribe_whole_quote(self.symbols)
                if _to_int(sub_id, 0) > 0:
                    self.quote_sub_ids.append(int(sub_id))
                    self.quote_subscription_mode = "whole"
                    logging.info(
                        "subscribed whole quote for %s symbols, seq=%s",
                        len(self.symbols),
                        sub_id,
                    )
                    return
                logging.warning("subscribe_whole_quote returned invalid seq: %s", sub_id)
            except Exception as exc:
                logging.warning("subscribe_whole_quote failed, fallback to subscribe_quote: %s", exc)

        if hasattr(xtdata, "subscribe_quote"):
            for symbol in self.symbols:
                try:
                    sub_id = xtdata.subscribe_quote(symbol, period="tick", count=0)
                except TypeError:
                    try:
                        sub_id = xtdata.subscribe_quote(symbol, "tick", "", "", 0)
                    except Exception as exc:
                        logging.warning("subscribe_quote failed for %s: %s", symbol, exc)
                        continue
                except Exception as exc:
                    logging.warning("subscribe_quote failed for %s: %s", symbol, exc)
                    continue

                if _to_int(sub_id, 0) > 0:
                    self.quote_sub_ids.append(int(sub_id))
                    logging.info("subscribed tick quote for %s, seq=%s", symbol, sub_id)
                else:
                    logging.warning("subscribe_quote returned invalid seq for %s: %s", symbol, sub_id)

        if self.quote_sub_ids:
            self.quote_subscription_mode = "single"
            return

        logging.warning(
            "no realtime quote subscription succeeded; xtdata polling may return empty snapshots"
        )

    def bootstrap(self) -> None:
        self._load_instrument_detail()
        self.sync_account_state(force=True)
        self.refresh_quotes()
        for symbol, state in self.states.items():
            cfg = state.config
            if cfg.base_position is not None:
                state.base_position = _align_volume(cfg.base_position, cfg.lot_size)
            else:
                state.base_position = _align_volume(state.current_position, cfg.lot_size)
            if cfg.anchor_price is not None:
                state.anchor_price = float(cfg.anchor_price)
            elif state.latest_quote is not None:
                state.anchor_price = self._choose_anchor_price(state, state.latest_quote)
            state.dynamic_grid_pct = cfg.grid_pct
            logging.info(
                "[bootstrap] %s base=%s anchor=%.6f tick=%.6f up=%.6f down=%.6f",
                symbol,
                state.base_position,
                state.anchor_price,
                state.price_tick,
                state.up_limit,
                state.down_limit,
            )

    def _load_instrument_detail(self) -> None:
        for symbol, state in self.states.items():
            detail = None
            try:
                detail = xtdata.get_instrument_detail(symbol, False)
            except TypeError:
                detail = xtdata.get_instrument_detail(symbol)
            except Exception as exc:
                logging.warning("get_instrument_detail failed for %s: %s", symbol, exc)
            if not detail:
                state.price_tick = state.config.price_tick or 0.001
                continue

            state.instrument_name = str(_field(detail, "InstrumentName", default=""))
            state.price_tick = _to_float(
                _field(detail, "PriceTick", default=state.config.price_tick or 0.001),
                state.config.price_tick or 0.001,
            )
            state.up_limit = _to_float(_field(detail, "UpStopPrice"), 0.0)
            state.down_limit = _to_float(_field(detail, "DownStopPrice"), 0.0)

    def sync_account_state(self, force: bool = False) -> None:
        now_ts = time.time()
        if not force and now_ts - self.last_sync_ts < self.config.runtime.sync_interval_s and not self.need_sync:
            return

        asset = self.trader.query_stock_asset(self.account)
        self.available_cash = _to_float(
            _field(asset, "cash", "available_cash", "enable_balance", default=0.0), 0.0
        )

        positions = self.trader.query_stock_positions(self.account) or []
        position_map: Dict[str, Any] = {}
        for item in positions:
            code = _field(item, "stock_code", "stockCode", "stock_code1", default="")
            if not code:
                continue
            try:
                code = _normalize_symbol(str(code))
            except ValueError:
                continue
            position_map[code] = item

        for symbol, state in self.states.items():
            position = position_map.get(symbol)
            if position is None:
                state.current_position = 0
                state.can_use_volume = 0
                state.avg_price = 0.0
                state.market_value = 0.0
                continue
            state.current_position = _align_volume(
                _to_int(_field(position, "volume", "total_volume", default=0), 0),
                state.config.lot_size,
            )
            state.can_use_volume = _align_volume(
                _to_int(
                    _field(position, "can_use_volume", "available_volume", default=state.current_position),
                    state.current_position,
                ),
                state.config.lot_size,
            )
            state.avg_price = _to_float(_field(position, "avg_price", "open_price", default=0.0), 0.0)
            state.market_value = _to_float(_field(position, "market_value", default=0.0), 0.0)

        self.last_sync_ts = now_ts
        self.need_sync = False

    def _latest_tick_from_market_data(self, payload: Any) -> Optional[Dict[str, Any]]:
        if payload is None:
            return None
        direct = _record_to_dict(payload)
        if direct is not None:
            return direct

        if isinstance(payload, (list, tuple)):
            if not payload:
                return None
            return self._latest_tick_from_market_data(payload[-1])

        try:
            length = len(payload)  # type: ignore[arg-type]
        except Exception:
            return None
        if length <= 0:
            return None
        try:
            return self._latest_tick_from_market_data(payload[-1])  # type: ignore[index]
        except Exception:
            return None

    def _fetch_tick_snapshot(self) -> Dict[str, Any]:
        if self.quote_subscription_mode == "whole" and hasattr(xtdata, "get_full_tick"):
            return xtdata.get_full_tick(self.symbols) or {}

        if hasattr(xtdata, "get_market_data_ex"):
            try:
                data = xtdata.get_market_data_ex([], self.symbols, period="tick", count=1)
            except TypeError:
                data = xtdata.get_market_data_ex([], self.symbols, "tick", "", "", 1)
            snapshot: Dict[str, Any] = {}
            if isinstance(data, dict):
                for symbol in self.symbols:
                    latest = self._latest_tick_from_market_data(data.get(symbol))
                    if latest:
                        snapshot[symbol] = latest
            if snapshot:
                return snapshot

        if hasattr(xtdata, "get_full_tick"):
            return xtdata.get_full_tick(self.symbols) or {}
        return {}

    def refresh_quotes(self) -> None:
        try:
            snapshot = self._fetch_tick_snapshot()
        except Exception as exc:
            logging.warning("refresh quote snapshot failed: %s", exc)
            return
        if not snapshot:
            return

        now_ts = time.time()
        with self.lock:
            for symbol in self.symbols:
                tick = snapshot.get(symbol)
                if not tick:
                    continue
                quote = self._parse_quote(tick, now_ts)
                if quote is None:
                    continue
                state = self.states[symbol]
                state.latest_quote = quote
                state.quote_history.append(quote)
                if state.anchor_price <= 0:
                    state.anchor_price = self._choose_anchor_price(state, quote)

    def _parse_quote(self, tick: Dict[str, Any], default_ts: float) -> Optional[QuotePoint]:
        last_price = _to_float(_field(tick, "lastPrice", "last_price", "close", default=0.0), 0.0)
        if last_price <= 0:
            return None
        bid1 = _extract_best_level(_field(tick, "bidPrice", "bidPrice1", "bid1", default=[]))
        ask1 = _extract_best_level(_field(tick, "askPrice", "askPrice1", "ask1", default=[]))
        bid_vol1 = _to_float(_extract_best_level(_field(tick, "bidVol", "bidVol1", "bidVolume1", default=[])), 0.0)
        ask_vol1 = _to_float(_extract_best_level(_field(tick, "askVol", "askVol1", "askVolume1", default=[])), 0.0)
        total_volume = _to_float(
            _field(tick, "volume", "Volume", "totalVolume", "tradeVol", default=0.0),
            0.0,
        )
        total_amount = _to_float(
            _field(tick, "amount", "Amount", "turnover", "tradeAmount", default=0.0),
            0.0,
        )
        ts = _parse_xt_timestamp(_field(tick, "time", "timetag", "timeTag", default=default_ts * 1000), default_ts)
        return QuotePoint(
            ts=ts,
            last_price=last_price,
            bid1=bid1,
            ask1=ask1,
            bid_vol1=bid_vol1,
            ask_vol1=ask_vol1,
            last_close=_to_float(_field(tick, "lastClose", "last_close", "preClose", "pre_close", default=last_price), last_price),
            total_volume=total_volume,
            total_amount=total_amount,
        )

    def _choose_anchor_price(self, state: SymbolState, quote: QuotePoint) -> float:
        mode = state.config.anchor_mode.lower()
        if mode == "last_close" and quote.last_close > 0:
            return quote.last_close
        if mode == "last_price" and quote.last_price > 0:
            return quote.last_price
        return quote.last_close if quote.last_close > 0 else quote.last_price

    def on_disconnected(self) -> None:
        logging.error("xttrader disconnected")
        self.need_sync = True

    def on_account_status(self, status: Any) -> None:
        status_code = _field(status, "status", "account_status", default="")
        account_id = _field(status, "account_id", default=self.config.account.account_id)
        account_type = _field(status, "account_type", default=self.config.account.account_type)
        logging.info(
            "[account-status] account=%s type=%s status=%s msg=%s",
            account_id,
            account_type,
            status_code,
            _to_text(_field(status, "msg", "status_msg", default="")),
        )
        self.need_sync = True

    def on_order_update(self, order: Any) -> None:
        symbol = _field(order, "stock_code", "stockCode", "stock_code1", default="")
        if not symbol:
            return
        try:
            symbol = _normalize_symbol(str(symbol))
        except ValueError:
            return
        if symbol not in self.states:
            return
        order_id = _to_int(_field(order, "order_id", "orderID", default=0), 0)
        status = _to_int(_field(order, "order_status", default=0), 0)
        status_msg = str(_field(order, "status_msg", "error_msg", default=""))
        traded_volume = _to_int(_field(order, "traded_volume", "filled_volume", default=0), 0)

        with self.lock:
            state = self.states[symbol]
            if state.pending_order and state.pending_order.order_id == order_id:
                state.pending_order.filled_volume = max(state.pending_order.filled_volume, traded_volume)
                if status in self.FINAL_ORDER_STATUSES or traded_volume >= state.pending_order.volume:
                    logging.info(
                        "[order-final] %s order=%s status=%s msg=%s",
                        symbol,
                        order_id,
                        status,
                        status_msg,
                    )
                    state.pending_order = None
                    self.need_sync = True
                else:
                    logging.info(
                        "[order-update] %s order=%s status=%s traded=%s msg=%s",
                        symbol,
                        order_id,
                        status,
                        traded_volume,
                        status_msg,
                    )

    def on_trade_update(self, trade: Any) -> None:
        symbol = _field(trade, "stock_code", "stockCode", "stock_code1", default="")
        if not symbol:
            return
        try:
            symbol = _normalize_symbol(str(symbol))
        except ValueError:
            return
        if symbol not in self.states:
            return

        order_id = _to_int(_field(trade, "order_id", "orderID", default=0), 0)
        traded_volume = _to_int(_field(trade, "traded_volume", "business_amount", default=0), 0)
        traded_price = _to_float(_field(trade, "traded_price", "business_price", default=0.0), 0.0)
        order_type = _to_int(_field(trade, "order_type", "offset_flag", default=0), 0)

        with self.lock:
            state = self.states[symbol]
            if order_type == getattr(xtconstant, "STOCK_BUY", 23):
                state.daily_buy_amount += traded_volume * traded_price
            elif order_type == getattr(xtconstant, "STOCK_SELL", 24):
                state.daily_sell_amount += traded_volume * traded_price
            if state.pending_order and state.pending_order.order_id == order_id:
                state.pending_order.filled_volume += traded_volume
                if state.pending_order.filled_volume >= state.pending_order.volume:
                    state.pending_order = None
            self.need_sync = True
        logging.info(
            "[trade] %s order=%s volume=%s price=%.6f type=%s",
            symbol,
            order_id,
            traded_volume,
            traded_price,
            order_type,
        )

    def on_order_error(self, order_error: Any) -> None:
        order_id = _to_int(_field(order_error, "order_id", default=0), 0)
        error_id = _to_int(_field(order_error, "error_id", default=0), 0)
        error_msg = str(_field(order_error, "error_msg", default=""))
        logging.error("[order-error] order=%s error=%s msg=%s", order_id, error_id, error_msg)
        self._clear_pending_by_order_id(order_id)

    def on_cancel_error(self, cancel_error: Any) -> None:
        order_id = _to_int(_field(cancel_error, "order_id", default=0), 0)
        error_id = _to_int(_field(cancel_error, "error_id", default=0), 0)
        error_msg = str(_field(cancel_error, "error_msg", default=""))
        logging.error("[cancel-error] order=%s error=%s msg=%s", order_id, error_id, error_msg)

    def _clear_pending_by_order_id(self, order_id: int) -> None:
        if order_id <= 0:
            return
        with self.lock:
            for state in self.states.values():
                if state.pending_order and state.pending_order.order_id == order_id:
                    state.pending_order = None

    def sync_orders(self) -> None:
        try:
            all_orders = self.trader.query_stock_orders(self.account, False) or []
            cancelable_orders = self.trader.query_stock_orders(self.account, True) or []
        except Exception as exc:
            logging.warning("query_stock_orders failed: %s", exc)
            return

        cancelable_ids = {
            _to_int(_field(order, "order_id", "orderID", default=0), 0)
            for order in cancelable_orders
        }
        order_map = {
            _to_int(_field(order, "order_id", "orderID", default=0), 0): order
            for order in all_orders
        }

        now_ts = time.time()
        with self.lock:
            for symbol, state in self.states.items():
                pending = state.pending_order
                if pending is None:
                    continue
                order = order_map.get(pending.order_id)
                if pending.order_id in cancelable_ids:
                    if now_ts - pending.created_ts >= self.config.runtime.order_timeout_s:
                        self._cancel_order(pending.order_id, symbol)
                    continue
                if order is not None:
                    status = _to_int(_field(order, "order_status", default=0), 0)
                    traded_volume = _to_int(_field(order, "traded_volume", default=0), 0)
                    if status in self.FINAL_ORDER_STATUSES or traded_volume >= pending.volume:
                        state.pending_order = None
                        self.need_sync = True
                        continue
                if now_ts - pending.created_ts >= self.config.runtime.pending_clear_grace_s:
                    logging.info("[pending-clear] %s order=%s", symbol, pending.order_id)
                    state.pending_order = None
                    self.need_sync = True

    def _cancel_order(self, order_id: int, symbol: str) -> None:
        try:
            result = self.trader.cancel_order_stock(self.account, order_id)
        except Exception as exc:
            logging.warning("cancel_order_stock failed for %s order=%s: %s", symbol, order_id, exc)
            return
        logging.info("[cancel] %s order=%s result=%s", symbol, order_id, result)

    def run(self) -> None:
        self.connect()
        self.bootstrap()
        self._install_signal_handlers()

        while not self.stop_event.is_set():
            now = datetime.now()
            self._roll_trade_day(now)
            if _in_a_share_session(now):
                self.refresh_quotes()
                self.sync_account_state()
                self.sync_orders()
                self._process_symbols(now)
            else:
                self.sync_account_state()
            time.sleep(self.config.runtime.poll_interval_s)

        self.shutdown()

    def _install_signal_handlers(self) -> None:
        def _handle_stop(signum: int, frame: Any) -> None:
            logging.info("received signal %s, stopping strategy", signum)
            self.stop_event.set()

        try:
            signal.signal(signal.SIGINT, _handle_stop)
            signal.signal(signal.SIGTERM, _handle_stop)
        except Exception as exc:
            logging.debug("skip signal handler install: %s", exc)

    def _roll_trade_day(self, now: datetime) -> None:
        if now.date() == self.current_trade_day:
            return
        self.current_trade_day = now.date()
        self.need_sync = True
        for state in self.states.values():
            state.daily_buy_amount = 0.0
            state.daily_sell_amount = 0.0
            state.daily_order_count = 0
            state.pending_order = None
            state.quote_history.clear()
            state.latest_quote = None
            if state.config.base_position is None:
                state.base_position = _align_volume(state.current_position, state.config.lot_size)
            if state.config.anchor_price is not None:
                state.anchor_price = float(state.config.anchor_price)
            else:
                state.anchor_price = 0.0

    def _process_symbols(self, now: datetime) -> None:
        for symbol, state in self.states.items():
            if state.latest_quote is None:
                continue
            if state.pending_order is not None:
                continue
            if now.timestamp() - state.last_order_ts < state.config.cooldown_s:
                continue

            if now.time() >= self.config.runtime.close_back_to_base_clock():
                if state.config.close_back_to_base:
                    self._back_to_base(symbol, state)
                continue

            if now.time() >= self.config.runtime.stop_new_order_clock():
                continue

            self._maybe_place_grid_order(symbol, state)

    def _back_to_base(self, symbol: str, state: SymbolState) -> None:
        diff = state.current_position - state.base_position
        if abs(diff) < state.config.lot_size:
            return
        quote = state.latest_quote
        if quote is None:
            return
        if diff > 0:
            volume = min(diff, state.config.max_order_volume)
            volume = _align_volume(min(volume, state.can_use_volume), state.config.lot_size)
            if volume >= state.config.lot_size and self._can_sell(state, volume):
                self._submit_order(symbol, state, "SELL", volume, "close_to_base")
        else:
            volume = min(-diff, state.config.max_order_volume)
            volume = _align_volume(volume, state.config.lot_size)
            available_cash = max(0.0, self.available_cash - state.config.cash_reserve)
            if volume >= state.config.lot_size and available_cash >= volume * quote.last_price:
                self._submit_order(symbol, state, "BUY", volume, "close_to_base")

    def _maybe_place_grid_order(self, symbol: str, state: SymbolState) -> None:
        quote = state.latest_quote
        if quote is None or quote.last_price <= 0 or state.anchor_price <= 0:
            return

        if quote.bid1 > 0 and quote.ask1 > 0:
            spread_pct = (quote.ask1 - quote.bid1) / max(quote.last_price, 1e-9)
            if spread_pct > state.config.max_spread_pct:
                return

        now_ts = time.time()
        score = state.last_signal_score
        grid_pct = state.dynamic_grid_pct or state.config.grid_pct
        if state.config.use_adaptive_signal:
            if now_ts - state.last_signal_ts >= self.config.runtime.signal_refresh_s or state.last_signal_ts <= 0:
                score, grid_pct, _ = self.signal_engine.compute(state)
                state.last_signal_score = score
                state.dynamic_grid_pct = grid_pct
                state.last_signal_ts = now_ts
        else:
            score = 0.0
            grid_pct = state.config.grid_pct

        actual_steps = state.actual_steps()
        target_steps = self._calc_target_steps(state, quote.last_price, grid_pct, score)
        step_diff = target_steps - actual_steps
        if step_diff == 0:
            return

        unit = state.unit_volume()
        if step_diff > 0:
            volume = min(step_diff * unit, state.config.max_order_volume)
            volume = _align_volume(volume, state.config.lot_size)
            if volume >= state.config.lot_size and self._can_buy(state, volume, quote.last_price):
                self._submit_order(symbol, state, "BUY", volume, f"grid_buy|score={score:.3f}")
        else:
            volume = min(abs(step_diff) * unit, state.config.max_order_volume)
            volume = _align_volume(min(volume, state.can_use_volume), state.config.lot_size)
            if volume >= state.config.lot_size and self._can_sell(state, volume):
                self._submit_order(symbol, state, "SELL", volume, f"grid_sell|score={score:.3f}")

    def _calc_target_steps(self, state: SymbolState, price: float, grid_pct: float, score: float) -> int:
        if price <= 0 or state.anchor_price <= 0 or grid_pct <= 0:
            return 0
        ratio = price / state.anchor_price
        raw = math.log(ratio) / math.log(1.0 + grid_pct)
        grid_index = math.floor(raw) if raw >= 0 else math.ceil(raw)
        bias_steps = int(round(score * state.config.signal_bias_steps))
        target_steps = -grid_index + bias_steps

        long_limit = state.long_steps_limit()
        short_limit = state.short_steps_limit()
        target_steps = max(-short_limit, min(long_limit, target_steps))

        if price <= state.anchor_price * (1.0 - state.config.stop_loss_pct):
            target_steps = min(target_steps, max(0, state.actual_steps()))
        return target_steps

    def _can_buy(self, state: SymbolState, volume: int, price: float) -> bool:
        projected_position = state.current_position + volume
        extra_value = max(0, projected_position - state.base_position) * price
        available_cash = max(0.0, self.available_cash - state.config.cash_reserve)

        if state.config.max_position_value is not None and projected_position * price > state.config.max_position_value:
            return False
        if state.config.max_daily_buy_amount is not None and state.daily_buy_amount + volume * price > state.config.max_daily_buy_amount:
            return False
        if available_cash < volume * price:
            return False
        if extra_value > state.config.max_intraday_long_volume * price:
            return False
        if state.daily_order_count >= state.config.max_orders_per_day:
            return False
        return True

    def _can_sell(self, state: SymbolState, volume: int) -> bool:
        if state.can_use_volume < volume:
            return False
        if state.daily_order_count >= state.config.max_orders_per_day:
            return False
        projected_short = max(0, state.base_position - (state.current_position - volume))
        if projected_short > state.config.max_intraday_short_volume:
            return False
        return True

    def _submit_order(self, symbol: str, state: SymbolState, side: str, volume: int, reason: str) -> None:
        quote = state.latest_quote
        if quote is None:
            return
        order_type = getattr(xtconstant, "STOCK_BUY", 23) if side == "BUY" else getattr(xtconstant, "STOCK_SELL", 24)
        price_type = getattr(xtconstant, "FIX_PRICE", 11)
        raw_price = self._calc_order_price(state, quote, side)
        remark = f"{self.config.runtime.remark_prefix}|{reason}"

        if self.config.runtime.dry_run:
            logging.info(
                "[dry-run] %s side=%s volume=%s price=%.6f remark=%s",
                symbol,
                side,
                volume,
                raw_price,
                remark,
            )
            state.last_order_ts = time.time()
            state.daily_order_count += 1
            return

        try:
            order_id = self.trader.order_stock(
                self.account,
                symbol,
                order_type,
                volume,
                price_type,
                raw_price,
                self.config.runtime.strategy_name,
                remark,
            )
        except Exception as exc:
            logging.error("[order-exception] %s side=%s volume=%s error=%s", symbol, side, volume, exc)
            return

        if order_id <= 0:
            logging.error("[order-failed] %s side=%s volume=%s order_id=%s", symbol, side, volume, order_id)
            return

        state.pending_order = PendingOrder(
            order_id=order_id,
            side=side,
            volume=volume,
            price=raw_price,
            created_ts=time.time(),
        )
        state.last_order_ts = time.time()
        state.daily_order_count += 1
        logging.info(
            "[order] %s side=%s volume=%s price=%.6f order_id=%s remark=%s",
            symbol,
            side,
            volume,
            raw_price,
            order_id,
            remark,
        )

    def _calc_order_price(self, state: SymbolState, quote: QuotePoint, side: str) -> float:
        tick_size = state.price_tick or state.config.price_tick or 0.001
        if side == "BUY":
            base = quote.ask1 if quote.ask1 > 0 else quote.last_price
            price = base + state.config.slippage_ticks * tick_size
            if state.up_limit > 0:
                price = min(price, state.up_limit)
        else:
            base = quote.bid1 if quote.bid1 > 0 else quote.last_price
            price = base - state.config.slippage_ticks * tick_size
            if state.down_limit > 0:
                price = max(price, state.down_limit)
        return _align_price(price, tick_size, side)

    def shutdown(self) -> None:
        if hasattr(xtdata, "unsubscribe_quote"):
            for sub_id in self.quote_sub_ids:
                try:
                    xtdata.unsubscribe_quote(sub_id)
                except Exception:
                    pass
        self.quote_sub_ids = []
        self.quote_subscription_mode = "none"
        if self.trader is not None:
            try:
                self.trader.unsubscribe(self.account)
            except Exception:
                pass
            try:
                self.trader.stop()
            except Exception:
                pass


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LOF T+0 grid strategy for XtQuant/MiniQMT")
    parser.add_argument("--config", help="Path to json config")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.config:
        config_path = Path(args.config).expanduser().resolve()
        config = StrategyConfig.from_dict(_load_json(config_path))
    else:
        config = build_default_config()
    setup_logging(config.runtime.log_level)

    strategy = LofT0GridStrategy(config)
    try:
        strategy.run()
    except KeyboardInterrupt:
        logging.info("stopped by keyboard interrupt")
        strategy.stop_event.set()
        strategy.shutdown()
    return 0


class StrategyFileController:
    def __init__(self) -> None:
        self.strategy: Optional[LofT0GridStrategy] = None
        self.thread: Optional[threading.Thread] = None
        self.error: Optional[BaseException] = None
        self.lock = threading.Lock()

    def start(self, config: Optional[StrategyConfig] = None) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            if config is None:
                config = build_default_config()
            setup_logging(config.runtime.log_level)
            self.strategy = LofT0GridStrategy(config)
            self.error = None
            self.thread = threading.Thread(
                target=self._thread_main,
                name="lof_t0_grid_strategy",
                daemon=True,
            )
            self.thread.start()

    def _thread_main(self) -> None:
        assert self.strategy is not None
        try:
            self.strategy.run()
        except BaseException as exc:
            self.error = exc
            logging.exception("strategy thread exited with error")

    def poll(self) -> None:
        if self.error is not None:
            raise RuntimeError(f"strategy thread failed: {self.error}") from self.error

    def stop(self) -> None:
        with self.lock:
            if self.strategy is None:
                return
            self.strategy.stop_event.set()
            self.strategy.shutdown()
            self.strategy = None


_STRATEGY_FILE_CONTROLLER = StrategyFileController()


def init(C: Any) -> None:
    _ = C
    _STRATEGY_FILE_CONTROLLER.start()


def after_init(C: Any) -> None:
    _ = C


def handlebar(C: Any) -> None:
    _ = C
    _STRATEGY_FILE_CONTROLLER.poll()


def stop(C: Any) -> None:
    _ = C
    _STRATEGY_FILE_CONTROLLER.stop()


def run_with_qmttools() -> None:
    from xtquant.qmttools import run_strategy_file

    param = {
        "stock_code": DEFAULT_DRIVER_STOCK,
        "period": DEFAULT_DRIVER_PERIOD,
        "start_time": "",
        "end_time": "",
        "trade_mode": "simulation",
        "quote_mode": "realtime",
    }
    run_strategy_file(
        str(Path(__file__).resolve()),
        param=param,
    )


if __name__ == "__main__":
    sys.exit(main())
