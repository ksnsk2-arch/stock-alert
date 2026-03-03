"""
Discord Webhook 通知モジュール
推奨銘柄をEmbedフォーマットで送信する
"""

import os
import logging
import requests
from datetime import datetime, timezone, timedelta
from stock_alert.config import TAKE_PROFIT_PCT, STOP_LOSS_PCT, LOT_SIZE

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


def _ok(flag: bool) -> str:
    return "✅" if flag else "❌"


def _build_embed(rank: int, stock: dict) -> dict:
    """1銘柄分のDiscord Embedオブジェクトを生成する。"""
    ticker  = stock["ticker"]
    name    = stock["name"]
    price   = stock["price"]
    lot_cost = stock["lot_cost"]
    f_detail = stock["f_detail"]
    tech     = stock["tech"]
    take_profit = stock["take_profit"]
    stop_loss   = stock["stop_loss"]

    # ── ファンダメンタル表示 ──────────────────
    f_lines = []

    per = f_detail.get("per", {})
    f_lines.append(f"PER: {per.get('label', 'N/A')} {_ok(per.get('ok', False))}")

    pbr = f_detail.get("pbr", {})
    f_lines.append(f"PBR: {pbr.get('label', 'N/A')} {_ok(pbr.get('ok', False))}")

    roe = f_detail.get("roe", {})
    f_lines.append(f"ROE: {roe.get('label', 'N/A')} {_ok(roe.get('ok', False))}")

    dy = f_detail.get("dividend_yield", {})
    f_lines.append(f"配当利回り: {dy.get('label', 'N/A')} {_ok(dy.get('ok', False))}")

    rg = f_detail.get("revenue_growth", {})
    f_lines.append(f"売上成長率: {rg.get('label', 'N/A')} {_ok(rg.get('ok', False))}")

    er = f_detail.get("equity_ratio", {})
    f_lines.append(f"自己資本比率: {er.get('label', 'N/A')} {_ok(er.get('ok', False))}")

    fundamental_text = "\n".join(f_lines)

    # ── テクニカル表示 ────────────────────────
    t_lines = []
    t_lines.append(f"出来高: 平均の{tech['volume_ratio']:.1f}倍 {_ok(tech['volume_surge'])}")
    t_lines.append(f"MA状態: MA{5}={tech['ma_short']:,.0f} / MA{25}={tech['ma_long']:,.0f} {_ok(tech['golden_cross'])} GC")
    t_lines.append(f"RSI(14): {tech['rsi_value']:.1f} {_ok(tech['rsi_ok'])}")
    t_lines.append(f"MACD: クロス {_ok(tech['macd_cross'])}")

    technical_text = "\n".join(t_lines)

    # ── Embed 構成 ────────────────────────────
    color = 0x00C851  # 緑

    embed = {
        "title": f"{'①②③④⑤'[rank-1]} {name}（{ticker.replace('.T', '')}）",
        "color": color,
        "fields": [
            {
                "name": "💴 前日終値",
                "value": f"¥{price:,.0f}（100株 = ¥{lot_cost:,.0f}）",
                "inline": False,
            },
            {
                "name": "📋 ファンダメンタル（主役）",
                "value": f"```\n{fundamental_text}\n```",
                "inline": False,
            },
            {
                "name": "📈 テクニカル（エントリー確認）",
                "value": f"```\n{technical_text}\n```",
                "inline": False,
            },
            {
                "name": "🎯 利確ライン",
                "value": f"¥{take_profit:,.0f}（+{TAKE_PROFIT_PCT}%）",
                "inline": True,
            },
            {
                "name": "🛑 損切りライン",
                "value": f"¥{stop_loss:,.0f}（-{STOP_LOSS_PCT}%）",
                "inline": True,
            },
        ],
        "footer": {
            "text": "⚠️ 情報提供のみ。最終判断はご自身で。",
        },
    }
    return embed


def send_discord(stocks: list[dict]) -> None:
    """推奨銘柄をDiscordに送信する。"""
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.error("DISCORD_WEBHOOK_URL が未設定")
        return

    now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    if not stocks:
        # 推奨銘柄なし
        payload = {
            "embeds": [
                {
                    "title": f"📊 本日の推奨銘柄 [{now_jst}]",
                    "description": "本日は条件を満たす銘柄が見つかりませんでした。\n見送りが正解の日もあります。",
                    "color": 0x888888,
                }
            ]
        }
    else:
        embeds = []

        # ヘッダー Embed
        embeds.append({
            "title": f"📊 本日の推奨銘柄 [{now_jst}]",
            "description": f"ファンダメンタル優先スクリーニング結果（上位{len(stocks)}銘柄）",
            "color": 0x0099FF,
        })

        # 各銘柄 Embed
        for i, stock in enumerate(stocks, 1):
            embeds.append(_build_embed(i, stock))

        payload = {"embeds": embeds}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Discord通知を送信しました")
    except requests.HTTPError as e:
        logger.error(f"Discord送信失敗（HTTP {e.response.status_code}）: {e.response.text}")
    except Exception as e:
        logger.error(f"Discord送信失敗: {e}")
