# 161129 LOF 策略更新报告

## 一、标的分析：161129 银华恒生港股通

### 1. 基本信息
- **代码**: 161129.SZ
- **名称**: 银华恒生港股通
- **类型**: LOF 基金 (上市型开放式基金)
- **投资标的**: 恒生港股通指数成分股
- **交易机制**: T+0（符合条件）
- **最小交易单位**: 100 份
- **价格精度**: 0.001 元

### 2. 近期走势分析（基于技术面）

#### 关键特征：
1. **波动性**: 港股通标的受多因素影响，日内波动通常在 1-3%
2. **流动性**: 盘中成交相对活跃，适合网格交易
3. **相关性**: 与恒生指数、人民币汇率高度相关

#### 技术面关注点：
- 支撑位/阻力位网格设置
- 成交量变化
- 折溢价率监控

---

## 二、国际局势分析

### 1. 主要影响因素

| 因素 | 影响方向 | 说明 |
|------|----------|------|
| 美联储利率政策 | ⚠️ 高影响 | 利率预期变化影响港股流动性 |
| 中美关系 | ⚠️ 中高影响 | 贸易政策、科技制裁等 |
| 人民币汇率 | ⚠️ 高影响 | USD/CNH 波动影响港股估值 |
| 中国经济数据 | ⚠️ 高影响 | GDP、PMI、社融等 |
| 港股通政策 | ⚠️ 中影响 | 资金流向变化 |

### 2. 当前局势评估（2026 年 4 月）

#### 风险因素：
1. **美联储政策**: 关注 FOMC 会议决议
2. **地缘政治**: 区域局势变化
3. **经济复苏**: 中国经济复苏节奏

#### 机会因素：
1. **估值优势**: 港股整体估值处于历史低位
2. **政策支持**: 稳增长政策持续
3. **资金回流**: 南向资金净流入

---

## 三、策略参数更新建议

### 1. 网格参数优化

基于当前波动环境和风险评估，建议调整以下参数：

```json
{
  "symbols": [
    {
      "symbol": "161129.SZ",
      "enabled": true,
      
      "基础仓位设置": {
        "base_position": 5000,
        "anchor_mode": "last_close",
        "anchor_price": null
      },
      
      "网格核心参数": {
        "order_volume": 200,
        "lot_size": 100,
        "grid_pct": 0.008,
        "min_grid_pct": 0.005,
        "max_grid_pct": 0.020
      },
      
      "风险控制": {
        "max_intraday_long_volume": 5000,
        "max_intraday_short_volume": 5000,
        "max_order_volume": 500,
        "max_spread_pct": 0.005,
        "stop_loss_pct": 0.025,
        "cash_reserve": 20000
      },
      
      "交易限制": {
        "max_position_value": 200000,
        "max_daily_buy_amount": 150000,
        "max_orders_per_day": 80,
        "cooldown_s": 2.0
      },
      
      "信号增强": {
        "use_adaptive_signal": true,
        "signal_bias_steps": 3,
        "close_back_to_base": true
      },
      
      "AI 信号配置": {
        "formulaic_alpha_enabled": true,
        "formulaic_alpha_blend": 0.50,
        "formulaic_alpha_bar_seconds": 60,
        "formulaic_alpha_lookback": 25,
        "formulaic_alpha_min_bars": 30,
        "formulaic_alpha_weights": {
          "alpha001": 0.10,
          "alpha002": 0.12,
          "alpha003": 0.06,
          "alpha004": 0.05,
          "alpha005": 0.07,
          "alpha006": 0.06,
          "alpha007": 0.08,
          "alpha008": 0.05,
          "alpha009": 0.07,
          "alpha010": 0.05,
          "alpha011": 0.05,
          "alpha012": 0.08,
          "alpha013": 0.04,
          "alpha014": 0.04,
          "alpha016": 0.05,
          "alpha017": 0.05,
          "alpha018": 0.06,
          "alpha026": 0.07,
          "alpha041": 0.08,
          "alpha042": 0.05,
          "alpha043": 0.08,
          "alpha044": 0.05,
          "alpha053": 0.04,
          "alpha054": 0.04,
          "alpha055": 0.05,
          "alpha101": 0.10
        }
      }
    }
  ]
}
```

### 2. 参数调整说明

| 参数 | 原值 | 新值 | 调整原因 |
|------|------|------|----------|
| grid_pct | 0.006 | 0.008 | 适应当前波动率，减少频繁交易 |
| order_volume | 100 | 200 | 提高单笔交易效率 |
| max_intraday_long_volume | 3000 | 5000 | 增加日内做多空间 |
| max_intraday_short_volume | 3000 | 5000 | 增加日内做空空间 |
| signal_bias_steps | 2 | 3 | 增强 AI 信号影响力 |
| formulaic_alpha_blend | 0.45 | 0.50 | 提高 Alpha101 权重 |
| formulaic_alpha_lookback | 20 | 25 | 延长回看周期 |
| stop_loss_pct | 0.03 | 0.025 | 收紧止损 |
| cooldown_s | 3.0 | 2.0 | 缩短冷却时间，提高响应速度 |

---

## 四、买入卖出策略规则

### 1. 买入条件（同时满足）

```
✓ 价格 < 网格买入线 (anchor * (1 - grid_pct * n))
✓ 当前仓位 < base_position + max_intraday_long_volume
✓ 可用资金 >= 订单金额 + cash_reserve
✓ 当日买入金额 < max_daily_buy_amount
✓  spreads < max_spread_pct
✓ 不在 stop_new_order_time 之后
✓ 距离上次订单 > cooldown_s
```

### 2. 卖出条件（同时满足）

```
✓ 价格 > 网格卖出线 (anchor * (1 + grid_pct * n))
✓ 当前仓位 > base_position - max_intraday_short_volume
✓ 可用仓位 >= 订单数量
✓ 当日订单数 < max_orders_per_day
✓ spreads < max_spread_pct
✓ 不在 stop_new_order_time 之后
✓ 距离上次订单 > cooldown_s
```

### 3. 平仓回补策略

```
时间条件：close_back_to_base_time (14:57:00) 后
操作：将日内仓位调整回 base_position
- 多头：卖出多余仓位
- 空头：买入回补
```

---

## 五、风险监控清单

### 每日检查项

| 时间 | 检查项 | 阈值 |
|------|--------|------|
| 开盘前 | 隔夜美股表现 | ±2% |
| 开盘前 | 人民币汇率 | USD/CNH 波动 |
| 盘中 | 折溢价率 | >3% 警惕 |
| 盘中 | 成交量异常 | >均量 2 倍 |
| 收盘后 | 当日盈亏 | 复盘统计 |
| 周末 | 周度绩效 | 夏普比率 |

### 预警信号

```
🔴 红色预警：
- 单日亏损 > 5%
- 折溢价率 > 5%
- 流动性枯竭（ spread > 1%）

🟡 黄色预警：
- 单日亏损 > 3%
- 折溢价率 > 3%
- 连续 3 日负收益

🟢 正常：
- 各项指标在阈值内
```

---

## 六、回测与验证

### 使用回测脚本验证策略

```bash
# 回测命令示例
python3 /Users/zhiwei.bu/Git/Marspu.github.io/lof_t0_grid_replay.py \
  --csv /path/to/161129_ticks.csv \
  --config /Users/zhiwei.bu/Git/Marspu.github.io/Quant_qmt/config_161129.json \
  --symbol 161129.SZ \
  --initial-cash 300000 \
  --base-position 5000 \
  --forward-seconds 300 \
  --signal-csv /tmp/lof_signals.csv \
  --report-json /tmp/lof_report.json
```

### 预期绩效指标

| 指标 | 目标值 |
|------|--------|
| 日均交易次数 | 20-50 次 |
| 日均 turnover | 5-10 万 |
| 胜率 | >55% |
| 夏普比率 | >1.5 |
| 最大回撤 | <5% |

---

## 七、配置文件

创建专属配置文件 `/Users/zhiwei.bu/Git/Marspu.github.io/Quant_qmt/config_161129.json`:

```json
{
  "account": {
    "miniqmt_dir": "/path/to/userdata_mini",
    "account_id": "8887271027",
    "account_type": "STOCK",
    "session_id": null
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
    "dry_run": true,
    "strategy_name": "lof_t0_grid_161129",
    "remark_prefix": "161129_t0",
    "use_whole_quote_subscribe": true
  },
  "ai": {
    "signal_hook_file": "",
    "signal_hook_func": "predict_signal",
    "formulaic_alpha_enabled": true,
    "formulaic_alpha_blend": 0.50,
    "formulaic_alpha_bar_seconds": 60,
    "formulaic_alpha_lookback": 25,
    "formulaic_alpha_min_bars": 30,
    "formulaic_alpha_weights": {
      "alpha001": 0.10,
      "alpha002": 0.12,
      "alpha003": 0.06,
      "alpha004": 0.05,
      "alpha005": 0.07,
      "alpha006": 0.06,
      "alpha007": 0.08,
      "alpha008": 0.05,
      "alpha009": 0.07,
      "alpha010": 0.05,
      "alpha011": 0.05,
      "alpha012": 0.08,
      "alpha013": 0.04,
      "alpha014": 0.04,
      "alpha016": 0.05,
      "alpha017": 0.05,
      "alpha018": 0.06,
      "alpha026": 0.07,
      "alpha041": 0.08,
      "alpha042": 0.05,
      "alpha043": 0.08,
      "alpha044": 0.05,
      "alpha053": 0.04,
      "alpha054": 0.04,
      "alpha055": 0.05,
      "alpha101": 0.10
    }
  },
  "symbols": [
    {
      "symbol": "161129.SZ",
      "enabled": true,
      "base_position": 5000,
      "anchor_mode": "last_close",
      "anchor_price": null,
      "order_volume": 200,
      "lot_size": 100,
      "grid_pct": 0.008,
      "min_grid_pct": 0.005,
      "max_grid_pct": 0.020,
      "max_intraday_long_volume": 5000,
      "max_intraday_short_volume": 5000,
      "max_order_volume": 500,
      "slippage_ticks": 1,
      "price_tick": 0.001,
      "max_spread_pct": 0.005,
      "cash_reserve": 20000,
      "max_position_value": 200000,
      "max_daily_buy_amount": 150000,
      "max_orders_per_day": 80,
      "cooldown_s": 2.0,
      "use_adaptive_signal": true,
      "signal_bias_steps": 3,
      "stop_loss_pct": 0.025,
      "close_back_to_base": true
    }
  ]
}
```

---

## 八、运行指南

### 1. 模拟运行（Dry Run）

```bash
cd /Users/zhiwei.bu/Git/Marspu.github.io
python3 lof_t0_grid_xtquant.py --config Quant_qmt/config_161129.json
```

### 2. 实盘运行

确保：
1. MiniQMT 已启动并登录
2. 配置文件中 `dry_run` 改为 `false`
3. 账户有足够资金和仓位

```bash
# 修改配置后运行
python3 lof_t0_grid_xtquant.py --config Quant_qmt/config_161129.json
```

---

## 九、后续优化方向

1. **信号增强**: 添加自定义 AI 信号钩子
2. **多标的扩展**: 增加其他 LOF 标的
3. **参数自适应**: 根据波动率动态调整网格
4. **风控升级**: 添加更多风控维度

---

*报告生成时间：2026-04-13*
*下次回顾：2026-04-20*