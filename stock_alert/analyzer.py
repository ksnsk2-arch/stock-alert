"""
指標計算モジュール
- ファンダメンタル指標のスコアリング（6項目）
- テクニカル指標（MA, RSI, MACD, 出来高）の計算と4シグナル判定
"""

import logging
import pandas as pd
import ta as ta_lib
from stock_alert.config import FUNDAMENTAL, TECHNICAL

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# ファンダメンタル分析
# ─────────────────────────────────────────────

def _judge(value, ok: bool, label: str) -> dict:
    return {"value": value, "ok": ok, "label": label}


def score_fundamental(data: dict) -> tuple[int, dict]:
    """
    ファンダメンタル6項目を判定し、(score, detail) を返す。score は最大6点。
    データ欠損は不合格扱い（0点）だが label で「データなし」と分かるようにする。
    """
    cfg = FUNDAMENTAL
    detail = {}

    per = data.get("per")
    detail["per"] = (
        _judge(per, cfg["per_min"] <= per <= cfg["per_max"], f"{per:.1f}倍")
        if per is not None else _judge(None, False, "データなし")
    )

    pbr = data.get("pbr")
    detail["pbr"] = (
        _judge(pbr, pbr <= cfg["pbr_max"], f"{pbr:.2f}倍")
        if pbr is not None else _judge(None, False, "データなし")
    )

    roe = data.get("roe")
    detail["roe"] = (
        _judge(roe, roe >= cfg["roe_min"], f"{roe:.1f}%")
        if roe is not None else _judge(None, False, "データなし")
    )

    dy = data.get("dividend_yield")
    detail["dividend_yield"] = (
        _judge(dy, dy >= cfg["dividend_yield_min"], f"{dy:.2f}%")
        if dy is not None else _judge(None, False, "データなし")
    )

    rg = data.get("revenue_growth")
    detail["revenue_growth"] = (
        _judge(rg, rg >= cfg["revenue_growth_min"], f"{rg:+.1f}%")
        if rg is not None else _judge(None, False, "データなし")
    )

    er = data.get("equity_ratio")
    detail["equity_ratio"] = (
        _judge(er, er >= cfg["equity_ratio_min"], f"{er:.1f}%")
        if er is not None else _judge(None, False, "データなし")
    )

    score = sum(1 for d in detail.values() if d["ok"])
    return score, detail


# ─────────────────────────────────────────────
# テクニカル分析
# ─────────────────────────────────────────────

TECH_SIGNALS = ("trend_up", "macd_bull", "rsi_ok", "volume_surge")


def _crossed_up_within(fast: pd.Series, slow: pd.Series, days: int) -> bool:
    """直近 days 本以内に fast が slow を下から上に抜けたか。"""
    diff = (fast - slow).dropna()
    if len(diff) < days + 1:
        return False
    recent = diff.iloc[-(days + 1):]
    for i in range(1, len(recent)):
        if recent.iloc[i - 1] <= 0 < recent.iloc[i]:
            return True
    return False


def calc_technical(history: pd.DataFrame) -> dict:
    """
    テクニカル指標を計算し、4シグナルの真偽と各値を返す。

    シグナル:
      trend_up     : 終値 > MA25 かつ MA5 > MA25（上昇トレンドの状態）
      macd_bull    : MACD > シグナル線
      rsi_ok       : RSI(14) が適正ゾーン（買われすぎ・売られすぎでない）
      volume_surge : 出来高が20日平均の一定倍以上
    補助:
      fresh        : 直近N日以内にGCまたはMACDクロスが発生（ランク加点用）
    """
    cfg = TECHNICAL
    result = {
        "trend_up": False,
        "macd_bull": False,
        "rsi_ok": False,
        "volume_surge": False,
        "fresh": False,
        "golden_cross_recent": False,
        "macd_cross_recent": False,
        "volume_ratio": 0.0,
        "rsi_value": 0.0,
        "ma_short": 0.0,
        "ma_long": 0.0,
        "close": 0.0,
    }

    if history is None or len(history) < cfg["ma_long"] + 10:
        logger.warning("テクニカル計算: データ不足")
        return result

    close = history["Close"]
    volume = history["Volume"]

    try:
        result["close"] = round(float(close.iloc[-1]), 1)

        # ── 出来高 ───────────────────────────────
        vol_ma20 = volume.rolling(20).mean().iloc[-1]
        if vol_ma20 and vol_ma20 > 0:
            ratio = float(volume.iloc[-1]) / float(vol_ma20)
            result["volume_ratio"] = round(ratio, 2)
            result["volume_surge"] = ratio >= cfg["volume_surge_ratio"]

        # ── 移動平均・トレンド ────────────────────
        ma_s = close.rolling(cfg["ma_short"]).mean()
        ma_l = close.rolling(cfg["ma_long"]).mean()
        result["ma_short"] = round(float(ma_s.iloc[-1]), 1)
        result["ma_long"] = round(float(ma_l.iloc[-1]), 1)
        result["trend_up"] = bool(close.iloc[-1] > ma_l.iloc[-1] and ma_s.iloc[-1] > ma_l.iloc[-1])
        result["golden_cross_recent"] = _crossed_up_within(ma_s, ma_l, cfg["fresh_cross_days"])

        # ── RSI ────────────────────────────────
        rsi = ta_lib.momentum.RSIIndicator(close=close, window=14).rsi()
        if rsi is not None and not rsi.dropna().empty:
            v = float(rsi.dropna().iloc[-1])
            result["rsi_value"] = round(v, 1)
            result["rsi_ok"] = cfg["rsi_min"] <= v <= cfg["rsi_max"]

        # ── MACD ───────────────────────────────
        macd = ta_lib.trend.MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
        macd_line, sig_line = macd.macd(), macd.macd_signal()
        if macd_line is not None and not macd_line.dropna().empty:
            result["macd_bull"] = bool(macd_line.iloc[-1] > sig_line.iloc[-1])
            result["macd_cross_recent"] = _crossed_up_within(macd_line, sig_line, cfg["fresh_cross_days"])

        result["fresh"] = result["golden_cross_recent"] or result["macd_cross_recent"]

    except Exception as e:
        logger.warning(f"テクニカル指標計算エラー: {e}")

    return result


def count_tech_signals(tech: dict) -> int:
    """4シグナルのうち何個クリアしているかを返す。"""
    return sum(1 for k in TECH_SIGNALS if tech.get(k))
