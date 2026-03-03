"""
データ取得モジュール
- yfinance: 株価・出来高・基本的なファンダメンタル指標
- J-Quants API: 四半期財務データ（高精度）
"""

import os
import time
import logging
import requests
import yfinance as yf
import pandas as pd
from typing import Optional
from stock_alert.config import TECHNICAL

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# yfinance: 株価・出来高データ取得
# ─────────────────────────────────────────────

def fetch_price_history(ticker: str) -> Optional[pd.DataFrame]:
    """過去N日間の株価・出来高データを取得する。"""
    try:
        t = yf.Ticker(ticker)
        df = t.history(period=f"{TECHNICAL['history_days']}d")
        if df.empty:
            logger.warning(f"{ticker}: 株価データが空")
            return None
        return df
    except Exception as e:
        logger.warning(f"{ticker}: 株価データ取得失敗 - {e}")
        return None


def fetch_yfinance_info(ticker: str) -> dict:
    """
    yfinanceのinfoからファンダメンタル指標を取得する。
    日本株はフィールド欠損があるため、取得できた項目のみ返す。
    """
    info = {}
    try:
        t = yf.Ticker(ticker)
        raw = t.info

        # 銘柄名
        info["name"] = raw.get("longName") or raw.get("shortName") or ticker

        # PER（trailingPE: 実績PER）
        info["per"] = raw.get("trailingPE")

        # PBR（priceToBook）
        info["pbr"] = raw.get("priceToBook")

        # ROE（returnOnEquity: 0.12 → 12%換算）
        roe = raw.get("returnOnEquity")
        info["roe"] = roe * 100 if roe is not None else None

        # 配当利回り（dividendYield: 0.028 → 2.8%換算）
        dy = raw.get("dividendYield")
        info["dividend_yield"] = dy * 100 if dy is not None else None

        # 売上高成長率（revenueGrowth: YoY）
        rg = raw.get("revenueGrowth")
        info["revenue_growth"] = rg * 100 if rg is not None else None

        # 現在の株価
        info["price"] = raw.get("currentPrice") or raw.get("regularMarketPrice")

        # 時価総額
        info["market_cap"] = raw.get("marketCap")

        # セクター
        info["sector"] = raw.get("sector", "不明")

    except Exception as e:
        logger.warning(f"{ticker}: yfinance info取得失敗 - {e}")

    return info


# ─────────────────────────────────────────────
# J-Quants API: 財務データ取得
# ─────────────────────────────────────────────

JQUANTS_BASE_URL = "https://api.jquants.com/v1"
_jquants_id_token: Optional[str] = None


def _get_jquants_id_token() -> Optional[str]:
    """
    J-Quants APIのIDトークンを取得する。
    リフレッシュトークンから認証し、IDトークンを返す。
    """
    global _jquants_id_token
    if _jquants_id_token:
        return _jquants_id_token

    refresh_token = os.getenv("JQUANTS_REFRESH_TOKEN")
    if not refresh_token:
        logger.warning("JQUANTS_REFRESH_TOKEN が未設定。J-Quantsをスキップします。")
        return None

    try:
        resp = requests.post(
            f"{JQUANTS_BASE_URL}/token/auth_refresh",
            params={"refreshtoken": refresh_token},
            timeout=10,
        )
        resp.raise_for_status()
        _jquants_id_token = resp.json().get("idToken")
        return _jquants_id_token
    except Exception as e:
        logger.warning(f"J-Quants 認証失敗: {e}")
        return None


def fetch_jquants_financials(ticker: str) -> dict:
    """
    J-Quants APIから直近の四半期財務データを取得する。
    取得できた場合: 自己資本比率などを返す
    取得できない場合: 空の辞書を返す（yfinanceにフォールバック）
    """
    id_token = _get_jquants_id_token()
    if not id_token:
        return {}

    # ティッカー「7203.T」→ 証券コード「7203」に変換
    code = ticker.replace(".T", "")

    try:
        headers = {"Authorization": f"Bearer {id_token}"}
        resp = requests.get(
            f"{JQUANTS_BASE_URL}/fins/statements",
            params={"code": code},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        statements = resp.json().get("statements", [])
        if not statements:
            return {}

        # 直近のデータを使用（リストの末尾）
        latest = statements[-1]

        result = {}

        # 自己資本比率（EquityToAssetRatio: 0.45 → 45%換算）
        equity_ratio = latest.get("EquityToAssetRatio")
        if equity_ratio is not None:
            result["equity_ratio"] = float(equity_ratio) * 100

        # 売上高（当期 vs 前期で成長率を計算）
        if len(statements) >= 2:
            prev = statements[-2]
            net_sales_current = latest.get("NetSales")
            net_sales_prev = prev.get("NetSales")
            if net_sales_current and net_sales_prev and float(net_sales_prev) != 0:
                growth = (float(net_sales_current) - float(net_sales_prev)) / abs(float(net_sales_prev)) * 100
                result["revenue_growth"] = growth

        return result

    except Exception as e:
        logger.warning(f"{ticker}: J-Quants 財務データ取得失敗 - {e}")
        return {}


# ─────────────────────────────────────────────
# 統合取得: 全銘柄のデータをまとめて取得
# ─────────────────────────────────────────────

def fetch_all(tickers: list[str]) -> list[dict]:
    """
    全銘柄のデータを取得し、辞書のリストで返す。
    各辞書: {"ticker": ..., "name": ..., "price": ..., "history": DataFrame, "per": ..., ...}
    """
    results = []
    total = len(tickers)

    for i, ticker in enumerate(tickers, 1):
        logger.info(f"[{i}/{total}] {ticker} を取得中...")

        # 株価履歴（テクニカル分析用）
        history = fetch_price_history(ticker)
        if history is None:
            continue

        # yfinanceでファンダメンタル取得
        info = fetch_yfinance_info(ticker)

        # J-Quants APIで財務データを補完
        jquants = fetch_jquants_financials(ticker)

        # J-Quantsのデータを優先してマージ（精度が高いため）
        if "equity_ratio" in jquants:
            info["equity_ratio"] = jquants["equity_ratio"]
        if "revenue_growth" in jquants:
            info["revenue_growth"] = jquants["revenue_growth"]

        data = {
            "ticker": ticker,
            "history": history,
            **info,
        }
        results.append(data)

        # レート制限対策: 1秒待機
        time.sleep(1)

    logger.info(f"データ取得完了: {len(results)}/{total} 銘柄")
    return results
