#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
161129 LOF 激进版策略回测
优化参数以提高收益
"""

import json
import math
import random
from datetime import datetime, timedelta
from typing import Dict, List

# 激进版策略参数
AGGRESSIVE_CONFIG = {
    "symbol": "161129.SZ",
    "initial_cash": 300000,
    "base_position": 8000,  # 提高基础仓位
    "order_volume": 300,    # 提高单笔交易量
    "lot_size": 100,
    "grid_pct": 0.012,      # 扩大网格间距到 1.2%
    "min_grid_pct": 0.008,
    "max_grid_pct": 0.025,
    "max_intraday_long_volume": 10000,   # 大幅提高日内做多上限
    "max_intraday_short_volume": 10000,  # 大幅提高日内做空上限
    "max_order_volume": 1000,  # 提高单笔订单上限
    "max_daily_buy_amount": 300000,  # 提高日买入上限
    "max_orders_per_day": 150,  # 提高日交易次数
    "cooldown_s": 1.0,  # 缩短冷却时间
    "signal_bias_steps": 5,  # 增强信号影响力
    "stop_loss_pct": 0.02,  # 收紧止损
    "close_back_to_base": True,
    "cash_reserve": 10000,  # 降低现金储备，提高资金利用率
}

# 市场参数（保持不变）
MARKET_PARAMS = {
    "base_price": 1.150,
    "daily_volatility": 0.018,
    "intraday_range": 0.025,
    "drift": -0.0002,
    "mean_reversion": 0.03,
    "monthly_trends": [
        (1, -0.02, 1.2),
        (2, 0.01, 0.9),
        (3, -0.01, 1.1),
        (4, 0.03, 1.0),
    ],
    "event_risks": [
        {"probability": 0.05, "impact": -0.03, "duration": 3},
        {"probability": 0.03, "impact": -0.02, "duration": 2},
        {"probability": 0.08, "impact": 0.015, "duration": 1},
    ],
}


class AggressiveBacktest:
    def __init__(self, config: Dict, market_params: Dict):
        self.config = config
        self.market_params = market_params
        self.reset()
    
    def reset(self):
        self.cash = self.config["initial_cash"]
        self.position = self.config["base_position"]
        self.base_position = self.config["base_position"]
        self.anchor_price = self.market_params["base_price"]
        self.daily_buy_amount = 0
        self.daily_order_count = 0
        self.trades = []
        self.equity_curve = []
        self.max_drawdown = 0
        self.peak_equity = self.config["initial_cash"] + self.config["base_position"] * self.market_params["base_price"]
    
    def generate_price_path(self, days: int) -> List[Dict]:
        random.seed(42)
        prices = []
        current_price = self.market_params["base_price"]
        start_date = datetime(2025, 10, 13)
        
        for day in range(days):
            date = start_date + timedelta(days=day)
            month = date.month
            
            month_adjust = (0, 1.0)
            for m, trend, vol_adj in self.market_params["monthly_trends"]:
                if month == m or (month == 12 and m == 1):
                    month_adjust = (trend, vol_adj)
                    break
            
            daily_return = random.gauss(self.market_params["drift"], self.market_params["daily_volatility"])
            daily_return += month_adjust[0]
            vol_multiplier = month_adjust[1]
            
            for event in self.market_params["event_risks"]:
                if random.random() < event["probability"]:
                    daily_return += event["impact"]
                    for j in range(event["duration"] - 1):
                        if day + j < days:
                            daily_return += event["impact"] * 0.5
            
            if current_price > self.market_params["base_price"] * 1.1:
                daily_return -= self.market_params["mean_reversion"]
            elif current_price < self.market_params["base_price"] * 0.9:
                daily_return += self.market_params["mean_reversion"]
            
            current_price *= (1 + daily_return * vol_multiplier)
            current_price = max(current_price, 0.5)
            
            intraday_high = current_price * (1 + random.uniform(0, self.market_params["intraday_range"] * vol_multiplier))
            intraday_low = current_price * (1 - random.uniform(0, self.market_params["intraday_range"] * vol_multiplier))
            
            prices.append({
                "date": date,
                "open": current_price * random.uniform(0.995, 1.005),
                "high": intraday_high,
                "low": intraday_low,
                "close": current_price,
                "daily_return": daily_return,
            })
        
        return prices
    
    def generate_intraday_ticks(self, day_data: Dict, ticks_per_day: int = 240) -> List[Dict]:
        ticks = []
        base_ts = day_data["date"].replace(hour=9, minute=30).timestamp()
        current_price = day_data["open"]
        tick_vol = (day_data["high"] - day_data["low"]) / math.sqrt(ticks_per_day)
        
        for i in range(ticks_per_day):
            hour = 9 + (i * 2.5) // 60
            if hour >= 11 and hour < 13:
                continue
            if hour >= 15:
                break
            
            current_price += random.gauss(0, tick_vol)
            current_price = max(min(current_price, day_data["high"] * 1.01), day_data["low"] * 0.99)
            
            spread = current_price * 0.003  # 略高价差
            bid1 = current_price - spread / 2
            ask1 = current_price + spread / 2
            
            ticks.append({
                "ts": base_ts + i * 60,
                "last_price": current_price,
                "bid1": bid1,
                "ask1": ask1,
                "bid_vol1": random.randint(100, 1000),
                "ask_vol1": random.randint(100, 1000),
                "last_close": day_data["close"],
            })
        
        return ticks
    
    def calc_target_steps(self, price: float, grid_pct: float, score: float) -> int:
        if price <= 0 or self.anchor_price <= 0 or grid_pct <= 0:
            return 0
        
        ratio = price / self.anchor_price
        raw = math.log(ratio) / math.log(1 + grid_pct)
        grid_index = math.floor(raw) if raw >= 0 else math.ceil(raw)
        bias_steps = int(round(score * self.config["signal_bias_steps"]))
        target_steps = -grid_index + bias_steps
        
        long_limit = self.config["max_intraday_long_volume"] // self.config["order_volume"]
        short_limit = self.config["max_intraday_short_volume"] // self.config["order_volume"]
        target_steps = max(-short_limit, min(long_limit, target_steps))
        
        if price <= self.anchor_price * (1 - self.config["stop_loss_pct"]):
            target_steps = min(target_steps, max(0, (self.position - self.base_position) // self.config["order_volume"]))
        
        return target_steps
    
    def run_backtest(self, days: int = 126) -> Dict:
        self.reset()
        prices = self.generate_price_path(days)
        total_trades = 0
        total_turnover = 0
        daily_results = []
        
        for day_idx, day_data in enumerate(prices):
            self.daily_buy_amount = 0
            self.daily_order_count = 0
            day_trades = []
            
            if day_idx > 0:
                self.anchor_price = prices[day_idx - 1]["close"]
            
            ticks = self.generate_intraday_ticks(day_data)
            last_order_ts = 0
            current_steps = 0
            
            for tick in ticks:
                price = tick["last_price"]
                
                if tick["ts"] - last_order_ts < self.config["cooldown_s"]:
                    continue
                
                # 更激进的收盘回补逻辑
                hour = datetime.fromtimestamp(tick["ts"]).hour
                if hour >= 14 and hour < 15:
                    diff = self.position - self.base_position
                    if abs(diff) >= self.config["lot_size"]:
                        if diff > 0 and self.daily_order_count < self.config["max_orders_per_day"]:
                            volume = min(abs(diff), self.config["max_order_volume"])
                            volume = (volume // self.config["lot_size"]) * self.config["lot_size"]
                            if volume >= self.config["lot_size"]:
                                self.cash += volume * price
                                self.position -= volume
                                day_trades.append({"side": "SELL", "price": price, "volume": volume, "type": "close_back"})
                                total_trades += 1
                                total_turnover += volume * price
                                last_order_ts = tick["ts"]
                        continue
                
                if hour >= 14 and datetime.fromtimestamp(tick["ts"]).minute >= 55:
                    continue
                
                # 更积极的信号
                ret_5 = (price - self.anchor_price) / self.anchor_price if self.anchor_price > 0 else 0
                score = max(-1, min(1, -ret_5 * 15))  # 更敏感的均值回归信号
                
                grid_pct = max(self.config["min_grid_pct"], 
                              min(self.config["max_grid_pct"], self.config["grid_pct"]))
                target_steps = self.calc_target_steps(price, grid_pct, score)
                
                step_diff = target_steps - current_steps
                if abs(step_diff) < 1:
                    continue
                
                unit = self.config["order_volume"]
                
                if step_diff > 0:  # 买入
                    volume = min(step_diff * unit, self.config["max_order_volume"])
                    volume = (volume // self.config["lot_size"]) * self.config["lot_size"]
                    
                    if (volume >= self.config["lot_size"] and 
                        self.daily_buy_amount + volume * price < self.config["max_daily_buy_amount"] and
                        self.daily_order_count < self.config["max_orders_per_day"] and
                        self.cash >= volume * price + self.config["cash_reserve"]):
                        
                        self.cash -= volume * price
                        self.position += volume
                        self.daily_buy_amount += volume * price
                        day_trades.append({"side": "BUY", "price": price, "volume": volume, "type": "grid_buy"})
                        total_trades += 1
                        total_turnover += volume * price
                        current_steps += step_diff
                        last_order_ts = tick["ts"]
                
                elif step_diff < 0:  # 卖出
                    volume = min(abs(step_diff) * unit, self.config["max_order_volume"])
                    volume = (volume // self.config["lot_size"]) * self.config["lot_size"]
                    
                    if (volume >= self.config["lot_size"] and 
                        self.position >= volume and
                        self.daily_order_count < self.config["max_orders_per_day"]):
                        
                        self.cash += volume * price
                        self.position -= volume
                        day_trades.append({"side": "SELL", "price": price, "volume": volume, "type": "grid_sell"})
                        total_trades += 1
                        total_turnover += volume * price
                        current_steps += step_diff
                        last_order_ts = tick["ts"]
            
            end_price = day_data["close"]
            equity = self.cash + self.position * end_price
            
            if equity > self.peak_equity:
                self.peak_equity = equity
            
            drawdown = (self.peak_equity - equity) / self.peak_equity
            if drawdown > self.max_drawdown:
                self.max_drawdown = drawdown
            
            self.trades.extend(day_trades)
            self.equity_curve.append(equity)
            
            daily_results.append({
                "date": day_data["date"],
                "close": end_price,
                "equity": equity,
                "position": self.position,
                "cash": self.cash,
                "trades": len(day_trades),
                "drawdown": drawdown,
            })
        
        # 计算绩效
        initial_equity = self.config["initial_cash"] + self.config["base_position"] * self.market_params["base_price"]
        final_equity = self.cash + self.position * prices[-1]["close"]
        total_return = (final_equity - initial_equity) / initial_equity
        
        daily_returns = []
        for i in range(1, len(self.equity_curve)):
            daily_returns.append((self.equity_curve[i] - self.equity_curve[i-1]) / self.equity_curve[i-1])
        
        avg_return = sum(daily_returns) / len(daily_returns) if daily_returns else 0
        std_return = math.sqrt(sum((r - avg_return)**2 for r in daily_returns) / len(daily_returns)) if len(daily_returns) > 1 else 1
        sharpe = (avg_return / std_return) * math.sqrt(252) if std_return > 0 else 0
        
        profitable_days = sum(1 for d in daily_results if d["drawdown"] < 0.02)
        win_rate = profitable_days / len(daily_results) if daily_results else 0
        
        return {
            "summary": {
                "回测天数": days,
                "起始日期": prices[0]["date"].strftime("%Y-%m-%d"),
                "结束日期": prices[-1]["date"].strftime("%Y-%m-%d"),
                "初始资金": f"¥{initial_equity:,.2f}",
                "结束权益": f"¥{final_equity:,.2f}",
                "总收益率": f"{total_return*100:.2f}%",
                "年化收益率": f"{((1 + total_return) ** (252/days) - 1)*100:.2f}%",
                "夏普比率": f"{sharpe:.2f}",
                "最大回撤": f"{self.max_drawdown*100:.2f}%",
                "总交易次数": total_trades,
                "总成交额": f"¥{total_turnover:,.2f}",
                "日均交易": f"{total_trades/days:.1f}次",
                "胜率": f"{win_rate*100:.1f}%",
                "最终持仓": f"{self.position}份",
                "最终现金": f"¥{self.cash:,.2f}",
            },
            "monthly_performance": self._calc_monthly_performance(daily_results),
            "risk_metrics": self._calc_risk_metrics(daily_results, initial_equity),
        }
    
    def _calc_monthly_performance(self, daily_results: List[Dict]) -> List[Dict]:
        monthly = {}
        for d in daily_results:
            month_key = d["date"].strftime("%Y-%m")
            if month_key not in monthly:
                monthly[month_key] = {"start": d["equity"], "end": d["equity"], "trades": 0, "days": 0}
            monthly[month_key]["end"] = d["equity"]
            monthly[month_key]["trades"] += d["trades"]
            monthly[month_key]["days"] += 1
        
        result = []
        for month, data in sorted(monthly.items()):
            result.append({
                "月份": month,
                "收益率": f"{(data['end'] - data['start']) / data['start'] * 100:.2f}%",
                "交易次数": data["trades"],
                "交易天数": data["days"],
            })
        return result
    
    def _calc_risk_metrics(self, daily_results: List[Dict], initial_equity: float) -> Dict:
        equities = [d["equity"] for d in daily_results]
        max_equity = max(equities)
        min_equity = min(equities)
        
        daily_returns = []
        for i in range(1, len(equities)):
            daily_returns.append((equities[i] - equities[i-1]) / equities[i-1])
        
        daily_returns.sort()
        var_95 = -daily_returns[int(len(daily_returns) * 0.05)] if len(daily_returns) >= 20 else 0
        
        return {
            "最高权益": f"¥{max_equity:,.2f}",
            "最低权益": f"¥{min_equity:,.2f}",
            "权益波动率": f"{sum(abs(r) for r in daily_returns) / len(daily_returns) * 100:.2f}%" if daily_returns else "N/A",
            "VaR(95%)": f"{var_95*100:.2f}%",
            "盈亏比": f"{max(1, sum(1 for r in daily_returns if r > 0) / max(1, sum(1 for r in daily_returns if r <= 0))):.2f}",
        }


def compare_strategies():
    """对比保守版和激进版策略"""
    print("=" * 80)
    print("161129 LOF 策略优化对比分析（近 6 个月）")
    print("=" * 80)
    print()
    
    # 保守版（原策略）
    conservative_config = {
        "symbol": "161129.SZ",
        "initial_cash": 300000,
        "base_position": 5000,
        "order_volume": 200,
        "lot_size": 100,
        "grid_pct": 0.008,
        "min_grid_pct": 0.005,
        "max_grid_pct": 0.020,
        "max_intraday_long_volume": 5000,
        "max_intraday_short_volume": 5000,
        "max_order_volume": 500,
        "max_daily_buy_amount": 150000,
        "max_orders_per_day": 80,
        "cooldown_s": 2.0,
        "signal_bias_steps": 3,
        "stop_loss_pct": 0.025,
        "close_back_to_base": True,
        "cash_reserve": 20000,
    }
    
    conservative = AggressiveBacktest(conservative_config, MARKET_PARAMS)
    conservative_result = conservative.run_backtest(days=126)
    
    # 激进版（新策略）
    aggressive = AggressiveBacktest(AGGRESSIVE_CONFIG, MARKET_PARAMS)
    aggressive_result = aggressive.run_backtest(days=126)
    
    # 对比展示
    print("【策略对比】")
    print("-" * 80)
    print(f"{'指标':<20} {'保守版':<25} {'激进版':<25} {'变化':<15}")
    print("-" * 80)
    
    metrics = [
        ("总收益率", "总收益率", "总收益率"),
        ("年化收益率", "年化收益率", "年化收益率"),
        ("夏普比率", "夏普比率", "夏普比率"),
        ("最大回撤", "最大回撤", "最大回撤"),
        ("总交易次数", "总交易次数", "总交易次数"),
        ("日均交易", "日均交易", "日均交易"),
        ("胜率", "胜率", "胜率"),
        ("总成交额", "总成交额", "总成交额"),
    ]
    
    for name, key, key in metrics:
        cons_val = conservative_result["summary"][key]
        agg_val = aggressive_result["summary"][key]
        
        # 计算变化
        try:
            cons_num = float(cons_val.replace("¥", "").replace(",", "").replace("%", ""))
            agg_num = float(agg_val.replace("¥", "").replace(",", "").replace("%", ""))
            if cons_num != 0:
                change = (agg_num - cons_num) / abs(cons_num) * 100
                change_str = f"+{change:.1f}%" if change > 0 else f"{change:.1f}%"
            else:
                change_str = "N/A"
        except:
            change_str = "N/A"
        
        print(f"{name:<20} {cons_val:<25} {agg_val:<25} {change_str:<15}")
    
    print()
    print("【参数对比】")
    print("-" * 80)
    print(f"{'参数':<25} {'保守版':<20} {'激进版':<20}")
    print("-" * 80)
    
    params = [
        ("base_position", "基础仓位"),
        ("order_volume", "单笔交易量"),
        ("grid_pct", "网格间距"),
        ("max_intraday_long_volume", "日内做多上限"),
        ("max_order_volume", "单笔订单上限"),
        ("max_daily_buy_amount", "日买入上限"),
        ("max_orders_per_day", "日交易次数"),
        ("cooldown_s", "冷却时间"),
        ("signal_bias_steps", "信号步数"),
        ("cash_reserve", "现金储备"),
    ]
    
    for key, name in params:
        cons_val = conservative_config.get(key, "N/A")
        agg_val = AGGRESSIVE_CONFIG.get(key, "N/A")
        print(f"{name:<25} {cons_val:<20} {agg_val:<20}")
    
    print()
    print("=" * 80)
    print("【激进版策略详细结果】")
    print("=" * 80)
    print()
    
    print("【回测概要】")
    print("-" * 50)
    for key, value in aggressive_result["summary"].items():
        print(f"  {key}: {value}")
    
    print()
    print("【月度表现】")
    print("-" * 50)
    print(f"  {'月份':<10} {'收益率':<12} {'交易次数':<10} {'交易天数':<10}")
    for m in aggressive_result["monthly_performance"]:
        print(f"  {m['月份']:<10} {m['收益率']:<12} {m['交易次数']:<10} {m['交易天数']:<10}")
    
    print()
    print("【风险指标】")
    print("-" * 50)
    for key, value in aggressive_result["risk_metrics"].items():
        print(f"  {key}: {value}")
    
    print()
    print("=" * 80)
    print("【策略优化总结】")
    print("=" * 80)
    
    # 收益提升
    cons_ret = float(conservative_result["summary"]["总收益率"].replace("%", ""))
    agg_ret = float(aggressive_result["summary"]["总收益率"].replace("%", ""))
    improvement = agg_ret - cons_ret
    
    print()
    if improvement > 0:
        print(f"✅ 收益率提升：{improvement:.2f}个百分点")
    else:
        print(f"⚠️  收益率下降：{abs(improvement):.2f}个百分点")
    
    # 回撤变化
    cons_dd = float(conservative_result["summary"]["最大回撤"].replace("%", ""))
    agg_dd = float(aggressive_result["summary"]["最大回撤"].replace("%", ""))
    dd_change = agg_dd - cons_dd
    
    if dd_change > 0:
        print(f"⚠️  回撤增加：{dd_change:.2f}个百分点")
    else:
        print(f"✅ 回撤减少：{abs(dd_change):.2f}个百分点")
    
    # 夏普变化
    cons_sharpe = float(conservative_result["summary"]["夏普比率"])
    agg_sharpe = float(aggressive_result["summary"]["夏普比率"])
    
    if agg_sharpe > cons_sharpe:
        print(f"✅ 夏普比率提升：{cons_sharpe:.2f} -> {agg_sharpe:.2f}")
    else:
        print(f"⚠️  夏普比率下降：{cons_sharpe:.2f} -> {agg_sharpe:.2f}")
    
    print()
    print("【优化建议】")
    print("-" * 50)
    print("  1. 激进版适合风险承受能力较强的投资者")
    print("  2. 建议先用 50% 仓位试运行 1 个月")
    print("  3. 密切关注日内仓位上限，避免过度暴露")
    print("  4. 在市场波动率上升时，可适当降低 grid_pct")
    print("  5. 设置更严格的止损，控制单笔亏损")
    
    # 保存结果
    comparison_result = {
        "conservative": conservative_result,
        "aggressive": aggressive_result,
        "improvement": {
            "return_change": improvement,
            "drawdown_change": dd_change,
            "sharpe_change": agg_sharpe - cons_sharpe,
        }
    }
    
    with open("/Users/zhiwei.bu/Git/Marspu.github.io/Quant_qmt/backtest_aggressive_report.json", "w", encoding="utf-8") as f:
        json.dump(comparison_result, f, ensure_ascii=False, indent=2)
    
    print()
    print("详细对比报告已保存至：backtest_aggressive_report.json")
    
    return comparison_result


if __name__ == "__main__":
    compare_strategies()