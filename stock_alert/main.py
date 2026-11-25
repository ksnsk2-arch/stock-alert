"""
朝のエントリーポイント（08:00 JST）
データ取得 → 2段階スクリーニング → Discord通知 → 追跡ポジション登録

実行方法:
  python -m stock_alert.main            # 本番（Discord送信・positions.json更新）
  python -m stock_alert.main --dry-run  # 送信も保存もしない確認用
  python -m stock_alert.main -v         # DEBUGログ（全銘柄のスコアを表示）
"""

import argparse
import logging
import sys

from dotenv import load_dotenv

from stock_alert.config import NIKKEI225_TICKERS
from stock_alert.fetcher import fetch_all
from stock_alert.screener import run_screening
from stock_alert.notifier import send_discord
from stock_alert.tracker import open_positions, open_tickers

load_dotenv()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Discord送信とpositions更新をしない")
    ap.add_argument("-v", "--verbose", action="store_true", help="DEBUGログ")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("yfinance").setLevel(logging.ERROR)
    logging.getLogger("peewee").setLevel(logging.ERROR)
    logger = logging.getLogger(__name__)

    logger.info("=== 株式スクリーニング 朝便 開始 ===")
    held = open_tickers()
    logger.info(f"対象銘柄数: {len(NIKKEI225_TICKERS)} / 追跡中: {len(held)}")

    all_data = fetch_all(NIKKEI225_TICKERS)
    if not all_data:
        logger.error("データ取得失敗。終了します。")
        sys.exit(1)

    recommended, stats, near = run_screening(all_data, exclude=held)

    logger.info(f"ファネル: {stats}")
    for i, r in enumerate(recommended, 1):
        logger.info(
            f"  #{i} {r['ticker']} {r['name']} ¥{r['price']:,.0f} × {r['shares']}株 "
            f"（ファンダ{r['f_score']}/6 テクニカル{r['tech_count']}/4 fresh={r['tech']['fresh']}）"
        )
    if not recommended:
        logger.info("本日の推奨銘柄なし")
        for n in near:
            logger.info(f"  惜しい: {n['ticker']} {n['name']} ファンダ{n['f_score']}/6 テク{n['tech_count']}/4")

    send_discord(recommended, stats, near, open_count=len(held), dry_run=args.dry_run)

    if recommended and not args.dry_run:
        open_positions(recommended)

    logger.info("=== 完了 ===")


if __name__ == "__main__":
    main()
