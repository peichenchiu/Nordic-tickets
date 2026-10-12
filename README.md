# 芬蘭／挪威／瑞典機票每日監控

每天台灣時間 **中午 12:00 左右** 由 GitHub Actions（`.github/workflows/nordic.yml`）到 Google Flights 查詢，寄一封 Gmail 報告：

- 行程：赫爾辛基 HEL、奧斯陸 OSL、斯德哥爾摩 ARN 任兩個一進一出（多個城市），共 6 種走法
  - 例：台北 TPE → 赫爾辛基 HEL ……（自行陸路／渡輪）…… 斯德哥爾摩 ARN → 台北
- 出發日：2027-07-18 ～ 2027-08-10；整趟 16～21 天（最晚 8/31 回到台灣）
- 乘客：2 位成人 + 1 位兒童（2～11 歲），經濟艙，每段最多轉機 1 次
- **含託運行李**：Google Flights 這類行程只能篩手提行李，所以改用航空公司規定判斷——只列長程經濟艙基本票就含託運行李的航空（阿聯酋、卡達、長榮、華航、國泰、新加坡、泰航、星宇）。芬航、KLM、法航、漢莎集團、SAS、英航等自己開的票最便宜票種常不含託運行李，不列入；華航、長榮接這些航空的聯程票依華航／長榮規定含託運。訂票時仍請確認票種的行李額度
- **排除土耳其航空**（`EXCLUDE_AIRLINES`）
- **只看傳統航空**：去程、回程都排除廉價航空（Norwegian、Scoot、AirAsia、Jetstar、VietJet 等）
- 每組日期先選去程最便宜的傳統航空班次，再選回程最便宜的，讀取全家含稅總價
- 共 864 組（24 個出發日 × 6 種天數 × 6 種走法），每次約 2 小時；排程 09:30 開始，10:00 有備援

信件內容：
- **今日最低價**：最便宜的一組（含連結），再列 9 組便宜日期（只列日期與價格）
- **各航空公司最低價**：上面含託運行李的航空中有查到的，各列最低一組（含連結，實際點選確認過）
  ＋另外 5 組日期（去程列表上的估計價）
- 與昨天／歷史最低比較
出現以下情況時，主旨會加上「🔥特別推薦！」，信件開頭列出那一組：

- 比之前查到的歷史最低價還便宜
- （選用）全家總價低於 `THRESHOLD_TWD`；目前沒有設定（0）

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
| `FINLAND_AIRPORT` / `NORWAY_AIRPORT` / `SWEDEN_AIRPORT` | HEL / OSL / ARN | 例如改成羅瓦涅米 RVN、特羅姆瑟 TOS |
| `DEPART_START` / `DEPART_END` | 2027-07-18 / 2027-08-10 | 出發日範圍 |
| `TRIP_DAYS_MIN` / `TRIP_DAYS_MAX` | 16 / 21 | 整趟天數 |
| `MAX_STOPS` | 1 | 每段最多轉機次數 |
| `REQUIRE_BAGS` | 1 | 0 = 不排除可能不含託運行李的航空 |
| `EXCLUDE_AIRLINES` | Turkish Airlines | 不想搭的航空（英文名稱，逗號分隔）；空白 = 不排除 |
| `THRESHOLD_TWD` | 0 | 全家總價低於此就特別推薦；0 = 不用門檻 |
| `WORKERS` | 6 | 同時查幾組 |
