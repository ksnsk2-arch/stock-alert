"""
2段階スクリーニングモジュール
1st pass: ファンダメンタルスコア（6項目）で足切り＆上位N銘柄に絞る
2nd pass: テクニカル4シグナルで「今が仕込みどき」を確認
"""

import logging
import math

from stock_alert.config import (
    FUNDAMENTAL, TECHNICAL, TOP_N,
    BUDGET, MAX_POSITION_RATIO, TRADE_MODE, LOT_SIZE, MAX_STOCK_PRICE,
    TAKE_PROFIT_PCT, STOP_LOSS_PCT,
)
from stock_alert.analyzer import score_fundamental, calc_technical, count_tech_signals

logger = logging.getLogger(__name__)


def position_size(price: float) -> tuple[int, float]:
    """予算内で買える株数と投入額を返す。買えなければ (0, 0)。"""
    cap = BUDGET * MAX_POSITION_RATIO
    if price is None or price <= 0:
        return 0, 0.0
    if TRADE_MODE == "lot":
        if price > MAX_STOCK_PRICE:
            return 0, 0.0
        lots = math.floor(cap / (price * LOT_SIZE))
        shares = lots * LOT_SIZE
    else:
        shares = math.floor(cap / price)
    return shares, shares * price


def run_screening(all_data: list[dict], exclude: set[str] | None = None) -> tuple[list[dict], dict, list[dict]]:
    """
    全銘柄データを2段階でスクリーニングする。

    Args:
        all_data: fetch_all の結果
        exclude:  既に追跡中などで除外するティッカー集合

    Returns:
        (推奨銘柄リスト, ファネル統計, 惜しい銘柄リスト)
    """
    exclude = exclude or set()
    stats = {
        "total": len(all_data),
        "priced": 0,
        "affordable": 0,
        "f_pass": 0,
        "candidates": 0,
        "tech_pass": 0,
    }

    # ── 1st pass: ファンダメンタル ─────────────────────────
    logger.info("=== 1st pass: ファンダメンタル ===")
    scored = []
    for d in all_data:
        price = d.get("price")
        if price is None:
            continue
        stats["priced"] += 1

        shares, cost = position_size(price)
        if shares <= 0:
            logger.debug(f"{d['ticker']}: 株価¥{price:,.0f} は予算内で買えない → スキップ")
            continue
        stats["affordable"] += 1

        f_score, f_detail = score_fundamental(d)
        if f_score < FUNDAMENTAL["min_f_score"]:
            continue
        stats["f_pass"] += 1

        scored.append({
            "ticker": d["ticker"],
            "name": d.get("name", d["ticker"]),
            "price": price,
            "shares": shares,
            "cost": cost,
            "sector": d.get("sector", "不明"),
            "dividend_yield": d.get("dividend_yield"),
            "f_score": f_score,
            "f_detail": f_detail,
            "history": d.get("history"),
        })

    scored.sort(key=lambda x: (x["f_score"], x["dividend_yield"] or 0), reverse=True)
    candidates = scored[: FUNDAMENTAL["top_n_fundamental"]]
    stats["candidates"] = len(candidates)
    logger.info(
        f"1st pass: 株価あり{stats['priced']} → 予算内{stats['affordable']} → "
        f"スコア{FUNDAMENTAL['min_f_score']}以上{stats['f_pass']} → 上位{len(candidates)}銘柄"
    )

    # ── 2nd pass: テクニカル ───────────────────────────────
    logger.info("=== 2nd pass: テクニカル ===")
    passed, near = [], []
    required = TECHNICAL["tech_signals_required"]

    for c in candidates:
        if c["history"] is None:
            continue
        tech = calc_technical(c["history"])
        tc = count_tech_signals(tech)
        price = c["price"]
        row = {
            **{k: v for k, v in c.items() if k != "history"},
            "tech": tech,
            "tech_count": tc,
            "take_profit": round(price * (1 + TAKE_PROFIT_PCT / 100), 1),
            "stop_loss": round(price * (1 - STOP_LOSS_PCT / 100), 1),
        }
        logger.debug(
            f"{c['ticker']}: ファンダ{c['f_score']}/6 テクニカル{tc}/4 "
            f"(trend={tech['trend_up']} macd={tech['macd_bull']} rsi={tech['rsi_value']} vol={tech['volume_ratio']}x fresh={tech['fresh']})"
        )
        if c["ticker"] in exclude:
            continue
        if tc >= required:
            passed.append(row)
        else:
            near.append(row)

    stats["tech_pass"] = len(passed)

    # ランク: テクニカル数 → 新鮮なクロスあり → ファンダ → 配当
    rank_key = lambda x: (x["tech_count"], x["tech"]["fresh"], x["f_score"], x["dividend_yield"] or 0)
    passed.sort(key=rank_key, reverse=True)
    near.sort(key=rank_key, reverse=True)

    top = passed[:TOP_N]
    logger.info(f"2nd pass: {len(passed)} 銘柄通過 → 推奨 {len(top)} 銘柄")
    return top, stats, near[:3]
