"""
データ取得モジュール
- yfinance: 株価・出来高・ファンダメンタル指標・自己資本比率（四半期BS）
- J-Quants API: 自己資本比率・売上成長率を高精度で上書き（任意）
"""

import os
import time
import logging
from typing import Optional

import requests
import yfinance as yf
import pandas as pd

from stock_alert.config import TECHNICAL

logger = logging.getLogger(__name__)

REQUEST_INTERVAL_SEC = 0.5


# ─────────────────────────────────────────────
# yfinance
# ─────────────────────────────────────────────

def fetch_price_history(ticker: str) -> Optional[pd.DataFrame]:
    """過去N日間の株価・出来高データを取得する。"""
    try:
        df = yf.Ticker(ticker).history(period=f"{TECHNICAL['history_days']}d", auto_adjust=False)
        if df.empty:
            logger.warning(f"{ticker}: 株価データが空")
            return None
        return df
    except Exception as e:
        logger.warning(f"{ticker}: 株価データ取得失敗 - {e}")
        return None


def fetch_yfinance_info(ticker: str, history: Optional[pd.DataFrame] = None) -> dict:
    """
    yfinanceのinfoからファンダメンタル指標を取得する。
    日本株はフィールド欠損があるため、取得できた項目のみ返す（欠損はNone）。
    """
    info: dict = {}
    try:
        t = yf.Ticker(ticker)
        raw = t.info or {}

        info["name"] = raw.get("longName") or raw.get("shortName") or ticker
        info["per"] = raw.get("trailingPE")
        info["pbr"] = raw.get("priceToBook")

        roe = raw.get("returnOnEquity")
        info["roe"] = roe * 100 if roe is not None else None

        # 配当利回り:
        #   yfinance 0.2.5x 以降 dividendYield は「%」で返る（3.02 = 3.02%）。
        #   旧版は小数（0.0302）。単位ブレを避けるため trailingAnnualDividendYield（常に小数）を優先する。
        tady = raw.get("trailingAnnualDividendYield")
        if tady is not None:
            info["dividend_yield"] = tady * 100
        else:
            dy = raw.get("dividendYield")
            info["dividend_yield"] = (dy * 100 if dy < 1 else dy) if dy is not None else None

        rg = raw.get("revenueGrowth")
        info["revenue_growth"] = rg * 100 if rg is not None else None

        price = raw.get("currentPrice") or raw.get("regularMarketPrice") or raw.get("previousClose")
        if price is None and history is not None and not history.empty:
            price = float(history["Close"].iloc[-1])
        info["price"] = price

        info["market_cap"] = raw.get("marketCap")
        info["sector"] = raw.get("sector", "不明")

        # 自己資本比率（四半期BS: 株主資本 ÷ 総資産）
        info["equity_ratio"] = _equity_ratio_from_balance_sheet(t)

    except Exception as e:
        logger.warning(f"{ticker}: yfinance info取得失敗 - {e}")

    return info


def _equity_ratio_from_balance_sheet(t: yf.Ticker) -> Optional[float]:
    try:
        bs = t.quarterly_balance_sheet
        if bs is None or bs.empty:
            bs = t.balance_sheet
        if bs is None or bs.empty:
            return None
        equity_row = next(
            (r for r in ("Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest") if r in bs.index),
            None,
        )
        if equity_row is None or "Total Assets" not in bs.index:
            return None
        equity = bs.loc[equity_row].dropna()
        assets = bs.loc["Total Assets"].dropna()
        if equity.empty or assets.empty or float(assets.iloc[0]) == 0:
            return None
        return float(equity.iloc[0]) / float(assets.iloc[0]) * 100
    except Exception:
        return None


# ─────────────────────────────────────────────
# J-Quants API（任意・あれば精度向上）
# ─────────────────────────────────────────────

JQUANTS_BASE_URL = "https://api.jquants.com/v1"
_jquants_id_token: Optional[str] = None
_jquants_disabled = False


def _get_jquants_id_token() -> Optional[str]:
    """
    IDトークンを取得する。
    優先順位: メール＋パスワード（リフレッシュトークンを毎回自動発行・失効しない）
            → JQUANTS_REFRESH_TOKEN（1週間で失効するので非推奨）
    失敗したらこの実行中は二度と試みない。トークンはログに出さない。
    """
    global _jquants_id_token, _jquants_disabled
    if _jquants_id_token:
        return _jquants_id_token
    if _jquants_disabled:
        return None

    mail = os.getenv("JQUANTS_MAIL_ADDRESS")
    password = os.getenv("JQUANTS_PASSWORD")
    try:
        if mail and password:
            resp = requests.post(
                f"{JQUANTS_BASE_URL}/token/auth_user",
                json={"mailaddress": mail, "password": password},
                timeout=10,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"auth_user HTTP {resp.status_code}")
            refresh_token = resp.json().get("refreshToken")
        else:
            refresh_token = os.getenv("JQUANTS_REFRESH_TOKEN")

        if not refresh_token:
            logger.info("J-Quants 認証情報なし → yfinanceのみで実行します")
            _jquants_disabled = True
            return None

        resp = requests.post(
            f"{JQUANTS_BASE_URL}/token/auth_refresh",
            params={"refreshtoken": refresh_token},
            timeout=10,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"auth_refresh HTTP {resp.status_code}（トークン失効の可能性。メール＋パスワード認証を推奨）"
            )
        _jquants_id_token = resp.json().get("idToken")
        logger.info("J-Quants 認証OK")
        return _jquants_id_token
    except Exception as e:
        logger.warning(f"J-Quants 認証失敗: {e} → yfinanceのみで続行")
        _jquants_disabled = True
        return None


def fetch_jquants_financials(ticker: str) -> dict:
    """直近の四半期財務データから自己資本比率・売上成長率を返す。取れなければ空。"""
    id_token = _get_jquants_id_token()
    if not id_token:
        return {}

    code = ticker.replace(".T", "")
    try:
        resp = requests.get(
            f"{JQUANTS_BASE_URL}/fins/statements",
            params={"code": code},
            headers={"Authorization": f"Bearer {id_token}"},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"{ticker}: J-Quants statements HTTP {resp.status_code}")
            return {}
        statements = resp.json().get("statements", [])
        if not statements:
            return {}

        latest = statements[-1]
        result = {}
        er = latest.get("EquityToAssetRatio")
        if er not in (None, ""):
            result["equity_ratio"] = float(er) * 100

        # 同じ期種別（通期 vs 通期 など）の前回と比較する
        same_type = [s for s in statements if s.get("TypeOfCurrentPeriod") == latest.get("TypeOfCurrentPeriod")]
        if len(same_type) >= 2:
            prev = same_type[-2]
            cur_s, prev_s = latest.get("NetSales"), prev.get("NetSales")
            if cur_s and prev_s and float(prev_s) != 0:
                result["revenue_growth"] = (float(cur_s) - float(prev_s)) / abs(float(prev_s)) * 100
        return result
    except Exception as e:
        logger.warning(f"{ticker}: J-Quants 財務データ取得失敗 - {e}")
        return {}


# ─────────────────────────────────────────────
# 統合取得
# ─────────────────────────────────────────────

def fetch_all(tickers: list[str]) -> list[dict]:
    """
    全銘柄のデータを取得し、辞書のリストで返す。
    各辞書: {"ticker", "name", "price", "history": DataFrame, "per", "pbr", "roe", ...}
    """
    results = []
    total = len(tickers)

    for i, ticker in enumerate(tickers, 1):
        if i == 1 or i % 10 == 0 or i == total:
            logger.info(f"[{i}/{total}] 取得中...")

        history = fetch_price_history(ticker)
        if history is None:
            continue

        info = fetch_yfinance_info(ticker, history)

        jq = fetch_jquants_financials(ticker)
        if "equity_ratio" in jq:
            info["equity_ratio"] = jq["equity_ratio"]
        if "revenue_growth" in jq:
            info["revenue_growth"] = jq["revenue_growth"]

        results.append({"ticker": ticker, "history": history, **info})
        time.sleep(REQUEST_INTERVAL_SEC)

    logger.info(f"データ取得完了: {len(results)}/{total} 銘柄")
    return results
