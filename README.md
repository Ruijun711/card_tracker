# UA Card Tracker

UNION ARENA（UA）集換式卡牌的多系列圖鑑、店家行情、收購價與比價工具。以 Flask + SQLite 建置，提供公開前台與受密碼保護的管理後台。

## 功能

### 前台
| 路徑 | 說明 |
| --- | --- |
| `/` | 卡片圖鑑：依作品篩選、以卡名／卡號搜尋，顯示卡圖、效果與觸發效果 |
| `/yuyutei` | 悠悠亭售價行情：依作品與商品分類篩選，含歷史價格 |
| `/buyback` | 悠悠亭收購價：顯示收購中卡片、收購價與歷史 |
| `/recommendations` | 推薦卡片：保值候選、增值動能、親筆簽／稀有收藏候選（規則篩選，非投資建議） |
| `/prices/compare` | Dorasuta 多店價格比較：同卡號／稀有度／卡況的最高與最低報價 |

所有價格以日圓顯示，並可輸入「換算除數 x」以「日圓 ÷ x」即時顯示台幣參考價（設定存於瀏覽器）。

### 管理後台（`/admin`）
- 概覽與資料更新：同步圖鑑、更新各商品分類的售價／收購價（每分類至少間隔 60 秒）
- 圖鑑與效果：自訂卡名、效果文字，來源同步不會覆蓋
- 卡圖管理：上傳／替換圖鑑與店家商品卡圖（JPG / PNG / WebP，最大 5 MB）
- Dorasuta 全系列報價：匯入已儲存的 HTML 或頁面文字
- 收藏卡研究資料：登錄版本類型、真偽核對、卡況、發行量與成交參考

## 支援作品

鏈鋸人、咒術迴戰、鬼滅之刃、全職獵人、BLEACH 死神、刀劍神域、勝利女神：妮姬、反叛的魯路修、命運石之門（共 9 個作品、21 個商品分類，定義於 `series_registry.json`）。

## 安裝與執行

需求：Python 3.10 以上。

```bash
python -m pip install -r requirements.txt
```

設定必要的環境變數後啟動：

```bash
# macOS / Linux
export CARD_TRACKER_SECRET_KEY="請換成隨機長字串"
export CARD_TRACKER_ADMIN_PASSWORD="你的後台密碼"
python app.py
```

```powershell
# Windows PowerShell
$env:CARD_TRACKER_SECRET_KEY = "請換成隨機長字串"
$env:CARD_TRACKER_ADMIN_PASSWORD = "你的後台密碼"
python app.py
```

開啟 http://127.0.0.1:5000 。設定 `FLASK_DEBUG=1` 可啟用除錯模式。

首次啟動會自動建立 `union_arena.db`，並從隨附的 JSON 快照載入初始資料；之後重啟不會覆寫既有歷史紀錄。

## 專案結構

```
app.py                 Flask 進入點、登入、CSRF、後台概覽
database.py            SQLite 連線
series.py              作品／商品分類註冊與篩選
catalog.py             卡片圖鑑與卡圖管理
yuyutei.py             悠悠亭售價擷取
buyback.py             悠悠亭收購價擷取
dorasuta.py            Dorasuta 報價匯入
comparisons.py         多店價格比較
recommendations.py     推薦卡片規則
collectors.py          收藏卡研究資料
source_fetch.py        來源擷取與頻率限制
card_image_match.py    卡圖對應
*_seed.json            初始資料快照
series_registry.json   作品與商品分類清單
dorasuta_sources.json  Dorasuta 分類對應
templates/             Jinja2 模板
static/                CSS、JS 與手動上傳的卡圖
test_*.py              單元測試
```

## 測試

```bash
python -m unittest test_current.py test_market.py test_comparisons.py test_collectors.py test_dorasuta_series.py
```

## 資料來源與限制

- 圖鑑：rugiacreation.com
- 行情與收購：yuyu-tei.jp
- 比價：dorasuta.jp

隨附價格為擷取時的快照，並非即時價格。程式不會繞過登入或網站驗證；來源異常、逾時或資料不完整時會取消該次更新並保留原有資料。推薦清單為程式篩選規則，不構成投資建議或保值保證。

詳細的版本更新紀錄見 [README_CURRENT.md](README_CURRENT.md)。
