"""可配置策略引擎。

每种策略都是纯技术指标（只用收盘价序列），无需外部模型/数据，参数可在界面配置。
- momentum        动量：短均线相对长均线的方向
- meanreversion   均值回归：价格偏离 N 日均线的 Z 分数
- breakout        通道突破：收盘价突破 N 日高低通道
- baseline        基线（演示用，逻辑与 momentum 一致，仅作兜底）

run_strategy() 是唯一入口：根据 strategy_type + strategy_config 计算建议。
"""
import json


# 策略目录：前端据此渲染配置 UI；后端据此校验默认值。
STRATEGY_CATALOG = {
    "momentum": {
        "name": "动量",
        "desc": "短均线相对长均线向上→看多，向下→看空。适合趋势市。",
        "params": {
            "short_window":    {"label": "短均窗口",   "default": 5,   "min": 2,   "max": 60,  "step": 1},
            "long_window":     {"label": "长均窗口",   "default": 20,  "min": 3,   "max": 250, "step": 1},
            "threshold":       {"label": "信号阈值(%)", "default": 0.5, "min": 0.1, "max": 5.0, "step": 0.1},
            "momentum_weight": {"label": "动量权重",   "default": 0.5, "min": 0.0, "max": 2.0, "step": 0.1},
        },
    },
    "meanreversion": {
        "name": "均值回归",
        "desc": "价格偏离 N 日均线超过 Z 倍标准差时反向：偏离上轨看回落，下轨看反弹。适合震荡市。",
        "params": {
            "window":  {"label": "均线窗口", "default": 20,  "min": 5,   "max": 250, "step": 1},
            "entry_z": {"label": "触发Z值",  "default": 1.5, "min": 0.5, "max": 4.0, "step": 0.1},
        },
    },
    "breakout": {
        "name": "通道突破",
        "desc": "收盘价突破 N 日最高/最低价 K% 时顺势：上破看多，下破看空。适合突破行情。",
        "params": {
            "window": {"label": "通道窗口",   "default": 20, "min": 5,   "max": 250, "step": 1},
            "k":      {"label": "突破幅度(%)", "default": 2.0, "min": 0.2, "max": 10.0, "step": 0.1},
        },
    },
    "baseline": {
        "name": "基线演示",
        "desc": "最简单趋势演示（与动量逻辑一致），无数据也可给中性建议。",
        "params": {},
    },
}


def _normalize_config(config):
    if not config:
        return {}
    if isinstance(config, str):
        try:
            return json.loads(config) or {}
        except Exception:
            return {}
    return dict(config)


def run_strategy(strategy_type, config, recent_prices):
    """recent_prices: [(date, close), ...] 升序。返回建议 dict。"""
    cfg = _normalize_config(config)
    closes = [p[1] for p in recent_prices]
    n = len(closes)
    if n < 2:
        return dict(
            signal="HOLD", predicted_price=None, predicted_low=None,
            predicted_high=None, confidence=0.3,
            note="数据不足，仅给中性建议。接入行情后会有预测。",
        )
    last = closes[-1]
    strategy_type = strategy_type or "momentum"

    # ---------- 均值回归 ----------
    if strategy_type == "meanreversion":
        window = int(cfg.get("window", 20))
        entry_z = float(cfg.get("entry_z", 1.5))
        w = closes[-min(window, n):]
        mean = sum(w) / len(w)
        var = sum((x - mean) ** 2 for x in w) / max(len(w) - 1, 1)
        std = var ** 0.5
        z = (last - mean) / std if std > 1e-9 else 0.0
        # 始终预测回归至均线（均值回归的核心）：价格偏离越远，回归信号越强
        pred = mean
        if z > entry_z:
            signal = "SELL"
        elif z < -entry_z:
            signal = "BUY"
        else:
            signal = "HOLD"
        low, high = mean - std, mean + std
        conf = min(0.92, 0.4 + abs(z) * 0.25)
        note = (f"均值回归：{window}日均线 {mean:.2f}，当前偏离 Z={z:.2f} "
                f"（触发阈值 ±{entry_z}）。预测回归至均线 {mean:.2f}。")

    # ---------- 通道突破 ----------
    elif strategy_type == "breakout":
        window = int(cfg.get("window", 20))
        k = float(cfg.get("k", 2.0)) / 100.0
        w = closes[-min(window, n):]
        hh, ll = max(w), min(w)
        if last > hh * (1 + k):
            signal, pred = "BUY", hh * (1 + k)
        elif last < ll * (1 - k):
            signal, pred = "SELL", ll * (1 - k)
        else:
            signal, pred = "HOLD", last
        low, high = min(ll, last * 0.97), max(hh, last * 1.03)
        dev = (last - hh) / hh if last > hh else (ll - last) / ll if last < ll else 0.0
        conf = min(0.92, 0.4 + max(0.0, dev) * 5)
        note = (f"通道突破：{window}日区间 [{ll:.2f}, {hh:.2f}]，"
                f"突破幅度阈值 {k*100:.1f}%。")

    # ---------- 动量（含 baseline 兜底）----------
    else:
        short_window = int(cfg.get("short_window", 5))
        long_window = int(cfg.get("long_window", 20))
        threshold = float(cfg.get("threshold", 0.5)) / 100.0
        weight = float(cfg.get("momentum_weight", 0.5))
        sw = min(short_window, n)
        lw = min(long_window, n)
        ma_s = sum(closes[-sw:]) / sw
        ma_l = sum(closes[-lw:]) / lw
        mom = (ma_s - ma_l) / max(ma_l, 1e-9)
        pred = last * (1 + mom * weight)
        pred = max(pred, last * 0.9)   # 限幅，避免极端外推
        pred = min(pred, last * 1.1)
        low, high = pred * 0.97, pred * 1.03
        if pred > last * (1 + threshold):
            signal = "BUY"
        elif pred < last * (1 - threshold):
            signal = "SELL"
        else:
            signal = "HOLD"
        conf = min(0.92, 0.45 + abs(mom) * 8 + min(n, 120) / 600)
        note = (f"动量：短均 {ma_s:.2f} vs 长均 {ma_l:.2f}"
                f"（动量 {mom*100:.1f}%，信号阈值 ±{threshold*100:.1f}%）。")

    return dict(
        signal=signal,
        predicted_price=round(pred, 2),
        predicted_low=round(low, 2),
        predicted_high=round(high, 2),
        confidence=round(conf, 2),
        model=strategy_type,
        note=note,
    )


def _strategy_min_window(strategy_type, cfg):
    """该策略至少需要多少历史点才开始有预测。"""
    strategy_type = strategy_type or "momentum"
    if strategy_type == "momentum":
        return max(int(cfg.get("short_window", 5)), int(cfg.get("long_window", 20)))
    if strategy_type in ("meanreversion", "breakout"):
        return int(cfg.get("window", 20))
    return 2


def rolling_predict(dates, closes, strategy_type, config):
    """对历史上每一天计算“截至当日”的策略预测价，返回贯穿历史的策略预测价曲线。

    返回 [(date, predicted_price|None, predicted_low|None, predicted_high|None, signal), ...]
    数据不足的前几天返回 None（前端断点不连线）。
    """
    cfg = _normalize_config(config)
    n = len(closes)
    out = []
    need = _strategy_min_window(strategy_type, cfg)
    for i in range(n):
        if i < need - 1:
            out.append((dates[i], None, None, None, "HOLD"))
            continue
        slice_prices = list(zip(dates[: i + 1], closes[: i + 1]))
        r = run_strategy(strategy_type, cfg, slice_prices)
        out.append((dates[i], r["predicted_price"], r["predicted_low"],
                    r["predicted_high"], r["signal"]))
    return out


def forecast_future(dates, closes, strategy_type, config, days=5):
    """在最新行情之后外推未来 days 个交易日的预测价（水平延伸最后预测 + 区间带）。

    返回 [(future_date, predicted_price, predicted_low, predicted_high, signal), ...]
    交易日历用“跳过周末”的自然日近似。
    """
    from datetime import timedelta, datetime

    cfg = _normalize_config(config)
    curve = rolling_predict(dates, closes, strategy_type, cfg)
    # 取最后一个有效预测
    last_valid = None
    for item in reversed(curve):
        if item[1] is not None:
            last_valid = item
            break
    if last_valid is None:
        return []
    base_pred, base_low, base_high, last_signal = last_valid[1], last_valid[2], last_valid[3], last_valid[4]

    last_dt = datetime.strptime(dates[-1], "%Y-%m-%d")
    fut = []
    cur = last_dt
    while len(fut) < days:
        cur = cur + timedelta(days=1)
        if cur.weekday() >= 5:  # 跳过周六周日
            continue
        ds = cur.strftime("%Y-%m-%d")
        fut.append((ds, round(base_pred, 2), round(base_low, 2), round(base_high, 2), last_signal))
    return fut
