"""
夕方のエントリーポイント（引け後 16:15 JST）
追跡中ポジションを終値で更新 → 決着分を results.csv に記録 → Discord にレポート

実行方法:
  python -m stock_alert.evening            # 本番
  python -m stock_alert.evening --dry-run  # 送信も保存もしない（positions.json は更新しない）
"""

import argparse
import logging

from dotenv import load_dotenv

from stock_alert.tracker import update_positions, build_evening_summary, load_positions, save_positions
from stock_alert.notifier import send_text

load_dotenv()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("yfinance").setLevel(logging.ERROR)
    logger = logging.getLogger(__name__)

    logger.info("=== 追跡レポート 夕便 開始 ===")

    snapshot = load_positions() if args.dry_run else None
    closed, still_open = update_positions()
    if args.dry_run and snapshot is not None:
        save_positions(snapshot)  # dry-run では元に戻す（results.csv への追記だけは起きる点に注意）

    if closed or still_open:
        summary = build_evening_summary(closed, still_open)
        logger.info("\n" + summary)
        send_text(summary, dry_run=args.dry_run)
    else:
        logger.info("追跡中の銘柄なし。通知はしない")

    logger.info("=== 完了 ===")


if __name__ == "__main__":
    main()
