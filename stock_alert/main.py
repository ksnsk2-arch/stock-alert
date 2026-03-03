"""
エントリーポイント
実行方法: python -m stock_alert.main
"""

import logging
import sys
from dotenv import load_dotenv

from stock_alert.config import NIKKEI225_TICKERS
from stock_alert.fetcher import fetch_all
from stock_alert.screener import run_screening
from stock_alert.notifier import send_discord
from stock_alert.tracker import save_pending

# .env 読み込み（ローカル開発用。GitHub ActionsではSecretsが直接注入される）
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=== 株式自動スクリーニング開始 ===")
    logger.info(f"対象銘柄数: {len(NIKKEI225_TICKERS)}")

    # Step 1: データ取得
    logger.info("--- データ取得 ---")
    all_data = fetch_all(NIKKEI225_TICKERS)
    if not all_data:
        logger.error("データ取得失敗。終了します。")
        sys.exit(1)

    # Step 2: スクリーニング
    logger.info("--- スクリーニング ---")
    recommended = run_screening(all_data)

    # Step 3: Discord通知
    logger.info("--- Discord通知 ---")
    send_discord(recommended)

    # Step 4: 推奨銘柄を pending.json に保存（夕方の追跡処理で使用）
    if recommended:
        logger.info("--- 追跡用データを保存 ---")
        save_pending(recommended)
        logger.info(f"推奨銘柄 {len(recommended)} 件を通知しました:")
        for r in recommended:
            logger.info(f"  {r['ticker']} {r['name']} ¥{r['price']:,.0f} "
                        f"（ファンダ{r['f_score']}/6, テクニカル{r['tech_count']}/4）")
    else:
        logger.info("本日の推奨銘柄なし。見送りを通知しました。")

    logger.info("=== 完了 ===")


if __name__ == "__main__":
    main()
