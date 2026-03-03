"""
2段階スクリーニングモジュール
1st pass: ファンダメンタルスコアで上位15銘柄に絞る
2nd pass: テクニカル条件で「今が仕込みどき」を確認
"""

import logging
from stock_alert.config import FUNDAMENTAL, TECHNICAL, TOP_N, BUDGET, LOT_SIZE, MAX_STOCK_PRICE, TAKE_PROFIT_PCT, STOP_LOSS_PCT
from stock_alert.analyzer import score_fundamental, calc_technical, count_tech_signals

logger = logging.getLogger(__name__)


def run_screening(all_data: list[dict]) -> list[dict]:
    """
    全銘柄データを2段階でスクリーニングし、推奨銘柄リストを返す。

    Returns:
        推奨銘柄のリスト（辞書形式、通知に必要な情報を含む）
    """
    # ── 1st pass: ファンダメンタルスクリーニング ──────────────
    logger.info("=== 1st pass: ファンダメンタルスクリーニング ===")

    fundamental_scored = []

    for data in all_data:
        ticker = data["ticker"]
        price = data.get("price")

        # 株価が取得できない銘柄はスキップ
        if price is None:
            continue

        # 予算内で1単元買えるかチェック
        lot_cost = price * LOT_SIZE
        if price > MAX_STOCK_PRICE:
            logger.debug(f"{ticker}: 株価¥{price:,.0f} > 上限¥{MAX_STOCK_PRICE} → スキップ（ミニ株使用なら config.MAX_STOCK_PRICE を変更）")
            continue

        # ファンダメンタルスコア計算
        f_score, f_detail = score_fundamental(data)

        fundamental_scored.append({
            "ticker": ticker,
            "name": data.get("name", ticker),
            "price": price,
            "lot_cost": lot_cost,
            "sector": data.get("sector", "不明"),
            "f_score": f_score,
            "f_detail": f_detail,
            "history": data.get("history"),
        })

        logger.debug(f"{ticker}: ファンダスコア {f_score}/6")

    # スコア降順でソートし、上位N件を取り出す
    top_n = FUNDAMENTAL["top_n_fundamental"]
    fundamental_scored.sort(key=lambda x: x["f_score"], reverse=True)
    candidates = fundamental_scored[:top_n]

    logger.info(f"1st pass 通過: {len(candidates)}/{len(all_data)} 銘柄（上位{top_n}件）")

    # ── 2nd pass: テクニカルスクリーニング ──────────────────
    logger.info("=== 2nd pass: テクニカルスクリーニング ===")

    results = []

    for c in candidates:
        history = c.get("history")
        if history is None:
            continue

        tech = calc_technical(history)
        tech_count = count_tech_signals(tech)
        required = TECHNICAL["tech_signals_required"]

        logger.debug(
            f"{c['ticker']}: テクニカル {tech_count}/{4}条件クリア "
            f"（出来高{tech['volume_ratio']:.1f}x, RSI{tech['rsi_value']:.0f}, "
            f"GC={tech['golden_cross']}, MACD={tech['macd_cross']}）"
        )

        if tech_count >= required:
            price = c["price"]
            take_profit = round(price * (1 + TAKE_PROFIT_PCT / 100), 0)
            stop_loss   = round(price * (1 - STOP_LOSS_PCT / 100), 0)

            results.append({
                "ticker":       c["ticker"],
                "name":         c["name"],
                "price":        price,
                "lot_cost":     c["lot_cost"],
                "sector":       c["sector"],
                "f_score":      c["f_score"],
                "f_detail":     c["f_detail"],
                "tech":         tech,
                "tech_count":   tech_count,
                "take_profit":  take_profit,
                "stop_loss":    stop_loss,
            })

    # テクニカルシグナル数 → ファンダスコアの順でソート
    results.sort(key=lambda x: (x["tech_count"], x["f_score"]), reverse=True)

    top_results = results[:TOP_N]
    logger.info(f"2nd pass 通過: {len(top_results)} 銘柄（最終推奨）")

    return top_results
