"""
夕方実行エントリーポイント（引け後 16:00）
推奨銘柄の終値を取得 → results.csv に追記 → Discord に結果サマリーを送信
実行方法: python -m stock_alert.evening
"""

import logging
import os
import subprocess
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

import requests
from stock_alert.tracker import record_results, build_result_summary

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


def _send_result_to_discord(text: str) -> None:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.error("DISCORD_WEBHOOK_URL が未設定")
        return
    try:
        resp = requests.post(webhook_url, json={"content": text}, timeout=10)
        resp.raise_for_status()
        logger.info("Discord に結果サマリーを送信しました")
    except Exception as e:
        logger.error(f"Discord 送信失敗: {e}")


def main():
    logger.info("=== 夕方: 結果追跡処理 開始 ===")

    results = record_results()

    if results:
        summary = build_result_summary(results)
        _send_result_to_discord(summary)
    else:
        logger.info("追跡対象なし（今日の推奨銘柄がなかった可能性）")

    logger.info("=== 完了 ===")


if __name__ == "__main__":
    main()
