"""
指標計算モジュール
- ファンダメンタル指標のスコアリング
- テクニカル指標（MA, RSI, MACD, 出来高）の計算
"""

import logging
import pandas as pd
import ta as ta_lib
from stock_alert.config import FUNDAMENTAL, TECHNICAL

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# ファンダメンタル分析
# ─────────────────────────────────────────────

def score_fundamental(data: dict) -> tuple[int, dict]:
    """
    ファンダメンタル指標をスコアリングする。

    Returns:
        (score, detail): スコア（最大6点）と各指標の評価詳細
    """
    cfg = FUNDAMENTAL
    score = 0
    detail = {}

    # PER: 5〜20倍
    per = data.get("per")
    if per is not None:
        ok = cfg["per_min"] <= per <= cfg["per_max"]
        score += 1 if ok else 0
        detail["per"] = {"value": per, "ok": ok, "label": f"{per:.1f}倍"}
    else:
        detail["per"] = {"value": None, "ok": False, "label": "データなし"}

    # PBR: 1.5倍以下
    pbr = data.get("pbr")
    if pbr is not None:
        ok = pbr <= cfg["pbr_max"]
        score += 1 if ok else 0
        detail["pbr"] = {"value": pbr, "ok": ok, "label": f"{pbr:.2f}倍"}
    else:
        detail["pbr"] = {"value": None, "ok": False, "label": "データなし"}

    # ROE: 8%以上
    roe = data.get("roe")
    if roe is not None:
        ok = roe >= cfg["roe_min"]
        score += 1 if ok else 0
        detail["roe"] = {"value": roe, "ok": ok, "label": f"{roe:.1f}%"}
    else:
        detail["roe"] = {"value": None, "ok": False, "label": "データなし"}

    # 配当利回り: 1.5%以上
    dy = data.get("dividend_yield")
    if dy is not None:
        ok = dy >= cfg["dividend_yield_min"]
        score += 1 if ok else 0
        detail["dividend_yield"] = {"value": dy, "ok": ok, "label": f"{dy:.1f}%"}
    else:
        detail["dividend_yield"] = {"value": None, "ok": False, "label": "データなし"}

    # 売上高成長率: 0%以上（マイナス成長を除外）
    rg = data.get("revenue_growth")
    if rg is not None:
        ok = rg >= cfg["revenue_growth_min"]
        score += 1 if ok else 0
        detail["revenue_growth"] = {"value": rg, "ok": ok, "label": f"{rg:+.1f}%"}
    else:
        detail["revenue_growth"] = {"value": None, "ok": False, "label": "データなし"}

    # 自己資本比率: 30%以上
    er = data.get("equity_ratio")
    if er is not None:
        ok = er >= cfg["equity_ratio_min"]
        score += 1 if ok else 0
        detail["equity_ratio"] = {"value": er, "ok": ok, "label": f"{er:.1f}%"}
    else:
        detail["equity_ratio"] = {"value": None, "ok": False, "label": "データなし"}

    return score, detail


# ─────────────────────────────────────────────
# テクニカル分析
# ─────────────────────────────────────────────

def calc_technical(history: pd.DataFrame) -> dict:
    """
    テクニカル指標を計算する。

    Returns:
        {
            "volume_surge": bool,     # 出来高急増
            "golden_cross": bool,     # ゴールデンクロス
            "rsi_ok": bool,           # RSI適正ゾーン
            "macd_cross": bool,       # MACDクロス
            "volume_ratio": float,    # 出来高倍率
            "rsi_value": float,       # RSI値
            "ma_short": float,        # 短期MA値
            "ma_long": float,         # 長期MA値
        }
    """
    cfg = TECHNICAL
    result = {
        "volume_surge": False,
        "golden_cross": False,
        "rsi_ok": False,
        "macd_cross": False,
        "volume_ratio": 0.0,
        "rsi_value": 0.0,
        "ma_short": 0.0,
        "ma_long": 0.0,
    }

    if history is None or len(history) < cfg["ma_long"] + 5:
        logger.warning("テクニカル計算: データ不足")
        return result

    close = history["Close"]
    volume = history["Volume"]

    try:
        # ── 出来高急増 ──────────────────────────
        vol_ma20 = volume.rolling(20).mean().iloc[-1]
        vol_today = volume.iloc[-1]
        if vol_ma20 > 0:
            ratio = vol_today / vol_ma20
            result["volume_ratio"] = round(ratio, 2)
            result["volume_surge"] = ratio >= cfg["volume_surge_ratio"]

        # ── 移動平均・ゴールデンクロス ───────────
        ma_short = close.rolling(cfg["ma_short"]).mean()
        ma_long = close.rolling(cfg["ma_long"]).mean()

        result["ma_short"] = round(float(ma_short.iloc[-1]), 1)
        result["ma_long"] = round(float(ma_long.iloc[-1]), 1)

        # 当日: MA5 > MA25、前日: MA5 <= MA25 → ゴールデンクロス
        if len(ma_short) >= 2 and len(ma_long) >= 2:
            cross_today = ma_short.iloc[-1] > ma_long.iloc[-1]
            cross_prev = ma_short.iloc[-2] <= ma_long.iloc[-2]
            result["golden_cross"] = cross_today and cross_prev

        # ── RSI ────────────────────────────────
        rsi_series = ta_lib.momentum.RSIIndicator(close=close, window=14).rsi()
        if rsi_series is not None and not rsi_series.empty:
            rsi_val = float(rsi_series.iloc[-1])
            result["rsi_value"] = round(rsi_val, 1)
            result["rsi_ok"] = cfg["rsi_min"] <= rsi_val <= cfg["rsi_max"]

        # ── MACD クロス ─────────────────────────
        macd_obj = ta_lib.trend.MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
        macd_line   = macd_obj.macd()
        signal_line = macd_obj.macd_signal()
        if macd_line is not None and len(macd_line) >= 2:
            macd_today = macd_line.iloc[-1]
            sig_today  = signal_line.iloc[-1]
            macd_prev  = macd_line.iloc[-2]
            sig_prev   = signal_line.iloc[-2]
            result["macd_cross"] = (macd_today > sig_today) and (macd_prev <= sig_prev)

    except Exception as e:
        logger.warning(f"テクニカル指標計算エラー: {e}")

    return result


def count_tech_signals(tech: dict) -> int:
    """テクニカル条件を何個クリアしているかを返す。"""
    return sum([
        tech["volume_surge"],
        tech["golden_cross"],
        tech["rsi_ok"],
        tech["macd_cross"],
    ])
