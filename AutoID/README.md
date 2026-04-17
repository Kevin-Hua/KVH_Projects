# AutoID — 自動打卡
> 適用於 CMS 的自動出勤工具。  
> 透過 Playwright 開啟 Google Chrome，載入已儲存的 30 天 Session，  
> 並在排定時間以隨機延遲點擊**線上打卡**。

---

## 基本資訊

| 項目 | 說明 |
|------|------|
| 應用名稱 | RunTime (`_APP_NAME`) |
| 版本 | 1.0.1 |
| 設定檔 | `user.env`（CMS 網址、帳號密碼、SMTP、Session 選擇器）|
| 語言 | Python 3.10+ |
| 介面 | tkinter — 深色主題，固定視窗大小 |
| 瀏覽器 | Playwright → Google Chrome (`channel="chrome"`) |
| Session | CMS「記住我 30 天」Cookie |
| 排程檔 | `autoid_schedule.json` |
| 選項檔 | `autoid_options.json` |
| Session 檔 | `autoid_session.json` + `autoid_session_ts.txt` |
| 記錄檔 | `log.log` |

---

## 首次執行

首次啟動（找不到 `user.env`）時，會自動開啟設定精靈，收集以下資訊：

| 欄位 | 說明 |
|------|------|
| **CMS 網址** | 例如 `https://cms.company.com/cms` |
| **帳號** | 登入 Email |
| **密碼** | 登入密碼 |
| **過期選擇器** | 登入頁可見的 CSS 選擇器（預設 `#email`）|
| **有效選擇器** | 儀表板上打卡按鈕的文字（預設 `線上打卡`）|
| **SMTP 設定** | 選填 — 留空則停用 Email 通知 |
| **通知收件人** | 最多 5 個 Email，以逗號分隔 |

精靈會在程式同目錄下建立 `user.env`。之後可直接編輯 `user.env` 修改任何欄位。

---

## user.env 設定說明

```ini
# 必填
CMS_URL=https://cms.company.com/cms
EMAIL=user@company.com
PASSWORD=your-password

# Session 選擇器（如非此 CMS 請依需求修改）
SESSION_EXPIRED_SEL=#email
SESSION_VALID_SEL=線上打卡

# SMTP Email 通知（選填 — 留空則停用）
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=sender@gmail.com
SMTP_PASS=app-password
NOTIFY_TO=you@example.com, colleague@example.com
```

> ⚠ `SESSION_EXPIRED_SEL` 必須是有效的 CSS 選擇器（如 `#email`），**請勿**填入 Email 地址。

---

## 介面總覽

```
┌──────────────────────────────────────────────┐
│  RunTime                          v1.0.1     │
│  ──────────────────────────────────────────  │
│  11:03                                       │  ← 時:分（每分鐘對齊更新）
│  Session 於 30 天後到期 (2026-05-10)         │  ← Session 倒數
│  🔒 下次觸發：Fri 08:06  (21 小時後)         │  ← 下次排程
│                                              │
│  [ 執行 ]   [ 取消 ]   [ 🔒 ]                │  ← 操作按鈕
│          [ 🔑 Auth Test ]                    │  ← Session 有效性檢查
│                                              │
│  ┌ 設定排程 ──────────────────────────────┐  │
│  │  時段 1：08 : 06                        │  │
│  │  時段 2：18 : 13                        │  │
│  │             [ ● ON ]                   │  │  ← 排程啟用切換
│  │  ◀  2026 年 4 月  ▶                    │  │
│  │  日 一 二 三 四 五 六                   │  │  ← 日曆（日為首）
│  │   綠=ON  灰=OFF  紅=週末/ON             │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  [● ON] 儲存記錄至 log.log                   │
│  ┌ 捲動記錄框 ───────────────────────────┐   │
│  └───────────────────────────────────────┘   │
└──────────────────────────────────────────────┘
```

**自動執行流程：**

1. 程式啟動 → 載入排程、選項、鎖定狀態
2. 排程器監聽 `datetime.now()` 等待時段吻合
3. 吻合 → 計算隨機延遲 → 開始倒數
4. 倒數歸零 → 以 daemon 執行緒開啟 Chrome → 點擊線上打卡
5. 瀏覽器隨機保持開啟 3–10 分鐘 → 自動關閉
6. 排程器重新等待下一個時段

---

## 主要常數

| 常數 | 值 | 說明 |
|------|-----|------|
| `SESSION_DAYS` | `30` | Session 過期天數門檻 |
| `PRE_DELAY_MANUAL` | `30 秒` | 手動執行前的倒數時間 |
| `CLOSE_MIN` | `(180, 600) 秒` | 點擊後瀏覽器隨機保持開啟的範圍 |
| `POLL_INTERVAL_MS` | `30 000 ms` | 未鎖定模式下的排程器輪詢間隔 |

---

## 選項說明（`autoid_options.json`）

| 鍵值 | 預設 | 說明 |
|------|------|------|
| `rand_enabled` | `true` | 啟用隨機延遲 |
| `rand_min_min` | `1` | 最短延遲（分鐘）|
| `rand_max_min` | `10` | 最長延遲（分鐘）|
| `auto_renew_enabled` | `true` | Cookie 即將到期時自動更新 Session |
| `auto_renew_days` | `7` | 距到期幾天時觸發自動更新 |
| `close_on_success` | `false` | 打卡成功後自動關閉程式 |
| `close_on_success_secs` | `5` | 關閉程式前等待秒數 |
| `log_enabled` | `true` | 寫入 `log.log` |
| `startup_check_enabled` | `true` | 啟動時檢查 Session |
| `startup_check_min` | `3` | 距時段幾分鐘前執行預先檢查 |
| `browser_close_enabled` | `true` | 打卡後關閉瀏覽器 |
| `browser_close_secs` | `10` | 點擊後保持瀏覽器開啟秒數 |
| `auto_close_timeout_enabled` | `false` | 逾時後自動關閉瀏覽器 |
| `auto_close_timeout_min` | `15` | 逾時分鐘數 |
| `2fa_timeout_secs` | `8` | 登入後等待儀表板出現的秒數（超過即視為需要雙重驗證）|

---

## 演算法說明

### 隨機延遲（`_calc_delay`）

```
rand_enabled = True
    delay = randint(min_min, max_min) × 60  +  randint(0, 59)   秒
    例：min=1, max=10  →  範圍：60 秒 … 659 秒

rand_enabled = False
    delay = randint(0, 59)   秒
```

延遲在**偵測到時段吻合的當下**計算，而非提前。

### 下次時段掃描（`_next_fire_dt`）

```
slots = [(t1h,t1m), (t2h,t2m)]

for days_ahead = 0 … 7:
    d = today + days_ahead
    if not is_day_on(d): 略過

    for (h, m) in sorted(slots):          ← 較早的時間優先
        dt = datetime(d, h, m)
        if dt > now: return dt

return None   (排程關閉或 8 天內無啟用日)
```

`is_day_on(d)` → 優先查 `overrides[d.isoformat()]`，若無則依平日（週一至週五）。

### 時段偵測（`_tick_schedule`）

```
清除早於今日的 _fired_keys

if 排程啟用 AND 今日為 ON：
    key = "YYYY-MM-DD HH:MM"
    if key 不在 _fired_keys：
        for (h, m) in slots:
            if now.hour == h AND now.minute == m:
                _fired_keys.add(key)
                _do_fire()
                break

重新排程：
    鎖定   → after(至下一個 :00 的毫秒數)   對齊分鐘邊界
    未鎖定 → after(30 000)                 每 30 秒
```

`_fired_keys` 為 `set[str]`，防止同一時段在同一分鐘內重複觸發。

---

## 流程圖

### 啟動流程

```
python AutoID.py
    └─ 找不到 user.env？
          是 → _run_setup_wizard() → 寫入 user.env → 重新載入
          否 → _load_credentials() → EMAIL, PASSWORD, CMS_URL, SEL_EXPIRED, SEL_VALID
                App.__init__()
                      ├─ _load_sched()  → {t1h, t1m, t2h, t2m, enabled, locked, overrides}
                      ├─ _load_opts()   → {rand_enabled, min_min, max_min, 2fa_timeout_secs …}
                      ├─ 建立 UI 元件
                      ├─ _tick_clock()       ───► 永久循環（分鐘對齊 UI 更新）
                      ├─ _tick_schedule()    ───► 永久循環（排程器輪詢）
                      └─ sched["locked"]==True ?
                               是 → _toggle_lock()（恢復鎖定狀態 + 停用控制項）
```

### 排程觸發鏈

```
_tick_schedule()
    │  時段吻合
    ▼
_do_fire()
    ├── delay = _calc_delay(opts)
    ├── exec_dt = now + delay
    └── _on_execute(pre_delay=delay, scheduled=True)
              │
              └── _run_countdown()  ◄────────────────────┐
                        │                               │
                        ├── 取消？ → _reset_btns()      │
                        ├── 剩餘 ≤ 0？ → _launch()      │
                        └── 更新標籤 → after(Δms) ──────┘
                                │
                                ▼
                        _launch()  [daemon 執行緒]
                                │
                                ▼
                        clock_in(log, set_cd, stop, close_secs, tfa_timeout_ms)
                            ├── 開啟 Chrome（可見視窗）
                            ├── 載入 autoid_session.json（若存在）
                            ├── 導向 CMS_URL
                            ├── SEL_EXPIRED 可見？
                            │       是 → 填入帳號 + 密碼 → 送出
                            │             等待 SEL_CLOCKIN（tfa_timeout_secs）
                            │               成功 → 儲存 Session
                            │               逾時 → 雙重驗證模式：
                            │                       寄送警示 Email
                            │                       無限等待 SEL_CLOCKIN
                            ├── 等待 SEL_CLOCKIN 按鈕（15 秒逾時）
                            ├── 點擊
                            ├── 隨機等待 close_secs（每 1 秒檢查停止旗標）
                            └── browser.close()
```

### Auth Test 流程

```
使用者點擊 🔑 Auth Test
    │
    ▼
_test_auth()  [daemon 執行緒]
    ├── 無頭模式啟動 Chrome
    ├── 載入 autoid_session.json
    ├── 導向 CMS_URL
    ├── SEL_EXPIRED 可見？
    │       True  → expired = True
    │       False → expired = False
    ├── browser.close()
    └── _auth_done(expired)
              None  → ⚠ 無法連線 CMS（深紅色按鈕）
              True  → ❌ Session 已過期（紅色按鈕）
              False → ✅ Session 有效（綠色按鈕）
              after(5 000 ms) → 重置按鈕
```

### Session 自動更新

```
_bg_session_check()  [每次排程時段前 N 分鐘執行]
    ├── 導向 CMS_URL（無頭模式）
    ├── 等待 SEL_PRECHECK（= SEL_EXPIRED 或 SEL_CLOCKIN）
    ├── SEL_EXPIRED 可見？
    │       是 → 寄送警示 Email「Session 已過期，請在下次打卡前更新」
    │       否 → 檢查 Cookie 剩餘天數 → 即將到期？
    │                   是且 auto_renew_enabled → _auto_renew_session()
    └── browser.close()
```

### 鎖定切換

```
使用者點擊 🔒 / 🔓
    ├── 翻轉 _lock_var
    ├── 鎖定=True  → 停用：時間格、ON/OFF 鈕、日期鈕、選項選單
    │   鎖定=False → 啟用：以上全部
    ├── 更新按鈕外觀（顏色 + 圖示）
    ├── 更新視窗標題（加 / 移除 🔒 前綴）
    ├── 儲存 {locked} → autoid_schedule.json
    └── _tick_schedule()
              鎖定   → 等到下一個 :00 邊界
              未鎖定 → 30 秒後
```

---

## 排程器行為比較

| 項目 | 未鎖定 🔓 | 鎖定 🔒 |
|------|-----------|--------|
| 輪詢間隔 | 30 秒（固定）| 對齊下一個 `HH:MM:00` |
| 時段偵測精度 | 時段後 0–30 秒 | 時段後 < 1 秒 |
| 隨機延遲計算 | 偵測當下 | 偵測當下 |
| 倒數更新 | 每 1 秒 | 分鐘對齊 |
| UI 控制項 | 可編輯 | 全部停用 |
| 下次觸發圖示 | 🔄 | 🔒 |
| 鎖定狀態持久化 | ✅ | ✅ |

---

## Email 通知

`SMTP_USER`、`SMTP_PASS`、`SMTP_HOST`、`NOTIFY_TO` 全部設定後才會發送通知。

| 觸發情境 | 主旨 |
|----------|------|
| 打卡時需要雙重驗證 | `[AutoID] ⚠ 2FA required — action pending` |
| 未設定密碼 | `[AutoID] ⚠ Manual login required — action pending` |
| 預先檢查發現 Session 過期 | `[AutoID] ⚠ Session expired — renew before next clock-in` |

---

## 已知問題

| # | 問題 | 說明 |
|---|------|------|
| 1 | **電腦休眠** | `after()` 在休眠期間暫停，恢復後可能延遲。請保持電腦不休眠。 |
| 2 | **延遲可能為 0 秒** | 停用隨機延遲時，`randint(0,59)` 可能回傳 0，瀏覽器在整點觸發。 |
| 3 | **Session 過期** | 預先檢查會寄送警示 Email。建議每月執行 Auth Test 或 Renew Auth。 |
| 4 | **不支援國定假日** | 日曆預設平日=ON，假日需手動切換。 |
| 5 | **僅支援 2 個時段** | 若每天只打一次卡，將兩個時段設為相同時間即可。 |
| 6 | **雙重驗證逾時太短** | 網路較慢時可調高 `autoid_options.json` 中的 `2fa_timeout_secs`。 |

---

## 檔案說明

| 檔案 | 用途 |
|------|------|
| `AutoID.py` | 主程式 |
| `user.env` | 帳號密碼 + CMS 網址 + Session 選擇器 + SMTP — ⚠ 請勿上傳版控 |
| `autoid_schedule.json` | `{t1h, t1m, t2h, t2m, enabled, locked, overrides:{}}` |
| `autoid_options.json` | 所有選項，包含 `2fa_timeout_secs` |
| `autoid_session.json` | Playwright 儲存狀態 — ⚠ 包含登入 Cookie，請勿上傳版控 |
| `autoid_session_ts.txt` | 上次儲存 Session 的 ISO 日期 |
| `log.log` | 附加模式的時間戳記錄 |
| `Rewolf.ico` | 視窗圖示 |

---

## 注意事項

- **首次執行：** 精靈會在找不到 `user.env` 時自動開啟。填入 CMS 網址、帳號、密碼，以及選填的 SMTP 設定，精靈會自動建立 `user.env`。
- **Session 更新：** 建議每月執行 Auth Test。若已過期，請點擊「Renew Auth」重新驗證。
- **雙重驗證：** 若打卡時需要雙重驗證，瀏覽器會保持開啟。請手動完成驗證（勾選「記住我 30 天」）。若已設定 SMTP，會自動寄送通知信。
- **鎖定模式：** 離開前按 🔒 進入鎖定模式，防止誤改排程。鎖定狀態重啟後仍會保留。
- **記錄檔：** 旁邊的綠色 **● ON** 鈕控制是否寫入 `log.log`，程式內的記錄框無論如何都會顯示訊息。

---

## 打包為 EXE

```bat
_pack.bat
```

執行 `python ..\_gen_version.py AutoID` → `pyi-makespec` → `pyinstaller`。  
目標電腦須另行安裝 Playwright Chromium：`playwright install chromium`。
