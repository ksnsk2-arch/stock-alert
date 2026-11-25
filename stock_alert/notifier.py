"""
Discord Webhook 通知モジュール
- 朝: 推奨銘柄をEmbedで送信（0件の日はファネルと「惜しい銘柄」を添える）
- 夕: 追跡結果のテキストサマリーを送信
"""

import os
import logging
from datetime import datetime, timezone, timedelta

import requests

from stock_alert.config import TAKE_PROFIT_PCT, STOP_LOSS_PCT, TECHNICAL, FUNDAMENTAL, TRADE_MODE

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))
RANK_MARKS = "①②③④⑤"


def _ok(flag: bool) -> str:
    return "✅" if flag else "❌"


def _fundamental_text(f_detail: dict) -> str:
    rows = [
        ("PER", "per"), ("PBR", "pbr"), ("ROE", "roe"),
        ("配当利回り", "dividend_yield"), ("売上成長率", "revenue_growth"), ("自己資本比率", "equity_ratio"),
    ]
    return "\n".join(
        f"{label}: {f_detail.get(key, {}).get('label', 'N/A')} {_ok(f_detail.get(key, {}).get('ok', False))}"
        for label, key in rows
    )


def _technical_text(tech: dict) -> str:
    s, l = TECHNICAL["ma_short"], TECHNICAL["ma_long"]
    lines = [
        f"トレンド: MA{s}={tech['ma_short']:,.0f} / MA{l}={tech['ma_long']:,.0f} {_ok(tech['trend_up'])}",
        f"MACD: {'強気' if tech['macd_bull'] else '弱気'} {_ok(tech['macd_bull'])}",
        f"RSI(14): {tech['rsi_value']:.1f} {_ok(tech['rsi_ok'])}",
        f"出来高: 平均の{tech['volume_ratio']:.1f}倍 {_ok(tech['volume_surge'])}",
    ]
    if tech.get("fresh"):
        what = []
        if tech.get("golden_cross_recent"):
            what.append("GC")
        if tech.get("macd_cross_recent"):
            what.append("MACDクロス")
        lines.append(f"🆕 直近{TECHNICAL['fresh_cross_days']}日以内に {'・'.join(what)}")
    return "\n".join(lines)


def _build_embed(rank: int, stock: dict) -> dict:
    """1銘柄分のDiscord Embedを生成する。"""
    code = stock["ticker"].replace(".T", "")
    unit = "株" if TRADE_MODE == "fractional" else "株（単元）"
    return {
        "title": f"{RANK_MARKS[rank - 1]} {stock['name']}（{code}）",
        "color": 0x00C851,
        "fields": [
            {
                "name": "💴 前日終値 / 想定買付",
                "value": f"¥{stock['price']:,.0f} × {stock['shares']}{unit} = ¥{stock['cost']:,.0f}",
                "inline": False,
            },
            {
                "name": f"📋 ファンダメンタル {stock['f_score']}/6",
                "value": f"```\n{_fundamental_text(stock['f_detail'])}\n```",
                "inline": False,
            },
            {
                "name": f"📈 テクニカル {stock['tech_count']}/4",
                "value": f"```\n{_technical_text(stock['tech'])}\n```",
                "inline": False,
            },
            {"name": "🎯 利確", "value": f"¥{stock['take_profit']:,.0f}（+{TAKE_PROFIT_PCT:g}%）", "inline": True},
            {"name": "🛑 損切り", "value": f"¥{stock['stop_loss']:,.0f}（-{STOP_LOSS_PCT:g}%）", "inline": True},
        ],
        "footer": {"text": "⚠️ 情報提供のみ。最終判断はご自身で。"},
    }


def _funnel_text(stats: dict) -> str:
    return (
        f"対象{stats['total']} → 予算内{stats['affordable']} → "
        f"ファンダ{FUNDAMENTAL['min_f_score']}点以上{stats['f_pass']} → "
        f"上位{stats['candidates']} → テクニカル通過{stats['tech_pass']}"
    )


def _near_miss_text(near: list[dict]) -> str:
    if not near:
        return "なし"
    lines = []
    for n in near:
        code = n["ticker"].replace(".T", "")
        t = n["tech"]
        missing = [
            label for key, label in (
                ("trend_up", "トレンド"), ("macd_bull", "MACD"), ("rsi_ok", f"RSI{t['rsi_value']:.0f}"), ("volume_surge", "出来高"),
            ) if not t.get(key)
        ]
        lines.append(f"・{n['name']}（{code}）ファンダ{n['f_score']}/6 テク{n['tech_count']}/4 ─ 足りない: {'・'.join(missing)}")
    return "\n".join(lines)


def build_morning_payload(stocks: list[dict], stats: dict, near: list[dict], open_count: int = 0) -> dict:
    now_jst = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    header_desc = f"ファネル: {_funnel_text(stats)}"
    if open_count:
        header_desc += f"\n追跡中: {open_count}銘柄（夕方に経過報告）"

    if not stocks:
        return {
            "embeds": [{
                "title": f"📊 本日の推奨銘柄 [{now_jst}]",
                "description": "本日は条件を満たす銘柄なし。見送りが正解の日もあります。\n" + header_desc,
                "color": 0x888888,
                "fields": [{"name": "惜しかった銘柄", "value": _near_miss_text(near), "inline": False}],
            }]
        }

    embeds = [{
        "title": f"📊 本日の推奨銘柄 [{now_jst}]",
        "description": f"ファンダメンタル優先スクリーニング 上位{len(stocks)}銘柄\n{header_desc}",
        "color": 0x0099FF,
    }]
    embeds += [_build_embed(i, s) for i, s in enumerate(stocks, 1)]
    return {"embeds": embeds}


def send_payload(payload: dict, dry_run: bool = False) -> bool:
    """Discordへ送信する。dry_run なら送らずにログへ出す。"""
    if dry_run:
        logger.info("[dry-run] Discord送信をスキップ")
        return True
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.error("DISCORD_WEBHOOK_URL が未設定")
        return False
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Discord通知を送信しました")
        return True
    except requests.HTTPError as e:
        logger.error(f"Discord送信失敗（HTTP {e.response.status_code}）: {e.response.text[:200]}")
    except Exception as e:
        logger.error(f"Discord送信失敗: {e}")
    return False


def send_discord(stocks: list[dict], stats: dict, near: list[dict], open_count: int = 0, dry_run: bool = False) -> bool:
    return send_payload(build_morning_payload(stocks, stats, near, open_count), dry_run=dry_run)


def send_text(text: str, dry_run: bool = False) -> bool:
    return send_payload({"content": text}, dry_run=dry_run)
