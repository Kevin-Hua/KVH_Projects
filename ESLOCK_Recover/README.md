# ESLock & KVH Locker — 安全白皮書

> 本文件為 ESLock Encryptor / Decryptor 與 KVH Locker 的完整安全白皮書，描述其系統架構、加密模型、威脅面分析與已知限制。

---

## 目錄

1. [概覽](#概覽)
2. [系統架構](#系統架構)
3. [加密模型](#加密模型)
4. [ESLock 相容性說明](#eslock-相容性說明)
5. [部分加密機制](#部分加密機制)
6. [ESLock Recovery 工具](#eslock-recovery-工具)
7. [KVH Locker 設計理念](#kvh-locker-設計理念)
8. [方案比較](#方案比較)
9. [STRIDE 威脅模型](#stride-威脅模型)
10. [LINDDUN 隱私模型](#linddun-隱私模型)
11. [攻擊面](#攻擊面)
12. [安全假設](#安全假設)
13. [風險評估](#風險評估)
14. [法務聲明](#法務聲明)
15. [限制與風險](#限制與風險)
16. [結語](#結語)

---

## 概覽

本文件涵蓋：
- ESLock Encryptor / Decryptor — 相容 ES 檔案瀏覽器的加密工具
- KVH Locker — 高安全性、低可識別度的本地加密方案
- ESLock Recovery — 在密碼遺失時利用 Footer 內嵌金鑰復原 `.eslock` 檔案的工具

---

## 系統架構

- 使用者端工具（CLI / GUI）
- 密碼學核心（AES / PBKDF2 / HMAC）
- 檔案格式層（ESLock Footer / KVH Header）
- 本機儲存與備份媒體

---

## 加密模型

### ESLock

- AES-128-CFB（相容行動裝置）
- Key = MD5(password)
- 支援 Partial / Full Encryption

### KVH Locker

- PBKDF2-HMAC-SHA256（600k iterations）
- Master Key 派生成 File Key
- AES 加密 + HMAC 完整性驗證

---

## ESLock 相容性說明

### 加密演算法

- AES-128 CFB mode
- IV：固定為 `[0x00 .. 0x0F]`
- Key 派生：`MD5(password)`
- 檔名與 Key 儲存於 Footer（與手機版一致）
  - 這也是為何加密會被破解還原的主要原因——**不須原始密碼 即可還原檔案**
  - 優點：個人使用，不影響使用體驗，單純作為防窺的用途
  - 缺點：安全性較弱，容易被破解

```
Key = MD5(password)[:16]
Cipher = AES-CFB-128(Key, IV=[0x00..0x0F])
```

### ESLock 檔案結構

```
[ Encrypted Data Stream ]
[ Optional Partial Encryption ]
[ Footer ]
  ├─ Flags (0x04 = partial, 0xFF = full)
  ├─ Block Size
  ├─ Encrypted Original Filename
  ├─ Embedded AES Key
  ├─ CRC32 Integrity Check
  └─ Footer Length
```

---

## 部分加密機制

- 只加密檔案**前 N bytes + 後 N bytes**
- 中間資料保持原樣（速度極快）
- 適合影片、備份、同步資料

```
[ AES(head) ][ plain middle ][ AES(tail) ][ footer ]
```

> ⚠️ **Security Notice：** 部分加密保護 Metadata 與檔頭，但無法保護完整機密性。

---

## ESLock Recovery 工具

### ES 檔案瀏覽器加密演算法

ES 檔案瀏覽器使用 **AES-128-CFB**（密碼回饋模式，128 位元區段）搭配一個*固定的*初始向量（IV）來加密檔案。加密後的輸出以 `.eslock` 副檔名儲存。

### 金鑰推導

```
密碼（UTF-8 字串）
     │
     ▼
  MD5 雜湊（16 位元組）
     │
     ▼
  AES-128 金鑰（MD5 摘要的前 16 位元組）
```

- 使用者密碼以 **MD5** 進行雜湊
- 完整 16 位元組摘要直接作為 AES 金鑰
- 沒有鹽值、沒有迭代次數、沒有金鑰延展（**未使用** PBKDF2 / scrypt / Argon2）

### 完整加密（小檔案）

```
┌─────────────────────────────────┐
│         原始檔案                 │
│       （所有位元組）              │
└────────────────┬────────────────┘
                 │
                 ▼
   AES-128-CFB 串流加密
   IV  = [0x00, 0x01, 0x02 … 0x0F]
   Key = MD5(密碼)
                 │
                 ▼
┌─────────────────────────────────┐
│         加密後內容               │
├─────────────────────────────────┤
│            Footer               │
│   （中繼資料 + 金鑰 + CRC）      │
└─────────────────────────────────┘
          輸出：file.eslock
```

### 部分加密（大檔案）

```
┌──────────────┬───────────────────────┬──────────────┐
│  First Block │     Middle Section    │  Last Block  │
│  (1024 B)    │     (plaintext)       │  (1024 B)    │
└──────┬───────┴───────────────────────┴──────┬───────┘
       │                                      │
       ▼                                      ▼
  AES-CFB encrypt                        AES-CFB encrypt
  (fresh cipher)                         (fresh cipher)
       │                                      │
       ▼                                      ▼
┌──────────────┬───────────────────────┬──────────────┐
│  Encrypted   │  Plaintext (pass-     │  Encrypted   │
│  First Block │  through, untouched)  │  Last Block  │
├──────────────┴───────────────────────┴──────────────┤
│                       Footer                         │
└──────────────────────────────────────────────────────┘
```

- 預設**區塊大小 = 1024 位元組**
- 僅加密**前端**和**後端**各 1024 位元組
- 檔案**中段保持為明文**
- 每個區塊使用**全新的加密器**（相同金鑰 + 相同 IV）

### Footer 結構

Footer 附加在加密內容之後，最後 4 個位元組始終存放 footer 長度。

| 欄位 | 大小 | 說明 |
|---|---|---|
| 加密旗標 | 1 B | `0xFF` = 完整加密；其他 = 部分加密 |
| 區塊大小 | 4 B（大端序） | 僅在部分加密時存在 |
| 檔名長度 | 1 B | `0xFF` = 未儲存檔名 |
| 加密的檔名 | 可變長度 | AES-CFB 加密，使用相同金鑰 |
| 金鑰前綴魔術位元組 | 1 B | 固定為 `0x10` |
| **★ AES 金鑰 ★** | 16 B | 解密金鑰，以**明文**儲存 |
| 金鑰後綴魔術位元組 | 1 B | `0x00` 或 `0x02` |
| CRC 填充 | 4 B | 全為零 |
| 儲存的 CRC32 | 4 B（大端序） | CRC 區段之前的 footer 位元組之 CRC |
| Footer 長度 | 4 B（大端序） | 包含此欄位在內的 footer 總長度 |

### 漏洞——為什麼能夠復原

> ⚠️ **AES 解密金鑰以明文形式儲存在加密檔案本身之中。**

`.eslock` 格式更接近**混淆**而非加密。被「鎖定」的檔案攜帶著自己的鑰匙——就像一個上了鎖的箱子，卻把鑰匙貼在箱子外面。

| 弱點 | 說明 |
|---|---|
| 🔴 嚴重：金鑰儲存在檔案中 | 16 位元組的 AES 金鑰位於魔術位元組 `0x10` 和 `0x00`/`0x02` 之間，任何能讀取檔案的人都可以提取它 |
| 🔴 嚴重：不需要密碼 | 因為金鑰在 footer 中，使用者的密碼**完全不需要**即可解密 |
| 🟡 高：弱金鑰推導 | `MD5(密碼)` — 沒有鹽值、沒有迭代，即使金鑰沒有嵌入，密碼也能被輕易暴力破解 |
| 🟡 高：固定 IV | `IV = [0, 1, 2, …, 15]` — 每個檔案、每個區塊都使用相同 IV，破壞 CFB 語義安全性 |
| 🟡 高：部分加密洩漏資料 | 在部分加密模式下，檔案中段全部為明文，媒體檔案內容大部分可直接檢視 |
| 🟡 中：僅有 CRC，無 MAC | CRC32 驗證結構完整性，**而非**檔案真實性，沒有 HMAC 或 AEAD |

### 復原流程

```
啟動 CLI
  → 掃描輸入路徑，尋找 *.eslock
  → 對每個 .eslock 檔案：
      讀取最後 4 位元組 → footer_length
      長度有效？
        否 → --heuristic? 掃描最後 128KB 尋找特徵碼
        是 ↓
      解析 footer 結構（金鑰、CRC、加密模式、區塊大小、加密檔名）
      CRC 有效？
        否 → --ignore-crc? 否則跳過
        是 ↓
      從 footer 提取 16 位元組 AES 金鑰
      解密原始檔名（如果有的話）
      解密檔案內容：
        完整 → AES-CFB 整個串流
        部分 → 前段 + 後段區塊
      寫入復原後的檔案至輸出目錄 ✓
```

### 環境需求

| 套件 | 版本 | 用途 |
|---|---|---|
| Python | ≥ 3.8 | 執行環境 |
| pycryptodome | ≥ 3.9 | AES-128-CFB 解密 |

標準函式庫模組：`argparse`、`hashlib`、`os`、`struct`、`sys`、`zlib`、`datetime`、`pathlib`、`typing`

```bash
pip install pycryptodome
```

### 使用方法

```bash
# 復原目前目錄下所有 .eslock 檔案
python ESLOCK_Recovery.py

# 復原單一檔案
python ESLOCK_Recovery.py photo.eslock

# 將目錄復原至指定輸出資料夾
python ESLOCK_Recovery.py ./encrypted ./recovered

# 使用啟發式搜尋處理損壞的檔案
python ESLOCK_Recovery.py --heuristic ./damaged

# 忽略 CRC 不符強制復原
python ESLOCK_Recovery.py --ignore-crc ./files

# 覆寫已存在的輸出檔案
python ESLOCK_Recovery.py --overwrite ./files ./output
```

| 參數 | 說明 |
|---|---|
| `input` | 要復原的檔案或目錄（預設：目前目錄） |
| `output` | 目的地目錄（預設：自動建立 `recovered-YYYYMMDD-HHMMSS`） |
| `--overwrite` | 覆寫已存在的輸出檔案 |
| `--ignore-crc` | 即使 footer CRC 檢查失敗仍繼續處理 |
| `--heuristic` | 使用啟發式 footer 搜尋（適用於損壞／截斷的檔案） |

### 函式參考

**密碼學**

| 函式 | 說明 |
|---|---|
| `_make_aes(key)` | 使用固定 IV `[0..15]` 建立 AES-128-CFB 加密器 |
| `decrypt_stream(…)` | 解密檔案本體——處理完整及部分加密模式 |
| `decrypt_file_name(…)` | 解密儲存在 footer 中的原始檔名（補齊至 16 位元組邊界） |

**Footer 解析**

| 函式 | 說明 |
|---|---|
| `read_footer_standard(path)` | 快速讀取器——讀取最後 1024 位元組，從宣告的長度欄位解析 |
| `read_footer_heuristic(path)` | 容錯讀取器——掃描最後 128 KB 尋找特徵碼與結構模式 |
| `_seq_parse(buf, total, offset)` | 在指定的緩衝區偏移量嘗試解析 footer（由啟發式掃描器使用） |
| `_read_tail(path, length)` | 讀取檔案的最後 N 個位元組 |
| `_crc32(data)` | 計算無號 32 位元 CRC32 |

**復原**

| 函式 | 說明 |
|---|---|
| `_recover_one(…)` | 核心函式：讀取 footer → 提取金鑰 → 驗證 CRC → 解密 → 寫入輸出 |
| `main()` | CLI 進入點：解析參數、收集檔案、執行復原、列印摘要 |

**資料模型**

| 類別 | 說明 |
|---|---|
| `EslockFooter` | 儲存所有已解析的 footer 欄位：`key`、`is_partial_encryption`、`encrypted_block_size`、`encrypted_original_name`、`stored_crc`、`calculated_crc`、`footer_offset`、`footer_length`。屬性 `is_crc_valid` 檢查 CRC 是否吻合。 |

---

## KVH Locker 設計理念

### 核心設計原則

- 🚀 **極速加密**：採用串流模式（AES-CFB），大型檔案亦可快速處理，無需填充
- 👁️ **低可識別性**：輸出不含任何可辨識的 Magic Bytes 或固定標頭，外觀接近隨機雜訊
- 🧠 **格式抗辨析**：不符合任何已知加密格式（非 ESLock / ZIP / PGP / VeraCrypt），難以透過檔案特徵辨別
- 🔐 **現代密碼學配置**：以 PBKDF2-HMAC-SHA256（600k iterations）做金鑰強化，有效抵抗暴力破解
- 🔗 **完整性驗證**：每個檔案附加 HMAC-SHA256，確保資料未被竄改後才允許解密
- 🗝️ **無金鑰嵌入**：不將金鑰或密碼以任何形式儲存於輸出檔案（對比 ESLock 的弱點）
- 🔄 **隨機 IV**：每次加密產生全新隨機 IV，相同明文不會產生相同密文

### 與 ESLock 的關鍵差異

| 特性 | ESLock | KVH Locker |
|---|---|---|
| 金鑰派生 | `MD5(password)` | `PBKDF2-HMAC-SHA256` × 600k |
| IV | 固定 `[0x00..0x0F]` | 隨機 16 bytes |
| 金鑰嵌入檔案 | ✅ 明文嵌入 | ❌ 不嵌入 |
| 完整性驗證 | CRC32（弱） | HMAC-SHA256 |
| 格式可識別度 | 高（有特徵標頭） | 極低（偽隨機） |
| 無密碼可還原 | ⚠️ 可以 | ❌ 不可 |

### KVH 金鑰層級架構

```
User Password
   ↓ PBKDF2-HMAC-SHA256
   ↓   salt = random 16 bytes
   ↓   iterations = 600,000
   ↓   dkLen = 32 bytes
Master Key (32 bytes)
   ↓ SHA256(Master Key + IV)
File Key (32 bytes)
   ↓ AES-256-CFB, IV = random 16 bytes
Encrypted Ciphertext
   ↓ HMAC-SHA256(File Key, Ciphertext)
Auth Tag (32 bytes) → appended to file
```

### 檔案格式（Header）

```
[ Magic / Version  :  4 bytes  ]  ← 內部識別，不對外暴露
[ Salt             : 16 bytes  ]  ← 隨機鹽值，用於 PBKDF2
[ IV               : 16 bytes  ]  ← 隨機初始向量
[ Ciphertext       :  N bytes  ]  ← AES-256-CFB 加密內容
[ HMAC-SHA256      : 32 bytes  ]  ← 完整性驗證碼（結尾）
```

---

## 方案比較

| 方案 | 速度 | 安全性 | 可識別度 |
|---|---|---|---|
| ESLock Partial | ★★★★★ | ★☆☆☆☆ | 高 |
| ESLock Full | ★★★☆☆ | ★★★☆☆ | 高 |
| KVH Locker | ★★★★☆ | ★★★★★ | 極低 |
| ZIP + AES | ★★☆☆☆ | ★★★☆☆ | 高 |
| VeraCrypt | ★☆☆☆☆ | ★★★★★ | 中 |

---

## STRIDE 威脅模型

| 類別 | 威脅 | 對策 |
|---|---|---|
| Spoofing | 身份偽裝 | 金鑰密碼學 |
| Tampering | 資料竄改 | CRC / HMAC |
| Repudiation | 操作否認 | 無集中授權機構 |
| Information Disclosure | 資訊外洩 | KVH 全加密 |
| Denial of Service | 服務阻斷 | 安全失敗設計 |
| Elevation of Privilege | 權限提升 | OS 層邊界隔離 |

---

## LINDDUN 隱私模型

| 類別 | 風險 | 對策 |
|---|---|---|
| Linkability | 跨檔關聯推斷 | 隨機 IV |
| Identifiability | 使用者身份推斷 | 格式混淆 |
| Non-repudiation | 操作可追蹤性 | 無集中記錄 |
| Detectability | 特徵掃描偵測 | 標頭遮蔽 |
| Data Disclosure | 明文資訊外洩 | KVH 全加密 |
| Unawareness | 使用者誤解風險 | 明確文件說明 |
| Non-compliance | 法規違規風險 | 由使用者自行負責 |

---

## 攻擊面

攻擊者可接觸之面包含使用者輸入、檔案系統與加密後資料，防禦邊界由加密邏輯與完整性驗證組成。

**防禦邊界內部元件：**
- 加密邏輯（AES / PBKDF2）
- 完整性驗證（HMAC-SHA256）

**防禦邊界外部儲存：**
- 加密儲存（`.eslock` / `.kvh`）

---

## 安全假設

- 作業系統本身未被入侵
- 密碼安全由使用者自行承擔
- 系統不提供金鑰託管或恢復

---

## 風險評估

- ESLock 使用 MD5（為相容性保留，非最佳實務）
- Partial Encryption 不可防止內容抽取
- Footer 明文位置可被取出（設計如此）
- KVH Locker 密碼遺失 = **永久不可逆**

---

## 法務聲明

本工具僅用於：

- ✔ 個人資料保護
- ✔ 合法鑑識 / 數位證據
- ✔ 備份與隱私管理
- ❌ 禁止非法闖入、竊取、破壞他人資料

---

## 限制與風險

- Partial Encryption 並非完整機密性保障
- 密碼遺失即永久喪失資料
- 無法防止 OS 已遭入侵之情境

---

## 結語

ESLock 工具提供 **100% 手機相容性**，KVH Locker 則提供**更高層級、低可辨識度的隱匿方案**。

**建議實務策略：**

| 使用情境 | 建議工具 |
|---|---|
| 📱 手機同步 | ESLock |
| 🗄️ 私密歸檔 | KVH Locker |
| 🧪 鑑識分析 | ESLockDecryptor |

---

*© 2025-2026 Rewolf — MIT 授權條款*
