"""
結果トラッカーモジュール
- 朝: 推奨銘柄を pending.json に保存
- 夕(16時以降): 終値を取得して data/results.csv に追記
"""

import csv
import json
import logging
import os
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import yfinance as yf

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

DATA_DIR     = Path(__file__).parent.parent / "data"
PENDING_FILE = DATA_DIR / "pending.json"
RESULTS_FILE = DATA_DIR / "results.csv"

RESULTS_HEADER = [
    "date",          # 推奨日（JST）
    "ticker",
    "name",
    "entry_price",   # 推奨時の前日終値
    "close_price",   # 推奨当日の終値
    "change_pct",    # 変動率（%）
    "hit_tp",        # 利確ライン（+5%）到達: 1/0
    "hit_sl",        # 損切りライン（-3%）到達: 1/0
    "take_profit",   # 利確ライン（円）
    "stop_loss",     # 損切りライン（円）
    "f_score",       # ファンダスコア（/6）
    "tech_count",    # テクニカルシグナル数（/4）
]


def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _init_csv():
    """results.csvが存在しなければヘッダー行を作成する。"""
    if not RESULTS_FILE.exists():
        with open(RESULTS_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=RESULTS_HEADER)
            writer.writeheader()


# ─────────────────────────────────────────────
# 朝の処理: 推奨銘柄を pending.json に保存
# ─────────────────────────────────────────────

def save_pending(recommended: list[dict]) -> None:
    """
    朝の推奨銘柄リストを pending.json に保存する。
    夕方の追跡処理で読み込んで終値を取得するために使う。
    """
    _ensure_data_dir()

    today = date.today().isoformat()
    records = []
    for r in recommended:
        records.append({
            "date":        today,
            "ticker":      r["ticker"],
            "name":        r["name"],
            "entry_price": r["price"],
            "take_profit": r["take_profit"],
            "stop_loss":   r["stop_loss"],
            "f_score":     r["f_score"],
            "tech_count":  r["tech_count"],
        })

    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    logger.info(f"pending.json に {len(records)} 件を保存しました")


# ─────────────────────────────────────────────
# 夕方の処理: 終値を取得して results.csv に追記
# ─────────────────────────────────────────────

def record_results() -> list[dict]:
    """
    pending.json を読み込み、当日終値を取得して results.csv に追記する。
    追記したレコードのリストを返す。
    """
    _ensure_data_dir()
    _init_csv()

    if not PENDING_FILE.exists():
        logger.info("pending.json が見つかりません。今日の推奨銘柄なし。")
        return []

    with open(PENDING_FILE, encoding="utf-8") as f:
        pending = json.load(f)

    if not pending:
        return []

    results = []
    for rec in pending:
        ticker      = rec["ticker"]
        entry_price = float(rec["entry_price"])
        take_profit = float(rec["take_profit"])
        stop_loss   = float(rec["stop_loss"])

        # 当日の終値を取得
        close_price = _fetch_today_close(ticker)
        if close_price is None:
            logger.warning(f"{ticker}: 終値が取得できませんでした")
            continue

        change_pct = round((close_price - entry_price) / entry_price * 100, 2)
        hit_tp = 1 if close_price >= take_profit else 0
        hit_sl = 1 if close_price <= stop_loss else 0

        row = {
            "date":        rec["date"],
            "ticker":      ticker,
            "name":        rec["name"],
            "entry_price": entry_price,
            "close_price": close_price,
            "change_pct":  change_pct,
            "hit_tp":      hit_tp,
            "hit_sl":      hit_sl,
            "take_profit": take_profit,
            "stop_loss":   stop_loss,
            "f_score":     rec["f_score"],
            "tech_count":  rec["tech_count"],
        }
        results.append(row)

        logger.info(
            f"{ticker}: 推奨¥{entry_price:,.0f} → 終値¥{close_price:,.0f} "
            f"（{change_pct:+.2f}%）TP={hit_tp} SL={hit_sl}"
        )

    # CSV に追記
    if results:
        with open(RESULTS_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=RESULTS_HEADER)
            writer.writerows(results)
        logger.info(f"results.csv に {len(results)} 件を追記しました")

    # 処理済みの pending.json を削除
    PENDING_FILE.unlink()

    return results


def _fetch_today_close(ticker: str) -> float | None:
    """当日の終値を yfinance から取得する。"""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="2d")  # 当日分を確実に含める
        if hist.empty:
            return None
        return round(float(hist["Close"].iloc[-1]), 1)
    except Exception as e:
        logger.warning(f"{ticker}: 終値取得失敗 - {e}")
        return None


# ─────────────────────────────────────────────
# 夕方Discord通知: 結果サマリーを送信
# ─────────────────────────────────────────────

def build_result_summary(results: list[dict]) -> str:
    """結果サマリーのテキストを生成する（Discord通知用）。"""
    if not results:
        return "本日の追跡結果なし"

    lines = ["**本日の推奨銘柄 結果**\n"]
    for r in results:
        emoji = "🟢" if r["change_pct"] > 0 else "🔴"
        tp_mark = " **🎯 利確到達**" if r["hit_tp"] else ""
        sl_mark = " **🛑 損切り到達**" if r["hit_sl"] else ""
        lines.append(
            f"{emoji} **{r['name']}**（{r['ticker'].replace('.T','')}）"
            f"  {r['change_pct']:+.2f}%{tp_mark}{sl_mark}"
        )

    hit_tp_count = sum(r["hit_tp"] for r in results)
    hit_sl_count = sum(r["hit_sl"] for r in results)
    avg_change   = sum(r["change_pct"] for r in results) / len(results)

    lines.append(f"\n平均変動: **{avg_change:+.2f}%**")
    lines.append(f"利確到達: {hit_tp_count}件 / 損切り到達: {hit_sl_count}件")

    return "\n".join(lines)
