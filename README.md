# 芬蘭／挪威機票每日監控

每天台灣時間 **中午 12:00 左右** 由 GitHub Actions（`.github/workflows/nordic.yml`）到 Google Flights 查詢，寄一封 Gmail 報告：

- 行程：一進一出（多個城市），兩種走法都查
  - A：台北 TPE → 赫爾辛基 HEL ……（自行陸路／渡輪）…… 奧斯陸 OSL → 台北
  - B：台北 TPE → 奧斯陸 OSL …… 赫爾辛基 HEL → 台北
- 出發日：2027-07-18 ～ 2027-08-10；整趟 16～21 天（最晚 8/31 回到台灣）
- 乘客：2 位成人 + 1 位兒童（2～11 歲），經濟艙，每段最多轉機 1 次
- **含託運行李**：Google Flights 這類行程只能篩手提行李，所以改用航空公司規定判斷——排除最便宜票種常不含託運行李的航空（芬航、KLM、法航、漢莎集團、SAS、英航等），只留長程經濟艙基本票就含託運行李的航空（土耳其、阿聯酋、卡達、長榮、華航、國泰等）。訂票時仍請確認票種的行李額度
- **只看傳統航空**：去程、回程都排除廉價航空（Norwegian、Scoot、AirAsia、Jetstar、VietJet 等）
- 每組日期先選去程最便宜的傳統航空班次，再選回程最便宜的，讀取全家含稅總價
- 共 288 組（24 個出發日 × 6 種天數 × 2 種走法），每次約 25 分鐘；排程 11:15 開始，11:45 有備援

信件內容：今日最便宜 10 組、兩種走法各自最低、每個出發日的最低價、與昨天／歷史最低比較。
出現以下情況時，主旨會加上「🔥特別推薦！」，信件開頭列出那一組：

- 全家總價低於 NT$85,000（`THRESHOLD_TWD`），或
- 比之前查到的歷史最低價還便宜

每天的最低價記錄在 repo 中標籤為 `nordic-history` 的 issue（自動更新，請勿關閉）。

## 設定 Gmail 通知

1. 取得 Gmail「應用程式密碼」：到 <https://myaccount.google.com/apppasswords> 建立一組（名稱填 `nordic-tickets`），複製 16 碼密碼。
   需要先開啟兩步驟驗證。紐航 repo 用的那組密碼也可以直接沿用。
2. 這個 repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**，新增：

   | Name | Secret |
   |---|---|
   | `SMTP_USER` | 你的 Gmail 地址 |
   | `SMTP_PASSWORD` | 16 碼應用程式密碼 |

   （選用）`NOTIFY_EMAIL`：想寄到別的信箱時再加。
3. 測試：**Actions** → **Daily Finland/Norway fare check** → **Run workflow** → 勾選「只寄一封測試信」→ **Run workflow**。
手動測試：Actions → **Daily Finland/Norway fare check** → **Run workflow**（`limit` 填 `6` 可快速試跑；勾選「只寄一封測試信」可測 Gmail）。

> Google Flights 約只開放 11 個月內的航班；8 月底回程的日期若還沒開放，會先算在「沒有符合條件的班次」，開放後自動納入。

修改 `check_nordic.py` 開頭或在 workflow 加 `env` 調整：

| 變數 | 預設 | 說明 |
|---|---|---|
| `FINLAND_AIRPORT` / `NORWAY_AIRPORT` | HEL / OSL | 例如改成羅瓦涅米 RVN、特羅姆瑟 TOS |
| `DEPART_START` / `DEPART_END` | 2027-07-18 / 2027-08-10 | 出發日範圍 |
| `TRIP_DAYS_MIN` / `TRIP_DAYS_MAX` | 16 / 21 | 整趟天數 |
| `MAX_STOPS` | 1 | 每段最多轉機次數 |
| `REQUIRE_BAGS` | 1 | 0 = 不排除可能不含託運行李的航空 |
| `THRESHOLD_TWD` | 85000 | 全家總價低於此就特別推薦 |
| `WORKERS` | 6 | 同時查幾組 |
