"""
結果トラッカーモジュール（複数日追跡）
- 朝: 推奨銘柄を data/positions.json に「追跡中ポジション」として登録
- 夕: 追跡中ポジションの終値を更新し、利確/損切り/期限到達で決着したものを data/results.csv に記録
"""

import csv
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from stock_alert.config import MAX_HOLD_DAYS, MAX_OPEN_POSITIONS

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

DATA_DIR = Path(__file__).parent.parent / "data"
POSITIONS_FILE = DATA_DIR / "positions.json"
RESULTS_FILE = DATA_DIR / "results.csv"
LEGACY_PENDING = DATA_DIR / "pending.json"

RESULTS_HEADER = [
    "date_open",     # 推奨日（JST）
    "date_close",    # 決着日
    "ticker",
    "name",
    "entry_price",   # 推奨時の前日終値
    "exit_price",    # 決着時の終値
    "change_pct",    # 変動率（%）
    "result",        # TP=利確 / SL=損切り / TIME=期限到達
    "days_held",     # 保有営業日数
    "shares",        # 想定株数
    "pnl_yen",       # 想定損益（円）
    "f_score",
    "tech_count",
]


def today_jst() -> str:
    return datetime.now(JST).date().isoformat()


def _ensure_files():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not RESULTS_FILE.exists():
        with open(RESULTS_FILE, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=RESULTS_HEADER).writeheader()
    if LEGACY_PENDING.exists():
        LEGACY_PENDING.unlink()


def load_positions() -> list[dict]:
    if not POSITIONS_FILE.exists():
        return []
    with open(POSITIONS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_positions(positions: list[dict]) -> None:
    _ensure_files()
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(positions, f, ensure_ascii=False, indent=2)


def open_tickers() -> set[str]:
    return {p["ticker"] for p in load_positions()}


# ─────────────────────────────────────────────
# 朝: ポジション登録
# ─────────────────────────────────────────────

def open_positions(recommended: list[dict]) -> list[dict]:
    """推奨銘柄を追跡中ポジションに追加する（重複・上限超過はスキップ）。追加分を返す。"""
    _ensure_files()
    positions = load_positions()
    held = {p["ticker"] for p in positions}
    added = []
    for r in recommended:
        if len(positions) >= MAX_OPEN_POSITIONS:
            logger.info(f"追跡上限 {MAX_OPEN_POSITIONS} 件に達したため {r['ticker']} は登録しない")
            break
        if r["ticker"] in held:
            continue
        p = {
            "date_open": today_jst(),
            "ticker": r["ticker"],
            "name": r["name"],
            "entry_price": float(r["price"]),
            "shares": int(r["shares"]),
            "take_profit": float(r["take_profit"]),
            "stop_loss": float(r["stop_loss"]),
            "f_score": r["f_score"],
            "tech_count": r["tech_count"],
            "last_close": float(r["price"]),
            "last_pct": 0.0,
            "days_held": 0,
        }
        positions.append(p)
        added.append(p)
    save_positions(positions)
    logger.info(f"positions.json: {len(added)} 件追加 → 追跡中 {len(positions)} 件")
    return added


# ─────────────────────────────────────────────
# 夕: 終値で更新・決着判定
# ─────────────────────────────────────────────

def _closes_since(ticker: str, date_open: str) -> pd.Series | None:
    """推奨日以降の終値系列（推奨当日を含む）を返す。"""
    try:
        hist = yf.Ticker(ticker).history(period="2mo", auto_adjust=False)
        if hist.empty:
            return None
        closes = hist["Close"].copy()
        closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
        return closes[closes.index >= pd.Timestamp(date_open)]
    except Exception as e:
        logger.warning(f"{ticker}: 終値取得失敗 - {e}")
        return None


def update_positions() -> tuple[list[dict], list[dict]]:
    """
    追跡中ポジションを最新終値で更新する。
    Returns: (決着したポジション, 継続中ポジション)
    """
    _ensure_files()
    positions = load_positions()
    if not positions:
        logger.info("追跡中ポジションなし")
        return [], []

    closed, still_open = [], []
    for p in positions:
        closes = _closes_since(p["ticker"], p["date_open"])
        if closes is None or closes.empty:
            logger.warning(f"{p['ticker']}: 推奨日以降の終値なし。継続")
            still_open.append(p)
            continue

        entry = p["entry_price"]
        result, exit_price, exit_date, days = None, None, None, 0
        for i, (ts, c) in enumerate(closes.items(), start=1):
            days = i
            c = float(c)
            if c >= p["take_profit"]:
                result, exit_price, exit_date = "TP", c, ts
                break
            if c <= p["stop_loss"]:
                result, exit_price, exit_date = "SL", c, ts
                break
            if i >= MAX_HOLD_DAYS:
                result, exit_price, exit_date = "TIME", c, ts
                break

        last_close = float(closes.iloc[-1])
        p["last_close"] = round(last_close, 1)
        p["last_pct"] = round((last_close - entry) / entry * 100, 2)
        p["days_held"] = days

        if result is None:
            still_open.append(p)
            logger.info(f"{p['ticker']}: 継続 {p['days_held']}日目 {p['last_pct']:+.2f}%")
            continue

        change_pct = round((exit_price - entry) / entry * 100, 2)
        row = {
            "date_open": p["date_open"],
            "date_close": exit_date.date().isoformat(),
            "ticker": p["ticker"],
            "name": p["name"],
            "entry_price": entry,
            "exit_price": round(exit_price, 1),
            "change_pct": change_pct,
            "result": result,
            "days_held": days,
            "shares": p["shares"],
            "pnl_yen": round((exit_price - entry) * p["shares"]),
            "f_score": p["f_score"],
            "tech_count": p["tech_count"],
        }
        closed.append(row)
        logger.info(f"{p['ticker']}: 決着 {result} {change_pct:+.2f}%（{days}日）")

    if closed:
        with open(RESULTS_FILE, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=RESULTS_HEADER).writerows(closed)
        logger.info(f"results.csv に {len(closed)} 件を追記")

    save_positions(still_open)
    return closed, still_open


# ─────────────────────────────────────────────
# 集計
# ─────────────────────────────────────────────

def cumulative_stats() -> dict | None:
    if not RESULTS_FILE.exists():
        return None
    with open(RESULTS_FILE, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None
    n = len(rows)
    pct = [float(r["change_pct"]) for r in rows]
    pnl = [float(r["pnl_yen"]) for r in rows]
    wins = sum(1 for v in pct if v > 0)
    return {
        "n": n,
        "win_rate": wins / n * 100,
        "avg_pct": sum(pct) / n,
        "total_pnl": sum(pnl),
        "tp": sum(1 for r in rows if r["result"] == "TP"),
        "sl": sum(1 for r in rows if r["result"] == "SL"),
        "time": sum(1 for r in rows if r["result"] == "TIME"),
    }


def build_evening_summary(closed: list[dict], still_open: list[dict]) -> str:
    lines = [f"**📒 追跡レポート {today_jst()}**"]

    if closed:
        lines.append("\n**決着**")
        for r in closed:
            mark = {"TP": "🎯 利確", "SL": "🛑 損切り", "TIME": "⏱ 期限"}[r["result"]]
            lines.append(
                f"{mark} **{r['name']}**（{r['ticker'].replace('.T', '')}）"
                f" {r['change_pct']:+.2f}% / {r['days_held']}日 / 想定{r['pnl_yen']:+,}円"
            )

    if still_open:
        lines.append("\n**継続中**")
        for p in still_open:
            emoji = "🟢" if p["last_pct"] >= 0 else "🔴"
            lines.append(
                f"{emoji} {p['name']}（{p['ticker'].replace('.T', '')}）"
                f" {p['last_pct']:+.2f}% / {p['days_held']}日目 / 終値¥{p['last_close']:,.0f}"
            )

    if not closed and not still_open:
        lines.append("追跡中の銘柄なし")

    st = cumulative_stats()
    if st:
        lines.append(
            f"\n累計 {st['n']}件: 勝率 **{st['win_rate']:.0f}%** / 平均 {st['avg_pct']:+.2f}% / "
            f"想定損益 {st['total_pnl']:+,.0f}円（利確{st['tp']} 損切{st['sl']} 期限{st['time']}）"
        )
    return "\n".join(lines)
