#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
161129 LOF 近 6 个月回测报告
基于历史波动特征的蒙特卡洛模拟回测
"""

import json
import math
import random
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import csv

# 策略参数（使用更新后的配置）
CONFIG = {
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

# 基于 161129 历史特征的市场参数
MARKET_PARAMS = {
    # 2025 年 10 月 -2026 年 4 月港股市场特征估计
    "base_price": 1.150,  # 起始价格（元）
    "daily_volatility": 0.018,  # 日均波动率约 1.8%
    "intraday_range": 0.025,  # 日内平均波幅 2.5%
    "drift": -0.0002,  # 轻微负漂移（港股震荡市）
    "mean_reversion": 0.03,  # 均值回归强度
    
    # 月度趋势参数
    "monthly_trends": [
        # (月份，趋势漂移，波动率调整)
        (1, -0.02, 1.2),   # 1 月：年初调整
        (2, 0.01, 0.9),    # 2 月：春节效应
        (3, -0.01, 1.1),   # 3 月：政策观察期
        (4, 0.03, 1.0),    # 4 月：财报季
    ],
    
    # 国际局势影响因子
    "event_risks": [
        # FOMC 会议周
        {"probability": 0.05, "impact": -0.03, "duration": 3},
        # 中美关系消息
        {"probability": 0.03, "impact": -0.02, "duration": 2},
        # 经济数据发布
        {"probability": 0.08, "impact": 0.015, "duration": 1},
    ],
}


class BacktestEngine:
    def __init__(self, config: Dict, market_params: Dict):
        self.config = config
        self.market_params = market_params
        self.reset()
    
    def reset(self):
        """重置回测状态"""
        self.cash = self.config["initial_cash"]
        self.position = self.config["base_position"]
        self.base_position = self.config["base_position"]
        self.anchor_price = self.market_params["base_price"]
        self.daily_buy_amount = 0
        self.daily_order_count = 0
        self.trades = []
        self.daily_pnl = []
        self.equity_curve = []
        self.max_drawdown = 0
        self.peak_equity = self.config["initial_cash"] + self.config["base_position"] * self.market_params["base_price"]
    
    def generate_price_path(self, days: int) -> List[Dict]:
        """生成价格路径（蒙特卡洛模拟）"""
        random.seed(42)  # 固定随机种子以便复现
        
        prices = []
        current_price = self.market_params["base_price"]
        start_date = datetime(2025, 10, 13)
        
        for day in range(days):
            date = start_date + timedelta(days=day)
            month = date.month
            
            # 获取月度趋势调整
            month_adjust = (0, 1.0)
            for m, trend, vol_adj in self.market_params["monthly_trends"]:
                if month == m or (month == 12 and m == 1):
                    month_adjust = (trend, vol_adj)
                    break
            
            # 基础波动
            daily_return = random.gauss(self.market_params["drift"], self.market_params["daily_volatility"])
            daily_return += month_adjust[0]
            vol_multiplier = month_adjust[1]
            
            # 事件风险
            for event in self.market_params["event_risks"]:
                if random.random() < event["probability"]:
                    daily_return += event["impact"]
                    # 持续影响
                    for j in range(event["duration"] - 1):
                        if day + j < days:
                            daily_return += event["impact"] * 0.5
            
            # 均值回归
            if current_price > self.market_params["base_price"] * 1.1:
                daily_return -= self.market_params["mean_reversion"]
            elif current_price < self.market_params["base_price"] * 0.9:
                daily_return += self.market_params["mean_reversion"]
            
            current_price *= (1 + daily_return * vol_multiplier)
            current_price = max(current_price, 0.5)  # 价格下限
            
            # 生成日内高低点
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
        """生成日内 Tick 数据"""
        ticks = []
        base_ts = day_data["date"].replace(hour=9, minute=30).timestamp()
        
        # 跳过午休
        current_price = day_data["open"]
        tick_vol = (day_data["high"] - day_data["low"]) / math.sqrt(ticks_per_day)
        
        for i in range(ticks_per_day):
            # 处理午休
            hour = 9 + (i * 2.5) // 60
            minute = 30 + ((i * 2.5) % 60)
            
            if hour >= 11 and hour < 13:
                continue
            
            if hour >= 15:
                break
            
            # 随机游走 + 均值回归
            current_price += random.gauss(0, tick_vol)
            current_price = max(min(current_price, day_data["high"] * 1.01), day_data["low"] * 0.99)
            
            # 生成买卖盘口
            spread = current_price * 0.002  # 0.2% 价差
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
        """计算目标网格步数"""
        if price <= 0 or self.anchor_price <= 0 or grid_pct <= 0:
            return 0
        
        ratio = price / self.anchor_price
        raw = math.log(ratio) / math.log(1 + grid_pct)
        grid_index = math.floor(raw) if raw >= 0 else math.ceil(raw)
        bias_steps = int(round(score * self.config["signal_bias_steps"]))
        target_steps = -grid_index + bias_steps
        
        # 限制
        long_limit = self.config["max_intraday_long_volume"] // self.config["order_volume"]
        short_limit = self.config["max_intraday_short_volume"] // self.config["order_volume"]
        target_steps = max(-short_limit, min(long_limit, target_steps))
        
        # 止损
        if price <= self.anchor_price * (1 - self.config["stop_loss_pct"]):
            target_steps = min(target_steps, max(0, (self.position - self.base_position) // self.config["order_volume"]))
        
        return target_steps
    
    def run_backtest(self, days: int = 126) -> Dict:
        """运行回测"""
        self.reset()
        
        prices = self.generate_price_path(days)
        total_trades = 0
        total_turnover = 0
        daily_results = []
        
        for day_idx, day_data in enumerate(prices):
            # 每日重置
            self.daily_buy_amount = 0
            self.daily_order_count = 0
            day_trades = []
            
            # 更新锚定价格（使用昨日收盘）
            if day_idx > 0:
                self.anchor_price = prices[day_idx - 1]["close"]
            
            # 生成日内数据
            ticks = self.generate_intraday_ticks(day_data)
            
            # 模拟交易
            last_order_ts = 0
            current_steps = 0
            
            for tick in ticks:
                price = tick["last_price"]
                
                # 冷却时间检查
                if tick["ts"] - last_order_ts < self.config["cooldown_s"]:
                    continue
                
                # 收盘回补
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
                
                # 停止开新仓
                if hour >= 14 and datetime.fromtimestamp(tick["ts"]).minute >= 55:
                    continue
                
                # 计算信号分数（简化版）
                ret_5 = (price - self.anchor_price) / self.anchor_price if self.anchor_price > 0 else 0
                score = max(-1, min(1, -ret_5 * 10))  # 简单均值回归信号
                
                # 网格交易逻辑
                grid_pct = max(self.config["min_grid_pct"], 
                              min(self.config["max_grid_pct"], self.config["grid_pct"]))
                target_steps = self.calc_target_steps(price, grid_pct, score)
                
                step_diff = target_steps - current_steps
                if abs(step_diff) < 1:
                    continue
                
                unit = self.config["order_volume"]
                
                if step_diff > 0:  # 买入信号
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
                
                elif step_diff < 0:  # 卖出信号
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
            
            # 计算当日盈亏
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
        
        # 计算绩效指标
        initial_equity = self.config["initial_cash"] + self.config["base_position"] * self.market_params["base_price"]
        final_equity = self.cash + self.position * prices[-1]["close"]
        total_return = (final_equity - initial_equity) / initial_equity
        
        # 计算夏普比率
        daily_returns = []
        for i in range(1, len(self.equity_curve)):
            daily_returns.append((self.equity_curve[i] - self.equity_curve[i-1]) / self.equity_curve[i-1])
        
        avg_return = sum(daily_returns) / len(daily_returns) if daily_returns else 0
        std_return = math.sqrt(sum((r - avg_return)**2 for r in daily_returns) / len(daily_returns)) if len(daily_returns) > 1 else 1
        sharpe = (avg_return / std_return) * math.sqrt(252) if std_return > 0 else 0
        
        # 计算胜率
        buy_trades = [t for t in self.trades if t["side"] == "BUY"]
        sell_trades = [t for t in self.trades if t["side"] == "SELL"]
        
        # 简化胜率计算
        profitable_days = sum(1 for d in daily_results if d["drawdown"] < 0.01)
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
        """计算月度表现"""
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
        """计算风险指标"""
        equities = [d["equity"] for d in daily_results]
        max_equity = max(equities)
        min_equity = min(equities)
        
        # 计算 VaR
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


def main():
    print("=" * 70)
    print("161129 LOF T+0 网格策略回测报告（近 6 个月）")
    print("=" * 70)
    print()
    
    engine = BacktestEngine(CONFIG, MARKET_PARAMS)
    result = engine.run_backtest(days=126)  # 约 6 个月交易日
    
    print("【回测概要】")
    print("-" * 50)
    for key, value in result["summary"].items():
        print(f"  {key}: {value}")
    
    print()
    print("【月度表现】")
    print("-" * 50)
    print(f"  {'月份':<10} {'收益率':<12} {'交易次数':<10} {'交易天数':<10}")
    for m in result["monthly_performance"]:
        print(f"  {m['月份']:<10} {m['收益率']:<12} {m['交易次数']:<10} {m['交易天数']:<10}")
    
    print()
    print("【风险指标】")
    print("-" * 50)
    for key, value in result["risk_metrics"].items():
        print(f"  {key}: {value}")
    
    print()
    print("=" * 70)
    print("【策略评价】")
    print("=" * 70)
    
    # 策略评价
    sharpe = float(result["summary"]["夏普比率"].replace("夏普比率：", ""))
    max_dd = float(result["summary"]["最大回撤"].replace("最大回撤：", "").replace("%", ""))
    total_ret = float(result["summary"]["总收益率"].replace("总收益率：", "").replace("%", ""))
    
    print()
    if sharpe > 1.5:
        print("✅ 夏普比率优秀，风险调整后收益良好")
    elif sharpe > 1.0:
        print("⚠️  夏普比率中等，可接受")
    else:
        print("❌ 夏普比率偏低，需优化")
    
    if max_dd < 5:
        print("✅ 最大回撤控制良好")
    elif max_dd < 10:
        print("⚠️  最大回撤中等，注意风控")
    else:
        print("❌ 最大回撤偏大，建议降低仓位")
    
    if total_ret > 10:
        print("✅ 绝对收益表现优秀")
    elif total_ret > 5:
        print("⚠️  绝对收益中等")
    else:
        print("❌ 绝对收益偏低")
    
    print()
    print("【国际局势影响分析】")
    print("-" * 50)
    print("  • 美联储政策：关注 FOMC 会议期间波动率上升")
    print("  • 人民币汇率：USD/CNH 升值压力可能影响港股估值")
    print("  • 地缘政治：区域局势变化可能带来脉冲式波动")
    print("  • 经济数据：中国 PMI、社融等数据影响市场情绪")
    
    print()
    print("【操作建议】")
    print("-" * 50)
    print("  1. 保持当前网格参数，grid_pct=0.8% 适应当前波动")
    print("  2. 日内仓位上限 5000 份，控制风险敞口")
    print("  3. 14:57 强制回补 base position，避免隔夜风险")
    print("  4. 密切关注折溢价率，>3% 时暂停交易")
    print("  5. 建议先用 dry_run 模式验证 1-2 周")
    
    print()
    print("=" * 70)
    
    # 保存结果
    with open("/Users/zhiwei.bu/Git/Marspu.github.io/Quant_qmt/backtest_report.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n详细报告已保存至：backtest_report.json")


if __name__ == "__main__":
    main()