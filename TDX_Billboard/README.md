# TDX Billboard — 台鐵時刻懸浮視窗

半透明置頂小視窗，即時顯示指定起訖站最近數班台鐵列車，並附帶兩地降雨資訊。

---

## 功能特色

- 顯示目前時刻後最近 N 班列車（車次、種別、發車時間）
- 半透明置頂，不干擾其他視窗操作
- 可拖曳移動，位置自動記憶
- 即時顯示起訖站 10 分鐘 / 1 小時降雨量（來源：中央氣象署）
- 支援歷史起訖站紀錄，快速切換常用路線
- 可設定出發前提醒時間（N 分鐘前提示）及提醒時段
- 所有設定儲存於 `tdx_billboard.ini`，下次啟動自動套用

---

## 系統需求

- Windows 10 / 11
- 執行 EXE：無需安裝 Python
- 執行原始碼：Python 3.11+，需安裝以下套件

```
pip install requests
```

---

## API 金鑰取得方式

程式需要兩組 API 金鑰，請分別至以下平台註冊：

### TDX（台灣交通部運輸資料流通服務）
1. 前往 https://tdx.transportdata.tw/ 註冊帳號
2. 登入後至「會員中心」→「資料服務」→「API 金鑰管理」
3. 建立應用程式，取得 `client_id` 與 `client_secret`

### CWA（中央氣象署開放資料平台）
1. 前往 https://opendata.cwa.gov.tw/ 註冊帳號
2. 登入後至「會員專區」→「API 授權碼」
3. 複製授權碼（格式為 `CWA-XXXX-...`）

---

## 金鑰設定方式（三選一）

### 方法一：環境變數（推薦）

```powershell
[Environment]::SetEnvironmentVariable("TDX_CLIENT_ID",     "your-client-id",     "User")
[Environment]::SetEnvironmentVariable("TDX_CLIENT_SECRET", "your-client-secret", "User")
[Environment]::SetEnvironmentVariable("CWA_APIKEY",        "CWA-XXXX-...",       "User")
```

設定後重新啟動程式即生效。

### 方法二：填寫 `TDX_Billboard.key`

開啟 `TDX_Billboard.key`，在 `[Credentials]` 區段填入金鑰：

```ini
[Credentials]
tdx_client_id     = your-client-id
tdx_client_secret = your-client-secret
cwa_apikey        = CWA-XXXX-...
```

### 方法三：填寫 `tdx_billboard.ini`

同上格式，加入 `[Credentials]` 區段至 `tdx_billboard.ini`。

> **注意：** 環境變數優先級最高；`TDX_Billboard.key` 次之；`.ini` 最低。

---

## 使用方式

1. 執行 `TDX_Billboard.exe`
2. 首次執行時選擇起站與迄站，按確定
3. 視窗即顯示最近班次與降雨資訊
4. **拖曳標題列**可移動視窗
5. **右鍵點擊視窗**可開啟選單，進行設定或切換路線

---

## 自行打包

需安裝 Python 與 PyInstaller：

```
pip install pyinstaller
```

於 `TDX_Billboard` 資料夾執行：

```
_pack.bat
```

打包流程會自動：
1. 從原始碼讀取版本號，更新 `version_info.txt`
2. 重新產生 `TDX_Billboard.spec`
3. 執行 `pyinstaller --clean` 輸出 `dist\TDX_Billboard.exe`

---

## 檔案說明

| 檔案 | 說明 |
|---|---|
| `TDX_Billboard.py` | 主程式 |
| `TDX_Billboard.key` | API 金鑰範本（請勿提交含金鑰的版本） |
| `TDX_Billboard.spec` | PyInstaller 打包設定 |
| `_pack.bat` | 一鍵打包腳本 |
| `_gen_version.py` | 自動更新 `version_info.txt` |
| `version_info.txt` | Windows EXE 版本資訊（自動產生） |
| `tdx_billboard.ini` | 執行時設定檔（自動產生） |

---

## 授權

© 2026 KVH
