# stock-alert

日本株（日経225主要銘柄）を毎朝スクリーニングして Discord に通知し、推奨銘柄をその後10営業日追跡して成績を記録する。

## 流れ

| 時刻（JST・平日） | ジョブ | やること |
|---|---|---|
| 08:00 | 朝便 `stock_alert.main` | 全銘柄取得 → ファンダ6項目で足切り → テクニカル4シグナル → 上位3銘柄を通知 → `data/positions.json` に追跡登録 |
| 16:15 | 夕便 `stock_alert.evening` | 追跡中銘柄を終値で更新 → 利確(+5%)/損切り(-3%)/期限(10日)で決着 → `data/results.csv` に記録 → 経過と累計成績を通知 |

GitHub Actions（`.github/workflows/daily_alert.yml`）で自動実行。`data/` はリポジトリにコミットして蓄積する。

## スクリーニング

**1st pass ファンダメンタル（6項目・3点以上で候補）**
PER 5〜20倍 / PBR 1.5以下 / ROE 8%以上 / 配当利回り 1.5%以上 / 売上成長 0%以上 / 自己資本比率 30%以上

**2nd pass テクニカル（4シグナル・2つ以上で通過）**
上昇トレンド（終値>MA25 かつ MA5>MA25）/ MACD>シグナル / RSI 40〜65 / 出来高 20日平均の1.3倍以上
直近5日以内のゴールデンクロス・MACDクロスは「🆕」としてランク加点。

閾値は `stock_alert/config.py` にまとめてある。

## 売買単位

既定は `TRADE_MODE = "fractional"`（1株単位・S株/かぶミニ想定）。予算10万円 × 1銘柄40%以内で株数を自動算出する。
100株単位で運用するなら `"lot"` にする（`MAX_STOCK_PRICE` 以下の銘柄しか対象にならない点に注意）。

## セットアップ

```
pip install -r requirements.txt
cp .env.example .env   # Discord Webhook を記入
python -m stock_alert.main --dry-run -v     # 通知も保存もしない確認実行
python -m stock_alert.evening --dry-run
```

GitHub Actions の Secrets:

- `DISCORD_WEBHOOK_URL` 必須
- `JQUANTS_MAIL_ADDRESS` / `JQUANTS_PASSWORD` 任意（J-Quants で自己資本比率・売上成長率を上書き。無ければ yfinance のみ）

## データ

- `data/positions.json` 追跡中ポジション（最大3件）
- `data/results.csv` 決着済みの成績（推奨日・決着日・変動率・結果 TP/SL/TIME・想定損益）

## 注意

情報提供のみ。売買判断は自己責任で。
