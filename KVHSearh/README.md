# KVHSearch

原始碼全文 trigram 搜尋引擎。  
專為大型 UEFI / 韌體代碼庫設計，支援頻繁切換 git 分支。

---

## 目錄

1. [概述](#1-概述)
2. [系統需求](#2-系統需求)
3. [檔案結構](#3-檔案結構)
4. [演算法與設計](#4-演算法與設計)
5. [索引流程](#5-索引流程)
6. [搜尋流程](#6-搜尋流程)
7. [建立索引指令](#7-建立索引指令)
8. [搜尋指令](#8-搜尋指令)
   - [search](#search--精確-and-搜尋)、[prefix](#prefix--前綴匹配)、[substr](#substr--子字串匹配)、[glob](#glob--萬用字元匹配)、[fuzzy](#fuzzy--容錯搜尋)、[hex](#hex--二進位位元組模式搜尋)、[ext](#ext--副檔名--設定檔篩選)
9. [搜尋篩選器](#9-搜尋篩選器)
10. [管理指令](#10-管理指令)
11. [多版本索引](#11-多版本索引)
12. [GUI 圖形介面](#12-gui-圖形介面)
13. [跳過規則](#13-跳過規則)
14. [副檔名設定檔](#14-副檔名設定檔)
15. [--db-dir：外部索引儲存](#15---db-dir外部索引儲存)
16. [Git Hooks](#16-git-hooks)
17. [日常工作流程](#17-日常工作流程)
18. [效能說明](#18-效能說明)

---

## 1. 概述

KVHSearch 將原始碼檔案索引至本機 SQLite FTS5 trigram 資料庫，提供快速的命令列搜尋。適用於：

- 檔案數量龐大（10,000–100,000+）
- 頻繁切換 git 分支
- 搜尋速度要求快（典型 < 10 ms）
- 需要同時搜尋文字檔與二進位檔

**分層索引**架構：在 `main` 上建立一次完整索引，之後每個分支只需 diff 索引，`build_minor` 在大型 repo 上也只需數秒。

---

## 2. 系統需求

- Python 3.10+
- Git（必須在 `PATH` 中）
- 無第三方套件 — 僅使用標準庫（`sqlite3`、`hashlib`、`threading`、`subprocess`）

---

## 3. 檔案結構

```
KVHSearch/
  kvhsearch_core.py           ← 完整引擎（函式庫 + 組合 CLI）
  Build_Index_cli.py          ← 建立索引專用入口
  Search_cli.py               ← 搜尋專用入口
  KVHSearch_Gui.py            ← tkinter GUI 圖形介面
  build_index.py              ← 向後相容 shim → 委派至 core
  build_index_profiles.json   ← 可編輯的副檔名設定檔 + 跳過規則
  hooks/
    post-checkout             ← git hook：checkout 後自動 build_minor
    post-merge                ← git hook：merge 後自動 build_minor
```

**執行時產生（在 `--db-dir` 或 CWD）：**

| 檔案 | 說明 |
|---|---|
| `main_<label>.db` | 某版本的完整索引（如 `main_v08.db`） |
| `minor_<label>_<sha7>.db` | 依版本 + 分支 HEAD SHA 命名的差異索引 |
| `main_index.db` | 無 `--label` 時的舊式 main |
| `minor_<sha7>.db` | 無 `--label` 時的舊式 minor |
| `file_index.db` | 向後相容的單一索引 |

---

## 4. 演算法與設計

### 4.1 儲存 — SQLite FTS5 Trigram

檔案內容被拆成重疊的 3 字元序列（trigram）並存入 FTS5 虛擬表。

```
"OemDxe" → "Oem", "emD", "mDx", "Dxe"
```

查詢直接命中 trigram 索引，搜尋時無需逐檔掃描。

### 4.2 內容擷取

| 檔案類型 | 擷取方式 |
|---|---|
| 文字（UTF-8 / UTF-16 / Latin-1） | 完整解碼 |
| 二進位 | 擷取 ≥ 4 字元的 ASCII 字串；可選滑動視窗 hex token |

### 4.3 內容雜湊 — 三層捷徑

每次增量建立索引時，每個檔案依序嘗試最快的捷徑：

```
1. Git blob SHA1（僅追蹤檔案）
   ├─ 與快取 blob_sha 相同 → 跳過（零檔案讀取）
   └─ 不符 → 繼續

2. mtime 檢查（未追蹤檔案 / 非 git repo）
   ├─ mtime 未變 → 跳過
   └─ 已變 → 讀取檔案

3. BLAKE2b 內容雜湊
   ├─ 雜湊未變 → 跳過（僅更新 mtime）
   └─ 雜湊已變 → 重新索引
```

### 4.4 分層索引 — Minor DB 以 SHA 命名

Minor DB 檔名嵌入分支 HEAD SHA：

```
minor_<label>_<前7碼hex>.db   例：minor_v08_a3f8c12.db
```

- 同一 commit 的兩個分支自動共用同一個 minor DB
- 過期偵測：若 HEAD 移動，舊 minor DB 不再匹配；引擎會在搜尋時發出警告

### 4.5 執行緒模型

```
主執行緒
  │
  ├─ git ls-files / rglob     →  檔案清單
  ├─ git ls-files -s          →  blob SHA1 對應表
  │
  └─ ThreadPoolExecutor（N 個 worker）
       │
       │  每個 worker：
       │    讀取 + 雜湊 + 擷取內容
       │    不接觸 SQLite
       │
       └─▶  write_queue  ──▶  寫入執行緒（單一）
                                   │
                                   └─  SQLite INSERT / UPDATE（序列化）
```

`DEFAULT_WORKERS = min(32, cpu_count × 2)`

---

## 5. 索引流程

```
build_main / build_minor
        │
        ▼
  git ls-files ──────────────── 檔案清單（遵循 .gitignore）
        │
        ▼
  git ls-files -s ────────────── blob SHA1 對應表
        │
        ▼
  對每個檔案：
    ┌─ blob SHA1 與快取相同？ ──是──▶ touch（零檔案讀取）
    │
    否
    ├─ mtime 未變（未追蹤）？ ──是──▶ 跳過
    │
    否
    ├─ 讀取檔案
    ├─ BLAKE2b 雜湊與快取相同？ ──是──▶ touch
    │
    否
    ├─ 偵測文字 / 二進位
    ├─ 擷取內容
    └─▶  write queue  ──▶  SQLite INSERT OR REPLACE

  完成後：
    儲存 last_commit SHA + build_time 至 index_meta
    輸出摘要
```

**build_minor 額外步驟：**
```
  git diff --name-status <base_sha> <branch_head>
        │
        ├─ A / M 檔案  →  索引至 minor DB
        └─ D / R 檔案  →  插入 tombstones 表

  儲存：base_commit、branch_head、branch_name 至 minor index_meta
```

---

## 6. 搜尋流程

```
搜尋指令（search / prefix / substr / glob / fuzzy / hex / ext）
        │
        ▼
_get_active_conns()
        │
        ├─ minor_*_<HEAD_sha7>.db 存在？
        │       是 ──▶  TIERED 模式
        │                 primary   = minor DB
        │                 secondary = main DB
        │                 tombstones = minor.tombstones 的集合
        │
        ├─ minor DB 存在但 SHA 不符？
        │       是 ──▶  STALE MINOR 警告 + MAIN ONLY 回退
        │
        ├─ 無 minor DB，main_*.db 存在？
        │       是 ──▶  MAIN ONLY（警告：比索引領先 N 個 commit）
        │
        └─ 僅有 file_index.db？
                是 ──▶  LEGACY 模式
        │
        ▼
  FTS5 MATCH 查詢
        │
        ├─ 有 secondary DB？
        │     是 ──▶  合併結果、去重、過濾 tombstones
        ▼
  _apply_search_filters()
        │  --ext    → 僅保留符合的副檔名
        │  --path   → 僅保留路徑包含子字串的（OR）
        │  --exclude → 排除符合 fnmatch glob 的路徑
        │  (--grep 在下方逐行處理)
        ▼
  對每個匹配檔案：
        │
        ├─ 文字檔  ──▶  掃描檔案找匹配行 + grep 篩選
        │                 輸出：path(lineno) :行內容
        │
        └─ 二進位檔 ──▶  輸出：[BIN] path
```

---

## 7. 建立索引指令

### 入口

| 腳本 | 用途 |
|---|---|
| `Build_Index_cli.py` | 建立索引專用 CLI |
| `kvhsearch_core.py` | 組合版（建立 + 搜尋） |
| `build_index.py` | Shim — 委派至 `kvhsearch_core.py` |

---

### `build_main` — 完整基底索引

```powershell
python Build_Index_cli.py build_main <dir> [--workers N] [--hex] [--db-dir <path>] [--label <ver>]
```

將整個工作區索引至 `main_<label>.db`。重複執行為增量更新。

| 參數 | 預設 | 說明 |
|---|---|---|
| `<dir>` | 必填 | 要索引的根目錄 |
| `--workers N` | `cpu_count × 2` | 平行 worker 執行緒數 |
| `--hex` | 關閉 | 同時索引二進位檔的 hex token |
| `--db-dir <path>` | CWD | DB 檔案存放目錄 |
| `--label <ver>` | _(無)_ | 版本標籤 — 產生 `main_<ver>.db` / `minor_<ver>_*.db` |

**範例：**
```powershell
python Build_Index_cli.py build_main .
python Build_Index_cli.py build_main E:\Git\Cathaya --workers 16 --hex --label v08
python Build_Index_cli.py build_main . --db-dir D:\indexes\Cathaya --label v08
```

---

### `build_minor` — 分支差異索引

```powershell
python Build_Index_cli.py build_minor <dir> [--workers N] [--hex] [--db-dir <path>] [--label <ver>]
```

需要 `main_<label>.db`。比較目前分支 HEAD 與基底 commit 的差異。輸出 `minor_<label>_<sha7>.db`。

**範例：**
```powershell
git checkout feature/oem-dxe
python Build_Index_cli.py build_minor . --label v08
```

---

### `build_chain` — 全分支 Minor 掃描

```powershell
python Build_Index_cli.py build_chain <dir> [--workers N] [--hex] [--db-dir <path>] [--label <ver>]
```

列出所有本地分支（由新到舊），自動 checkout → `build_minor` → 恢復原始分支。未提交變更會自動 stash。執行前顯示預覽並提示 `y/N`。

---

### `build_history` — 逐 Commit 建立 Minor

```powershell
python Build_Index_cli.py build_history <dir> [--workers N] [--hex] [--db-dir <path>] [--label <ver>]
```

以 `--first-parent` 線性歷史，從 HEAD 回溯到 main 基底 commit。對每個 commit 建立 minor index。顯示 commit subject 預覽並提示 `y/N`。

---

### `build_promote` — 合併 Minor 至新 Main

```powershell
python Build_Index_cli.py build_promote --from <ver> --minor <sha7> --to <new_ver> [--db-dir <path>]
```

複製 `main_<from_ver>.db` 為新的 `main_<new_ver>.db`，套用 `minor_<from_ver>_<sha7>.db` 的變更（upsert 修改、刪除 tombstone），最後執行 `VACUUM` 回收空間。

**範例：**
```powershell
python Build_Index_cli.py build_promote --from v07 --minor 42ea613 --to v08

Git V07 V07_01 V07_02 V07_03 V08
V07_01min, V07_02min,  V07_03min V08_min 為該節點的 Minor-Index (對每個節點都做 每個minor 皆包含 07 到 07n 的變動 格式為minor_v07_SHA1.db)

python Build_Index_cli.py build_promote --from v07 --minor V08_min的SHA1 --to v08
這樣會把 v07_MAin 加入 07-08 的所有 Minor 並導出到 V08_Main(過多minor 會拖慢執行效率)

Minors     glob (ms)      find (ms)      glob+read (ms)
----------------------------------------------------
9          0.04           0.03           28.39
10         0.19           0.08           33.13
50         0.37           0.28           32.53
100        0.78           0.69           39.34
200        0.35           0.20           50.79
```

---

### `build` — 舊式單一索引

```powershell
python Build_Index_cli.py build <dir> [--workers N] [--hex]
```

寫入 `file_index.db`。無分層架構。

---

### `--hex` 旗標

索引二進位檔為重疊的 4 位元組 hex token 視窗。`hex` 搜尋模式必須。韌體 repo 約增加 20–40% 索引大小。

---

## 8. 搜尋指令

**入口：** `Search_cli.py`  
所有指令自動偵測 TIERED / MAIN ONLY / LEGACY 模式。

---

### `search` — 精確 AND 搜尋

```powershell
python Search_cli.py search <kw1> [kw2 ...] [--txt|--bin] [--no-sort] [--db-dir <path>]
```

所有關鍵字必須出現在檔案中。不分大小寫。

```powershell
python Search_cli.py search OemDxe
python Search_cli.py search OemDxe EFI_STATUS      # AND — 兩者必須都出現
python Search_cli.py search PeiCore --txt          # 僅文字檔
python Search_cli.py search GUID --bin             # 僅二進位檔
```

---

### `prefix` — 前綴匹配

```powershell
python Search_cli.py prefix <prefix> [--txt|--bin] [--no-sort]
```

```powershell
python Search_cli.py prefix gEfi
python Search_cli.py prefix AMI_ --txt
```

---

### `substr` — 子字串匹配

```powershell
python Search_cli.py substr <fragment> [--txt|--bin] [--no-sort]
```

≥ 3 字元使用 FTS5 trigram；較短使用 SQL `LIKE`。

```powershell
python Search_cli.py substr HandleEvent
```

---

### `glob` — 萬用字元匹配

```powershell
python Search_cli.py glob <pattern> [--txt|--bin] [--no-sort]
```

支援 `*`（任意序列）、`?`（單字元）、`[...]`（字元類別）。  
第一階段：對長度 ≥ 3 的字面段落做 FTS5 AND 預先篩選。  
第二階段：對每行逐行 regex 驗證。

```powershell
python Search_cli.py glob "Oem*Dxe"
python Search_cli.py glob "PcdGet[0-9][0-9]" --ext .c .h
python Search_cli.py glob "gEfi*Protocol" --path LenovoWsPkg
```

---

### `fuzzy` — 容錯搜尋

```powershell
python Search_cli.py fuzzy <keyword> [dist] [--txt|--bin] [--no-sort]
```

`dist` = 最大編輯距離，預設 `1`。  
dist=1：展開**刪除 + 替換**兩種鄰域（`_FUZZY_ALPHABET`：`a-z0-9_`）。  
dist≥2：加入 **Pigeonhole 分段**預先篩選（保證至少一段原文出現在 FTS5 中）。  
第二階段：對每個匹配行的各 token 以 Levenshtein 精確驗證。

支援 **C 複合運算式** — `A->B`、`A.B`、`A(B)` — 每行強制驗證結構順序。

```powershell
python Search_cli.py fuzzy inicialize              # 匹配 "initialize"（替換）
python Search_cli.py fuzzy inicialize 2
python Search_cli.py fuzzy "gProtocol->IsSupported" 1
python Search_cli.py fuzzy "PcdGet32(PcdDebugLevel)" 1
```

---

### `hex` — 二進位位元組模式搜尋

```powershell
python Search_cli.py hex <pattern> [pattern2 ...] [--no-sort]
```

需要以 `--hex` 建立的索引。多個 pattern = AND 邏輯。

```powershell
python Search_cli.py hex DEADBEEF
python Search_cli.py hex DE:AD:BE:EF
python Search_cli.py hex DEADBEEF CAFEBABE   # 兩者必須出現在同一檔案
```

---

### `ext` — 副檔名 / 設定檔篩選

```powershell
python Search_cli.py ext <profile|.ext> [...] [--no-sort]
python Search_cli.py ext --list
```

```powershell
python Search_cli.py ext c               # profile → .c .cpp .h .hpp
python Search_cli.py ext sdl             # profile → .sdl .ssp
python Search_cli.py ext c .py           # 混合 profile + 原始副檔名
python Search_cli.py ext --list          # 顯示設定檔 + 路徑
```

---

### 通用搜尋旗標

| 旗標 | 效果 |
|---|---|
| `--txt` | 僅文字檔 |
| `--bin` | 僅二進位檔 |
| `--no-sort` | 跳過字母排序（大量結果時更快） |
| `--db-dir <path>` | 從指定目錄讀取 DB |

---

## 9. 搜尋篩選器

在 FTS5 查詢之後套用。可任意組合。

| 旗標 | 引數 | 邏輯 | 說明 |
|---|---|---|---|
| `--ext` | `.c .h ...` | OR | 僅保留這些副檔名 |
| `--path` | `substr ...` | OR | 僅保留路徑包含子字串的 |
| `--exclude` | `glob ...` | OR | 排除符合 fnmatch glob 的路徑 |
| `--grep` | `term ...` | AND | 僅顯示同時包含所有 term 的行 |

**範例：**
```powershell
python Search_cli.py search OemDxe --ext .c .h
python Search_cli.py search OemDxe --path LenovoWsPkg
python Search_cli.py search OemDxe --exclude *.inf *.sdl
python Search_cli.py search OemDxe --grep EFI_STATUS
python Search_cli.py search OemDxe --ext .c --path LenovoWsPkg --exclude *Test* --grep TRACE
```

---

## 10. 管理指令

### `status` — 儀表板

```powershell
python Build_Index_cli.py status [<dir>] [--db-dir <path>] [--repo-dir <path>]
```

顯示所有 `main_*.db` 和 `minor_*.db` 的大小、檔案數、commit、過期狀態與搜尋模式。目前選擇的版本標記 `[*]`。

| 模式 | 說明 |
|---|---|
| `TIERED` | `minor_*_<sha7>.db` 匹配目前 HEAD — 完全準確 |
| `STALE MINOR` | Minor DB 存在但 HEAD 已移動 — 請執行 `build_minor` |
| `MAIN ONLY` | 此分支無 minor DB — 分支異動對搜尋不可見 |
| `LEGACY` | 僅有 `file_index.db` |
| `NO INDEX FOUND` | 找不到 DB 檔案 |

---

### `cleanup` — 移除孤立索引

```powershell
python Build_Index_cli.py cleanup [<dir>] [--db-dir <path>] [--repo-dir <path>]
```

刪除 `minor_*.db` 中已不存在 git 的 `branch_head` SHA 對應的檔案。

---

## 11. 多版本索引

`--label` 旗標啟用命名版本，可並存多個索引快照。

### 命名規則

| `--label` | Main DB | Minor DB |
|---|---|---|
| `v07` | `main_v07.db` | `minor_v07_<sha7>.db` |
| `v08` | `main_v08.db` | `minor_v08_<sha7>.db` |
| _(無)_ | `main_index.db` | `minor_<sha7>.db` |

### 典型版本工作流程

```powershell
# 1. 在 v07 tag 建立 main
git checkout v07
python Build_Index_cli.py build_main . --label v07 --db-dir D:\indexes\Cathaya

# 2. 建立 v07 歷史上的逐 commit minor
python Build_Index_cli.py build_history . --label v07 --db-dir D:\indexes\Cathaya

# 3. 將特定 minor 合併為 v08
python Build_Index_cli.py build_promote --from v07 --minor 42ea613 --to v08 --db-dir D:\indexes\Cathaya

# 4. 檢視所有版本
python Build_Index_cli.py status --db-dir D:\indexes\Cathaya
```

### 跨版本 Minor 偵測

搜尋時，引擎會尋找**任何** `minor_*_<sha7>.db` 匹配目前 HEAD 的檔案，不受版本標籤限制。這表示在 v07 建立的 minor，即使選擇 v08 也會被找到並用於分層搜尋。

---

## 12. GUI 圖形介面

`KVHSearch_Gui.py` 提供深色主題的 tkinter GUI，整合所有搜尋與管理功能。

### 啟動

```powershell
python KVHSearch_Gui.py [--db-dir <path>] [--repo-dir <path>]
```

或使用 `_pack_KVHSearch_Gui.bat` 打包的 EXE。

### 功能

- **搜尋列** — 模式選擇（search / prefix / substr / glob / fuzzy / hex / ext）
- **篩選器** — File types、`--path`、`--exclude`、`--grep`
- **版本下拉選單** — 選擇 `main_<label>.db`；按 `⟳` 重新整理
- **Config 選單** — IndexDB 路徑、File types、排序、Debug 模式切換
- **Manage DB 選單**：
  - List Main / List Minor — 顯示索引狀態
  - Build Main / Build Branch / Build SHA1 / Build Chain / Build History
  - Promote — 合併 minor 至新 main 版本
  - Delete DB — 勾選式對話框刪除選取的 DB 檔案
- **設定** 儲存至 `kvhsearch_gui.json`（db_dir、ext、path、exclude、grep）
- **分層搜尋** — 自動偵測 minor 並顯示過期警告

---

## 13. 跳過規則

在 `build_index_profiles.json` 的 `"skip"` 中設定。於索引時套用。

| 規則 | 預設 |
|---|---|
| `max_file_mb` | `64` |
| `folders` | `.git`、`build`、`Flash_Image_Tool`、`__pycache__`、`.vs`、`.vscode`、`node_modules` |
| `extensions` | `.exe .dll .lib .obj .pdb .zip .7z .bin .fd .rom .efi .pdf .chm .bmp .jpg .png .gif .ttf` 等（34 種） |
| `filenames` | `system.sys` |
| `patterns` (fnmatch) | `RomAlignment.*`、`iasl*.*`、`BiosGuardCryptoCon*.*` 等 |

編輯後重新執行 `build_main` 即可套用。

---

## 14. 副檔名設定檔

儲存於 `build_index_profiles.json`。首次執行時自動建立預設值。

```json
{
  "profiles": {
    "c":    [".c", ".cpp", ".h", ".hpp"],
    "sdl":  [".sdl", ".ssp"],
    "inf":  [".inf"],
    "dec":  [".dec"],
    "asm":  [".asm", ".inc", ".mac", ".equ"],
    "asl":  [".asl", ".asi", ".oem"],
    "vfr":  [".sd", ".vfi", ".vfr", ".hfr"],
    "py":   [".py"],
    "txt":  [".txt"],
    "uni":  [".uni"],
    "bat":  [".bat", ".cmd"],
    "mak":  [".mak"],
    "cfg":  [".cfg"],
    "dxs":  [".dxs"],
    "bin":  [".bin", ".rom", ".efi", ".fd"],
    "all":  []
  }
}
```

使用 `python Search_cli.py ext --list` 查看目前設定檔與路徑。

---

## 15. `--db-dir`：外部索引儲存

將 DB 檔案存放在 repo 外部 — 適用於：
- 保持工作區乾淨（不產生 `.db` 檔案）
- 同一 repo 的多個 checkout 共用索引目錄
- 將索引放在較快的磁碟

```powershell
# 建立至外部目錄
python Build_Index_cli.py build_main  E:\Git\Cathaya --db-dir D:\indexes\Cathaya --label v08
python Build_Index_cli.py build_minor E:\Git\Cathaya --db-dir D:\indexes\Cathaya --label v08

# 使用外部目錄搜尋
python Search_cli.py search OemDxe    --db-dir D:\indexes\Cathaya
python Build_Index_cli.py status      --db-dir D:\indexes\Cathaya
python Build_Index_cli.py cleanup     --db-dir D:\indexes\Cathaya
```

若目錄不存在會自動建立。

---

## 16. Git Hooks

每次切換分支或 pull 後自動執行 `build_minor`：

```powershell
# Windows
Copy-Item KVHSearch\hooks\post-checkout  .git\hooks\post-checkout
Copy-Item KVHSearch\hooks\post-merge     .git\hooks\post-merge
```

```bash
# Linux / macOS
cp KVHSearch/hooks/post-checkout  .git/hooks/post-checkout
cp KVHSearch/hooks/post-merge     .git/hooks/post-merge
chmod +x .git/hooks/post-checkout .git/hooks/post-merge
```

---

## 17. 日常工作流程

```powershell
# ── 首次設定（在 main 分支上執行） ──────────────────────────────────────────
cd E:\Git\Cathaya
python Build_Index_cli.py build_main . --label v08

# ── 切換到功能分支 ────────────────────────────────────────────────────────────
git checkout feature/oem-dxe
python Build_Index_cli.py build_minor . --label v08

# ── 檢查索引狀態 ──────────────────────────────────────────────────────────────
python Build_Index_cli.py status

# ── 搜尋 ──────────────────────────────────────────────────────────────────────
python Search_cli.py search OemDxe
python Search_cli.py search OemDxe --ext .c .h
python Search_cli.py search OemDxe --path LenovoWsPkg --grep EFI_STATUS
python Search_cli.py prefix gBS_
python Search_cli.py ext c
python Search_cli.py fuzzy inicialize

# ── 分支有新 commit 後 ────────────────────────────────────────────────────────
git commit -am "fix: ..."
python Build_Index_cli.py build_minor . --label v08   # 數秒

# ── main 更新後 ───────────────────────────────────────────────────────────────
git checkout main
git pull
python Build_Index_cli.py build_main . --label v09   # 新版本

# ── 維護 ──────────────────────────────────────────────────────────────────────
python Build_Index_cli.py cleanup
```

---

## 18. 效能說明

| 操作 | 典型時間 |
|---|---|
| `build_main` 首次（30,000 檔） | 30–90 秒 |
| `build_main` 增量（少數新 commit） | 1–5 秒 |
| `build_minor`（一般） | 1–10 秒 |
| `build_minor` 切換分支後無檔案變更 | < 1 秒（blob SHA1 捷徑） |
| 任何搜尋指令 | < 10 ms |

### Minor DB 數量 vs 搜尋額外開銷

每次搜尋只使用 **1 個** minor DB（匹配目前 HEAD 的那個）。
但當找不到精確匹配時（過期 / 該分支尚無 minor），`_get_active_conns()` 會
glob 所有 `minor_*.db` 並逐一開啟讀取以尋找過期匹配。此開銷隨 minor 數量線性增長：

| Minor 數量 | `glob` (ms) | `find` (ms) | `glob + 全部讀取` (ms) |
|-----------:|------------:|------------:|-----------------------:|
| 9          | 0.04        | 0.03        | 28                     |
| 10         | 0.19        | 0.08        | 33                     |
| 50         | 0.37        | 0.28        | 33                     |
| 100        | 0.78        | 0.69        | 39                     |
| 200        | 0.35        | 0.20        | 51                     |
| 326        | 0.49        | 0.28        | 59                     |

> 在 NVMe SSD 上量測，30,000 檔 repo（E:\Git\Cathaya）。每個 minor DB ≈ 48 KB。

**欄位說明：**

- **glob** — `Path.glob("minor_*.db")` 僅目錄列表
- **find** — `Path.glob("minor_*_<sha7>.db")` 針對性查找（正常路徑使用此方式）
- **glob + 全部讀取** — glob + 開啟每個 minor DB 讀取 `branch_name`（最差情況的過期偵測）

**重點：**

| 路徑 | 何時發生 | 開銷 |
|---|---|---|
| 正常（找到 minor） | HEAD 匹配某個 `minor_*_<sha7>.db` | **< 0.5 ms** 不受 minor 數量影響 |
| 過期（無匹配） | 目前 HEAD 無對應 minor | **~30 ms + 每個 minor 0.1 ms** |

**建議：** 定期執行 `cleanup` 移除孤立 minor。保持 minor 數量在 ~50 以下，過期偵測開銷可忽略不計（< 35 ms）。
