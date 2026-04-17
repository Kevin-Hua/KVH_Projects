#!/usr/bin/env python
"""
TDX_Billboard.py — 台鐵簡易時刻懸浮視窗
半透明置頂小視窗，顯示目前時刻後最近 N 班列車。
第一次執行時詢問起訖站，並存入 tdx_billboard.ini 供下次讀取。
"""
import configparser
import json
import os
import re
import sys
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox, font as tkfont
from pathlib import Path
import requests
from collections import defaultdict
from datetime import datetime, date, timedelta

# ─── App Metadata ───────────────────────────────────────────────────────────
_APP_VERSION   = "1.0.13"
_APP_NAME      = "TDX 台鐵時刻表"
_APP_COPYRIGHT = "© 2026 KVH"

# ─── 路徑設定 ─────────────────────────────────────────────────────────────
_BASE_DIR   = (Path(sys.executable).parent if getattr(sys, "frozen", False)
               else Path(__file__).parent)
CONFIG_FILE    = _BASE_DIR / "tdx_billboard.ini"
STATION_LIST   = _BASE_DIR / "station_list.txt"
TOKEN_FILE   = _BASE_DIR / "tdx_token_cache.json"
SIMPLE_CACHE = _BASE_DIR / "tdx_billboard_cache.json"   # 與 TDX.py 分開存放
DEBUG_LOG          = _BASE_DIR / "tdx_billboard_debug.log"
_DEBUG_LOG_TTL_DAYS = 7   # 超過此天數則將舊 log 改名保留，開新檔

# ─── Debug Logging ────────────────────────────────────────────────────────
_debug_enabled: bool = False
_debug_lock          = threading.Lock()


def _rotate_debug_log() -> None:
    """若 DEBUG_LOG 存在且最後修改日超過 _DEBUG_LOG_TTL_DAYS，
    改名為 tdx_billboard_debug_YYYYMMDD.log 後，由後續程式建立新檔。"""
    if not DEBUG_LOG.exists():
        return
    age_days = (time.time() - DEBUG_LOG.stat().st_mtime) / 86400
    if age_days < _DEBUG_LOG_TTL_DAYS:
        return
    try:
        mdate = datetime.fromtimestamp(DEBUG_LOG.stat().st_mtime).strftime("%Y%m%d")
        archived = DEBUG_LOG.with_name(f"tdx_billboard_debug_{mdate}.log")
        # 若同名已存在（極少見）則加流水號
        stem, suffix, idx = archived.stem, archived.suffix, 1
        while archived.exists():
            archived = DEBUG_LOG.with_name(f"{stem}_{idx}{suffix}")
            idx += 1
        DEBUG_LOG.rename(archived)
    except Exception:
        pass


def _dlog(msg) -> None:
    """Debug 模式開啟時，附加記錄至 tdx_billboard_debug.log（含時間戳與執行緒名）。
    msg 可為 str 或 callable（延遲建立，debug 關閉時完全不評估）。"""
    if not _debug_enabled:
        return
    if callable(msg):
        msg = msg()
    ts   = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    thr  = threading.current_thread().name
    line = f"[{ts}][{thr}] {msg}\n"
    try:
        with _debug_lock:
            with open(DEBUG_LOG, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


# ─── TDX 認證 ─────────────────────────────────────────────────────────────
TOKEN_URL = ("https://tdx.transportdata.tw/auth/realms/TDXConnect"
             "/protocol/openid-connect/token")

# ─── 車站代碼表 ───────────────────────────────────────────────────────────
STATIONS: dict[str, str] = {
    "基隆": "0900", "三坑": "0910", "八堵": "0920", "七堵": "0930",
    "百福": "0940", "五堵": "0950", "汐止": "0960", "汐科": "0970",
    "南港": "0980", "松山": "0990", "臺北": "1000", "萬華": "1010",
    "板橋": "1020", "浮洲": "1030", "樹林": "1040", "山佳": "1060",
    "鶯歌": "1070", "桃園": "1080", "內壢": "1090", "中壢": "1100",
    "埔心": "1110", "楊梅": "1120", "富岡": "1130", "新豐": "1170",
    "湖口": "1160", "竹北": "1180", "新竹": "1210",
    "竹南": "1250", "苗栗": "3160", "三義": "3190", "豐原": "3230",
    "潭子": "3250", "臺中": "3300", "彰化": "3360", "員林": "3390",
    "田中": "3420", "二水": "3430", "斗六": "3470", "斗南": "3480",
    "嘉義": "4080", "新營": "4120", "臺南": "4220", "新左營": "4340",
    "左營": "4350", "高雄": "4400", "鳳山": "4440", "屏東": "5000",
    "花蓮": "7000", "新城": "7030", "宜蘭": "7190", "羅東": "7160",
    "蘇澳新": "7130",
}
_STATION_LABELS = [f"{name}（{code}）" for name, code in STATIONS.items()]

# ─── 配色 ─────────────────────────────────────────────────────────────────
C_BG     = "#001428"
C_HDR    = "#00203c"
C_FG     = "#cce8ff"
C_DIM    = "#5577aa"
C_SEP    = "#003060"
C_LOCAL  = "#5ab4f2"   # 區間車
C_EXPR   = "#50e09a"   # 區間快
C_TZE    = "#ff8888"   # 自強
C_CHU    = "#ffaa55"   # 葵光
C_FU     = "#cc99ff"   # 復興
C_PREM   = "#ffd060"   # 太魯閣 / 普悊瑪

FONT_HDR   = ("微軟正黑體", 11, "bold")
FONT_TRAIN = ("微軟正黑體", 13, "bold")
FONT_TYPE  = ("微軟正黑體", 11)
FONT_SMALL = ("微軟正黑體",  9)

# ─── API 金鑰（TDX + CWA）─────────────────────────────────────────────────
# 讀取順序：環境變數 > 內嵌 TDX_Kindway.key（EXE）> tdx_billboard.ini [Credentials]（開發）
# EXE 打包時由 _Pyinstaller.bat 自動將 TDX_Kindway.key 嵌入，不需外部檔案。
# 開發時於 tdx_billboard.ini 加入：
#   [Credentials]
#   tdx_client_id     = your-id
#   tdx_client_secret = your-secret
#   cwa_apikey        = CWA-XXXX-...
def _load_credentials() -> tuple[str, str, str]:
    e = {k: os.environ.get(ev, "") for k, ev in (
        ("tdx_client_id",     "TDX_CLIENT_ID"),
        ("tdx_client_secret", "TDX_CLIENT_SECRET"),
        ("cwa_apikey",        "CWA_APIKEY"),
    )}
    if not all(e.values()):
        _cfg = configparser.ConfigParser()
        # EXE 模式：從 PyInstaller 內嵌的 TDX_Kindway.key 讀取
        if getattr(sys, "frozen", False):
            _key = Path(sys._MEIPASS) / "TDX_Billboard.key"
            if _key.exists():
                _cfg.read(str(_key), encoding="utf-8")
        # 開發模式：從 tdx_billboard.ini 讀取
        elif CONFIG_FILE.exists():
            _cfg.read(str(CONFIG_FILE), encoding="utf-8")
        sec = "Credentials"
        if sec in _cfg:
            for k in e:
                e[k] = e[k] or _cfg[sec].get(k, "").strip()
    return e["tdx_client_id"], e["tdx_client_secret"], e["cwa_apikey"]

CLIENT_ID, CLIENT_SECRET, _CWA_APIKEY = _load_credentials()
_NO_CREDENTIALS: bool = not (CLIENT_ID and CLIENT_SECRET)

# ─── CWA 降雨 API ─────────────────────────────────────────────────────────
_CWA_RAIN_URL     = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0002-001"
_RAIN_INTERVAL_MS = 30 * 60_000   # 30 分鐘刷新

# 台鐵站名 → CWA 氣象站搜尋關鍵字（僅需填寫無法直接對應的站）
_TRAIN_TO_AREA: dict[str, str] = {
    "汐科":  "汐止",
    "新左營": "左營",
    "蘇澳新": "蘇澳",
    "三坑":  "基隆",
    "百福":  "汐止",
    "五堵":  "汐止",
    "浮洲":  "板橋",
    "山佳":  "樹林",
    "富岡":  "楊梅",
    "新城":  "新城",
}

# CWA 縣市排序（六都優先，先北再南）
_CWA_COUNTY_ORDER = [
    "臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市",
    "基隆市", "新竹市", "新竹縣", "苗栗縣",
    "彰化縣", "南投縣", "雲林縣",
    "嘉義市", "嘉義縣", "屏東縣",
    "宜蘭縣", "花蓮縣", "臺東縣",
    "澎湖縣", "金門縣", "連江縣",
]

# 固定查詢 10分鐘 與 1小時 降雨量
_RAIN_10MIN_KEY = "Past10Min"
_RAIN_1HR_KEY   = "Past1hr"

# ─── CWA 雨量站資料快取（記憶體內，30 分鐘有效）────────────────────────────
_cwa_stations_cache: list       = []
_cwa_stations_ts:    float      = 0.0          # 上次下載的 time.time()
_cwa_stations_lock               = threading.Lock()
_CWA_STATIONS_TTL                = 30 * 60     # 秒

_cwa_tree_cache:     tuple | None = None        # (county_list, data_tree)
_cwa_tree_lock                    = threading.Lock()


def _cwa_get_stations() -> list:
    """取得全台雨量站列表，30 分鐘內使用記憶體快取。"""
    global _cwa_stations_cache, _cwa_stations_ts
    with _cwa_stations_lock:
        if _cwa_stations_cache and (time.time() - _cwa_stations_ts < _CWA_STATIONS_TTL):
            _dlog("_cwa_get_stations: 命中快取")
            return _cwa_stations_cache
    _dlog("_cwa_get_stations: 快取過期或空白，下載資料…")
    params = {"Authorization": _CWA_APIKEY, "format": "JSON"}
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = requests.get(_CWA_RAIN_URL, params=params, timeout=20, verify=False)
    r.raise_for_status()
    stations = r.json()["records"]["Station"]
    with _cwa_stations_lock:
        _cwa_stations_cache = stations
        _cwa_stations_ts    = time.time()
    with _cwa_tree_lock:          # 站點資料已更新，清除縣市樹快取
        _cwa_tree_cache = None
    _dlog(f"_cwa_get_stations: 下載完成，共 {len(stations)} 站")
    return stations


def _cwa_fetch_tree() -> tuple[list, dict]:
    """從 CWA API 取得全台雨量站，回傳 (county_list, data_tree)。
    data_tree[county][town] = [station_dict, ...]
    使用 _cwa_get_stations() 快取，同一 process 內不重複下載。
    """
    global _cwa_tree_cache
    with _cwa_tree_lock:
        if _cwa_tree_cache is not None:
            _dlog("_cwa_fetch_tree: 命中 tree 快取")
            return _cwa_tree_cache

    stations = _cwa_get_stations()
    tree: dict = defaultdict(lambda: defaultdict(list))
    for s in stations:
        geo    = s.get("GeoInfo", {})
        county = geo.get("CountyName") or "未知"
        town   = geo.get("TownName")   or "未知"
        tree[county][town].append(s)

    def _county_key(name: str) -> int:
        try:
            return _CWA_COUNTY_ORDER.index(name)
        except ValueError:
            return len(_CWA_COUNTY_ORDER)

    county_list = sorted(tree.keys(), key=_county_key)
    result = county_list, {c: dict(t) for c, t in tree.items()}
    with _cwa_tree_lock:
        _cwa_tree_cache = result
    return result


def _fetch_rain_both(from_name: str, to_name: str,
                     area1: tuple | None = None,
                     area2: tuple | None = None) -> tuple[str, str, str, str]:
    """查詢兩地 10分鐘 與 1小時 降雨量。
    回傳 (from_10min, from_1hr, to_10min, to_1hr)。
    area1/area2: (county, town) 自訂區域；None 時依 from/to_name 模糊比對。
    """
    def _area(name: str) -> str:
        return _TRAIN_TO_AREA.get(name, name)

    try:
        stations = _cwa_get_stations()
    except Exception as e:
        _dlog(f"_fetch_rain_both: API 失敗 {e}")
        return "—", "—", "—", "—"

    def _extract(s: dict, pkey: str) -> str:
        rf  = s.get("RainfallElement", {})
        val = rf.get(pkey, {}).get("Precipitation", None)
        if val is None or (isinstance(val, (int, float)) and float(val) < -9990):
            return "—"
        try:
            return f"{float(val):.1f}"
        except (ValueError, TypeError):
            return str(val)

    def _find_by_area(county: str, town: str) -> tuple[str, str]:
        for s in stations:
            geo = s.get("GeoInfo", {})
            if geo.get("CountyName") == county and geo.get("TownName") == town:
                return _extract(s, _RAIN_10MIN_KEY), _extract(s, _RAIN_1HR_KEY)
        return "—", "—"

    def _find_by_name(name: str) -> tuple[str, str]:
        kw = _area(name)
        for s in stations:
            sname = s.get("StationName", "")
            stown = s.get("GeoInfo", {}).get("TownName", "")
            if kw == sname or kw in sname or kw in stown:
                return _extract(s, _RAIN_10MIN_KEY), _extract(s, _RAIN_1HR_KEY)
        return "—", "—"

    f10, f1h = _find_by_area(area1[0], area1[1]) if (area1 and area1[0]) else _find_by_name(from_name)
    t10, t1h = _find_by_area(area2[0], area2[1]) if (area2 and area2[0]) else _find_by_name(to_name)
    _dlog(f"_fetch_rain_both: {from_name} 10min={f10}mm 1hr={f1h}mm  {to_name} 10min={t10}mm 1hr={t1h}mm")
    return f10, f1h, t10, t1h

# ─── 站名清單 ──────────────────────────────────────────────────────────
_STATION_LIST_TTL_DAYS = 5   # 超過此天數則重新產生 station_list.txt

def _ensure_station_list() -> None:
    """station_list.txt 不存在或超過 _STATION_LIST_TTL_DAYS 天則重新建立。"""
    if STATION_LIST.exists():
        age_days = (time.time() - STATION_LIST.stat().st_mtime) / 86400
        if age_days < _STATION_LIST_TTL_DAYS:
            return
    lines = [f"{name} : {code}" for name, code in STATIONS.items()]
    STATION_LIST.write_text("\n".join(lines), encoding="utf-8")

# ─── 錯誤終止 ────────────────────────────────────────────────────────────
def _fatal(msg: str) -> None:
    """顯示錯誤訊息後結束程式。必須在 Tk 已啟動後呼叫。"""
    messagebox.showerror("設定錯誤", msg)
    sys.exit(1)


# ─── Config 讀寫 ──────────────────────────────────────────────────────────
def _load_cfg() -> configparser.ConfigParser:
    _dlog(f"_load_cfg: 讀取 {CONFIG_FILE.name}")
    cfg = configparser.ConfigParser()
    if CONFIG_FILE.exists():
        try:
            cfg.read(str(CONFIG_FILE), encoding="utf-8")
            _dlog(f"_load_cfg: 完成 sections={cfg.sections()}")
        except configparser.Error as e:
            _fatal(f"config.ini 格式錯誤，無法讀取：\n{e}")
    else:
        _dlog("_load_cfg: 設定檔不存在")
    return cfg


def _write_cfg(cfg: configparser.ConfigParser) -> None:
    tmp = CONFIG_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        cfg.write(f)
    tmp.replace(CONFIG_FILE)


def _get_od() -> tuple[str, str, str, str] | None:
    """回傳 (from_code, from_name, to_code, to_name)，或 None（第一次執行）。"""
    cfg = _load_cfg()
    sec = "tdx_billboard"
    if sec not in cfg:
        _dlog("_get_od: section 不存在，OD 未設定")
        return None   # 尚未設定，進入首次設定流程
    s  = cfg[sec]
    fc = s.get("from_code", "").strip()
    fn = s.get("from_name", "").strip()
    tc = s.get("to_code",   "").strip()
    tn = s.get("to_name",   "").strip()
    # 全空 → 只存了視窗位置，OD 尚未設定
    if not any([fc, fn, tc, tn]):
        _dlog("_get_od: OD 欄位全空，OD 未設定")
        return None
    # 部分填寫但不完整 → 資料損壞
    if not all([fc, fn, tc, tn]):
        _fatal("tdx_billboard.ini [tdx_billboard] 內容不完整。\n請刪除 tdx_billboard.ini 後重新執行。")
    all_codes = set(STATIONS.values())
    if fc not in all_codes or tc not in all_codes:
        _fatal(
            f"tdx_billboard.ini 中的車站代碼無效（from_code={fc!r}，to_code={tc!r}）。\n"
            "請刪除 tdx_billboard.ini 後重新執行。"
        )
    _dlog(f"_get_od: from={fn}({fc}) to={tn}({tc})")
    return (fc, fn, tc, tn)


def _save_od(fc: str, fn: str, tc: str, tn: str) -> None:
    cfg = _load_cfg()
    sec = "tdx_billboard"
    if sec not in cfg:
        cfg[sec] = {}
    cfg[sec]["from_code"] = fc
    cfg[sec]["from_name"] = fn
    cfg[sec]["to_code"]   = tc
    cfg[sec]["to_name"]   = tn
    _write_cfg(cfg)


_OD_HIST_MAX = 10

def _get_od_history() -> list[tuple]:
    """從 INI [OD_History] 讀取最多 10 筆歷史 OD（由新到舊）。"""
    cfg = _load_cfg()
    sec = "OD_History"
    if sec not in cfg:
        return []
    result = []
    for i in range(_OD_HIST_MAX):
        raw = cfg[sec].get(f"od_{i}", "").strip()
        if not raw:
            continue
        parts = raw.split("|")
        if len(parts) == 4 and all(parts):
            result.append(tuple(parts))
    return result


def _save_od_to_history(fc: str, fn: str, tc: str, tn: str) -> None:
    """將新 OD 加入歷史最前，去除重複，保留最新 10 筆。"""
    history = _get_od_history()
    entry = (fc, fn, tc, tn)
    history = [h for h in history if h != entry]
    history.insert(0, entry)
    history = history[:_OD_HIST_MAX]
    cfg = _load_cfg()
    sec = "OD_History"
    cfg[sec] = {}
    for i, (hfc, hfn, htc, htn) in enumerate(history):
        cfg[sec][f"od_{i}"] = f"{hfc}|{hfn}|{htc}|{htn}"
    _write_cfg(cfg)


def _get_win_pos() -> tuple[int, int]:
    cfg = _load_cfg()
    sec = "tdx_billboard"
    try:
        x = int(cfg[sec].get("win_x", 120)) if sec in cfg else 120
        y = int(cfg[sec].get("win_y", 120)) if sec in cfg else 120
    except ValueError:
        x, y = 120, 120
    # 防止視窗跑到螢幕外 — 允許 ±32000（足以涵蓋多螢幕）
    if x < -32000 or x > 32000:
        x = 120
    if y < -32000 or y > 32000:
        y = 120
    return x, y


def _save_win_pos(x: int, y: int) -> None:
    cfg = _load_cfg()
    sec = "tdx_billboard"
    if sec not in cfg:
        cfg[sec] = {}
    cfg[sec]["win_x"] = str(x)
    cfg[sec]["win_y"] = str(y)
    _write_cfg(cfg)


def _get_prefs() -> dict:
    """一次讀取 ini，回傳所有偏好設定（單次 I/O）。"""
    cfg = _load_cfg()
    s   = cfg["tdx_billboard"] if "tdx_billboard" in cfg else {}

    def _i(key: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
        try:
            v = int(s.get(key, default))
            if lo is not None: v = max(lo, v)
            if hi is not None: v = min(hi, v)
            return v
        except (ValueError, TypeError):
            return default

    prefs = {
        "remind_mins":    _i("remind_mins",    0,  lo=0),
        "alpha":          _i("alpha_pct",       75, 10, 100),
        "train_count":    _i("train_count",     3,  1,  10),
        "show_local":     s.get("show_local",   "1") != "0",
        "show_express":   s.get("show_express", "1") != "0",
        "debug_log":      s.get("debug_log",    "0") != "0",
        "live_refresh":      s.get("live_refresh",  "0") != "0",
        "show_clock":        s.get("show_clock",    "0") != "0",
        "rain_custom":       s.get("rain_custom", "0") != "0",
        "rain_area1_county": s.get("rain_area1_county", ""),
        "rain_area1_town":   s.get("rain_area1_town",   ""),
        "rain_area2_county": s.get("rain_area2_county", ""),
        "rain_area2_town":   s.get("rain_area2_town",   ""),
        "auto_close_time":   s.get("auto_close_time",   "").strip(),
        "remind_on_time":    s.get("remind_on_time",    "").strip(),
        "remind_off_time":   s.get("remind_off_time",   "").strip(),
    }
    _dlog(lambda: f"_get_prefs: {prefs}")
    return prefs


# ─── TDX API ──────────────────────────────────────────────────────────────
def _load_token() -> str | None:
    if not TOKEN_FILE.exists():
        _dlog("_load_token: token 快取不存在")
        return None
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if time.time() < d.get("expires_at", 0) - 30:
            _dlog("_load_token: 使用快取 token（有效）")
            return d["access_token"]
    except Exception:
        pass
    _dlog("_load_token: token 快取失效")
    return None


def _fetch_token() -> str:
    if _NO_CREDENTIALS:
        raise RuntimeError("未設定 TDX API 金鑰（tdx_client_id / tdx_client_secret）")
    _dlog("_fetch_token: 向 TDX 取新 token…")
    resp = requests.post(
        TOKEN_URL,
        headers={"content-type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials",
              "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        timeout=15,
    )
    resp.raise_for_status()
    j     = resp.json()
    cache = {"access_token": j["access_token"],
             "expires_at":   time.time() + int(j.get("expires_in", 1800))}
    tmp = TOKEN_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    tmp.replace(TOKEN_FILE)
    _dlog(f"_fetch_token: token 取得，有效 {j.get('expires_in', 1800)} 秒")
    return cache["access_token"]


def _get_token() -> str:
    return _load_token() or _fetch_token()


def _api_get(url: str) -> dict:
    _dlog(f"_api_get: GET …{url[-70:]}")
    token   = _get_token()
    headers = {"authorization": f"Bearer {token}", "Accept-Encoding": "gzip"}
    resp    = requests.get(url, headers=headers, timeout=60)
    _dlog(f"_api_get: status={resp.status_code}")
    if resp.status_code == 401:
        _dlog("_api_get: 401 → 刷新 token 後重試")
        TOKEN_FILE.unlink(missing_ok=True)
        headers["authorization"] = f"Bearer {_fetch_token()}"
        resp = requests.get(url, headers=headers, timeout=60)
        _dlog(f"_api_get: 重試 status={resp.status_code}")
    resp.raise_for_status()
    return resp.json()


def _get_all_trains() -> tuple[list, str | None]:
    """從快取或 API 取得今日班表。
    回傳 (trains, warn)；warn 非 None 表示無法連線 API，顯示快取資料。"""
    today_str = date.today().isoformat()
    _dlog(f"_get_all_trains: 查詢 {today_str} 班表")
    if SIMPLE_CACHE.exists():
        try:
            with open(SIMPLE_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cached_trains = data.get("TrainTimetables", [])
            cached_date   = data.get("CachedDate", "")
            if cached_date == today_str:
                _dlog(f"_get_all_trains: 快取命中，共 {len(cached_trains)} 班")
                if _NO_CREDENTIALS:
                    return cached_trains, "⚠ API 金鑰未設定，使用今日快取資料"
                return cached_trains, None
        except Exception:
            cached_trains = []
            cached_date   = ""
    else:
        cached_trains = []
        cached_date   = ""

    if _NO_CREDENTIALS:
        # 無 API 金鑰 + 無有效快取
        if cached_trains:
            return cached_trains, f"⚠ API 金鑰未設定，使用 {cached_date} 舊快取資料"
        raise RuntimeError(
            "無法存取 API：未設定 TDX API 金鑰，且本機無班表快取。\n"
            "請在 tdx_billboard.ini 的 [Credentials] 標頭下填入金鑰。")

    # 下載今天的實際行駛班表
    _dlog("_get_all_trains: 快取未命中，開始下載…")
    url  = (f"https://tdx.transportdata.tw/api/basic/v3/Rail/TRA"
            f"/DailyTrainTimetable/TrainDate/{today_str}?$top=2000&$format=JSON")
    resp = _api_get(url)
    trains = resp.get("TrainTimetables", [])
    _dlog(f"_get_all_trains: 下載完成，共 {len(trains)} 班，寫入快取")
    try:
        tmp = SIMPLE_CACHE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"CachedDate": today_str,
                       "TrainTimetables": trains},
                      f, ensure_ascii=False)
        tmp.replace(SIMPLE_CACHE)
    except Exception:
        pass
    return trains, None


def _fetch_live_delays(station_id: str) -> dict:
    """從 LiveBoard API 取得即時誤點，回傳 {TrainNo: DelayTime（分鐘）}。"""
    _dlog(f"_fetch_live_delays: 查站 {station_id}")
    url = (f"https://tdx.transportdata.tw/api/basic/v2/Rail/TRA"
           f"/LiveBoard/Station/{station_id}?$format=JSON")
    try:
        resp = _api_get(url)
        # v2 returns a flat list; v3 wraps in {"TrainLiveBoards": [...]}
        items = resp if isinstance(resp, list) else resp.get("TrainLiveBoards", [])
        result = {}
        for item in items:
            no = item.get("TrainNo", "")
            if not no:
                continue
            result[no] = int(item.get("DelayTime") or 0)
        _dlog(f"_fetch_live_delays: 取得 {len(result)} 班誤點資料")
        return result
    except Exception as e:
        _dlog(f"_fetch_live_delays: 失敗 {e}")
        return {}


# ─── 行駛日期判斷 ─────────────────────────────────────────────────────────
_WDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6}


def _schedule_days(text: str) -> set:
    days: set = set()
    for m in re.finditer(r"週([一二三四五六日])至([一二三四五六日])", text):
        s, e = _WDAY[m.group(1)], _WDAY[m.group(2)]
        days.update(range(s, e + 1) if s <= e
                    else list(range(s, 7)) + list(range(0, e + 1)))
    for m in re.finditer(r"週([一二三四五六日])", text):
        days.add(_WDAY[m.group(1)])
    if days:
        for m in re.finditer(r"[、及]([一二三四五六日])", text):
            days.add(_WDAY[m.group(1)])
    return days


def _runs_today(note: str, ref: date) -> bool | None:
    """True=行駛 / False=停駛 / None=無資訊（當每日行駛）。"""
    if not note:
        return None
    m = re.search(r"逢(.+?)(行駛|停駛)", note)
    if m:
        days = _schedule_days(m.group(1))
        if days:
            wd = ref.weekday()
            return (wd in days) if m.group(2) == "行駛" else (wd not in days)
    return None


# ─── 列車篩選 ─────────────────────────────────────────────────────────────
def _train_display(ttype: str) -> tuple[str, str]:
    """回傳 (顯示名稱, 顏色)。名稱最多 3 個字。"""
    if ttype.startswith("太魯閣"): return "太魯閣", C_PREM
    if ttype.startswith("普悊瑪"): return "普悊瑪", C_PREM
    if ttype.startswith("區間快"): return "區間快", C_EXPR
    if ttype.startswith("區間"):   return "區間 ",  C_LOCAL
    if ttype.startswith("自強"):   return "自強 ",  C_TZE
    if ttype.startswith("葵光"):   return "葵光 ",  C_CHU
    if ttype.startswith("復興"):   return "復興 ",  C_FU
    if ttype.startswith("莒光"):   return "莒光 ",  "#ddaa44"
    # 其他/未知：只取 CJK 字元，避免括號等符號混入
    cjk = re.sub(r"[^\u4e00-\u9fff\u3400-\u4dbf]", "", ttype)
    return (cjk[:3] or ttype[:3]), "#aaaaaa"


def _next_local_trains(all_trains: list, fc: str, tc: str,
                       count: int = 3,
                       show_local: bool = True,
                       show_express: bool = True) -> list:
    """回傳下一筆起訖間各車種列車（不超過 count 筆）。"""
    now_str = datetime.now().strftime("%H:%M")
    today   = date.today()
    result  = []

    for item in all_trains:
        info  = item.get("TrainInfo", {})
        ttype = info.get("TrainTypeName", {}).get("Zh_tw", "")
        if not ttype:                          # 車種不明跳過
            continue
        is_local = ttype.startswith("區間")
        if is_local and not show_local:
            continue
        if not is_local and not show_express:
            continue
        note = info.get("Note", "")
        if _runs_today(note, today) is False:  # 今日停駛
            continue

        stops    = item.get("StopTimes", [])
        orig_seq = dest_seq = None
        orig_dep = dest_arr = ""
        for s in stops:
            sid = s.get("StationID", "")
            if sid == fc:
                orig_seq = s.get("StopSequence", 0)
                orig_dep = s.get("DepartureTime", "")
            elif sid == tc:
                dest_seq = s.get("StopSequence", 0)
                dest_arr = s.get("ArrivalTime", s.get("DepartureTime", ""))
        if orig_seq is None or dest_seq is None or orig_seq >= dest_seq or not orig_dep:
            continue

        label, color = _train_display(ttype)
        result.append({
            "type":   label,
            "color":  color,
            "no":     info.get("TrainNo", ""),
            "dep":    orig_dep,
            "arr":    dest_arr,
        })

    upcoming = [t for t in result if t["dep"] >= now_str]
    upcoming.sort(key=lambda x: x["dep"])
    selected = upcoming[:count]
    _dlog(lambda: f"_next_local_trains: {fc}→{tc} 候選 {len(result)} 班 / 未來 {len(upcoming)} 班 / 顯示 {len(selected)} 班")
    return selected


# ─── 雨量區域設定對話框 ────────────────────────────────────────────────────
class _RainAreaDialog(tk.Toplevel):
    """選擇兩個自訂雨量顯示區域（各自選縣市 + 鄉鎮市區）。"""

    def __init__(self, parent, current_area1: tuple = ("", ""),
                 current_area2: tuple = ("", "")):
        super().__init__(parent)
        self.result = None   # ((county1, town1), (county2, town2)) or None
        self.title("自訂雨量顯示區域")
        self.resizable(False, False)
        self.wm_attributes("-topmost", True)
        self.grab_set()
        self.configure(bg="#001a2e")

        self._county_list: list = []
        self._data_tree:   dict = {}
        self._cur1 = current_area1
        self._cur2 = current_area2

        self._build_ui()
        threading.Thread(target=self._load_data, daemon=True).start()

    def _build_ui(self):
        BG  = "#001a2e"
        pad = {"padx": 14, "pady": 6}

        tk.Label(self, text="自訂雨量顯示區域", bg=BG, fg=C_FG,
                 font=FONT_HDR).grid(row=0, column=0, columnspan=2, pady=(12, 4))

        self._status_var = tk.StringVar(value="資料載入中，請稍候…")
        tk.Label(self, textvariable=self._status_var, bg=BG, fg=C_DIM,
                 font=FONT_SMALL).grid(row=1, column=0, columnspan=2, pady=(0, 6))

        self._county_vars: list = []
        self._county_cbs:  list = []
        self._town_vars:   list = []
        self._town_cbs:    list = []

        for col, lbl in enumerate(["區域一（起站側）", "區域二（訖站側）"]):
            tk.Label(self, text=lbl, bg=BG, fg=C_FG,
                     font=FONT_TYPE).grid(row=2, column=col, **pad)

            cvar = tk.StringVar()
            ccb  = ttk.Combobox(self, textvariable=cvar, state="disabled",
                                width=16, font=FONT_TYPE)
            ccb.grid(row=3, column=col, **pad)

            tvar = tk.StringVar()
            tcb  = ttk.Combobox(self, textvariable=tvar, state="disabled",
                                width=16, font=FONT_TYPE)
            tcb.grid(row=4, column=col, **pad)

            ccb.bind("<<ComboboxSelected>>", lambda e, c=col: self._on_county(c))
            self._county_vars.append(cvar)
            self._county_cbs.append(ccb)
            self._town_vars.append(tvar)
            self._town_cbs.append(tcb)

        btn_frm = tk.Frame(self, bg=BG)
        btn_frm.grid(row=5, column=0, columnspan=2, pady=12)
        self._ok_btn = tk.Button(btn_frm, text="確定", width=9,
                                  command=self._ok, state="disabled",
                                  bg="#004488", fg=C_FG, relief="flat",
                                  activebackground="#0055aa")
        self._ok_btn.pack(side="left", padx=8)
        tk.Button(btn_frm, text="取消", width=9, command=self.destroy,
                  bg="#223344", fg=C_FG, relief="flat",
                  activebackground="#334455").pack(side="left", padx=8)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(
            f"+{(sw - self.winfo_width()) // 2}+{(sh - self.winfo_height()) // 2}")
        self.lift()
        self.focus_force()

    def _load_data(self):
        try:
            county_list, data_tree = _cwa_fetch_tree()
            self._county_list = county_list
            self._data_tree   = data_tree
            self.after(0, self._on_ready)
        except Exception as e:
            self.after(0, lambda msg=str(e): self._status_var.set(f"載入失敗：{msg}"))

    def _on_ready(self):
        self._status_var.set(f"已載入 {len(self._county_list)} 個縣市")
        for i, ccb in enumerate(self._county_cbs):
            ccb["values"] = self._county_list
            ccb["state"]  = "readonly"
            cur = self._cur1 if i == 0 else self._cur2
            if cur[0] in self._county_list:
                ccb.set(cur[0])
                towns = sorted(self._data_tree.get(cur[0], {}).keys())
                self._town_cbs[i]["values"] = towns
                self._town_cbs[i]["state"]  = "readonly"
                if cur[1] in towns:
                    self._town_vars[i].set(cur[1])
        self._ok_btn["state"] = "normal"

    def _on_county(self, col: int):
        county = self._county_vars[col].get()
        towns  = sorted(self._data_tree.get(county, {}).keys())
        self._town_vars[col].set("")
        self._town_cbs[col]["values"] = towns
        self._town_cbs[col]["state"]  = "readonly"

    def _ok(self):
        a1c = self._county_vars[0].get().strip()
        a1t = self._town_vars[0].get().strip()
        a2c = self._county_vars[1].get().strip()
        a2t = self._town_vars[1].get().strip()
        if not a1c or not a1t or not a2c or not a2t:
            messagebox.showerror("錯誤", "請選擇兩個區域的縣市與鄉鎮市區", parent=self)
            return
        self.result = ((a1c, a1t), (a2c, a2t))
        self.destroy()


# ─── 設定對話框 ───────────────────────────────────────────────────────────
class _SetupDialog(tk.Toplevel):
    def __init__(self, parent: tk.Tk, current_od,
                 current_remind_mins: int = 15, current_alpha: int = 75,
                 current_train_count: int = 3,
                 current_show_local: bool = True,
                 current_show_express: bool = True,
                 current_debug: bool = False,
                 current_live_refresh: bool = False,
                 current_show_clock: bool = False,
                 current_auto_close_time: str = "",
                 current_remind_on_time:  str = "",
                 current_remind_off_time: str = "",
                 current_rain_custom: bool = False,
                 current_rain_area1: tuple = ("", ""),
                 current_rain_area2: tuple = ("", "")):
        super().__init__(parent)
        self.result: tuple | None = None
        self._rain_area1 = current_rain_area1
        self._rain_area2 = current_rain_area2
        self.title("設定起訖站點")
        self.resizable(False, False)
        self.wm_attributes("-topmost", True)   # 確保在主視窗上方
        self.grab_set()
        self.configure(bg="#001a2e")

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("S.TCombobox", fieldbackground="#002244",
                        background="#002244", foreground="#cce8ff",
                        selectbackground="#004488")

        pad = {"padx": 14, "pady": 8}

        tk.Label(self, text="起站：", bg="#001a2e", fg=C_FG,
                 font=FONT_TYPE).grid(row=0, column=0, sticky="e", **pad)
        self._from_var = tk.StringVar()
        cb_from = ttk.Combobox(self, textvariable=self._from_var,
                               values=_STATION_LABELS, width=22,
                               style="S.TCombobox")
        cb_from.grid(row=0, column=1, **pad)

        tk.Label(self, text="訖站：", bg="#001a2e", fg=C_FG,
                 font=FONT_TYPE).grid(row=1, column=0, sticky="e", **pad)
        self._to_var = tk.StringVar()
        cb_to = ttk.Combobox(self, textvariable=self._to_var,
                             values=_STATION_LABELS, width=22,
                             style="S.TCombobox")
        cb_to.grid(row=1, column=1, **pad)

        tk.Button(self, text="歷史", command=self._open_history,
                  bg="#003366", fg=C_FG, relief="flat", font=FONT_SMALL,
                  activebackground="#004488").grid(row=0, column=2, rowspan=2,
                                                   padx=(0, 12), sticky="ns")

        # 預填
        if current_od:
            fc, fn, tc, tn = current_od
            self._from_var.set(f"{fn}（{fc}）")
            self._to_var.set(f"{tn}（{tc}）")
        else:
            self._from_var.set("汐科（0970）")
            self._to_var.set("松山（0990）")

        # 提醒時間
        tk.Label(self, text="提醒：", bg="#001a2e", fg=C_FG,
                 font=FONT_TYPE).grid(row=2, column=0, sticky="e", **pad)
        remind_frm = tk.Frame(self, bg="#001a2e")
        remind_frm.grid(row=2, column=1, sticky="w", **pad)
        self._remind_var = tk.IntVar(value=max(0, current_remind_mins))
        tk.Spinbox(remind_frm, from_=0, to=60, width=4,
                   textvariable=self._remind_var,
                   bg="#002244", fg=C_FG, buttonbackground="#003366",
                   relief="flat", font=FONT_TYPE).pack(side="left")
        tk.Label(remind_frm, text=" 分鐘前提醒（0 = 不提醒）",
                 bg="#001a2e", fg=C_DIM, font=FONT_SMALL).pack(side="left")

        # 不透明度
        tk.Label(self, text="透明度：", bg="#001a2e", fg=C_FG,
                 font=FONT_TYPE).grid(row=3, column=0, sticky="e", **pad)
        alpha_frm = tk.Frame(self, bg="#001a2e")
        alpha_frm.grid(row=3, column=1, sticky="w", **pad)
        self._alpha_var = tk.IntVar(value=max(10, min(100, current_alpha)))
        tk.Spinbox(alpha_frm, from_=10, to=100, increment=5, width=4,
                   textvariable=self._alpha_var,
                   bg="#002244", fg=C_FG, buttonbackground="#003366",
                   relief="flat", font=FONT_TYPE).pack(side="left")
        tk.Label(alpha_frm, text=" %（10 = 最透明，100 = 不透明）",
                 bg="#001a2e", fg=C_DIM, font=FONT_SMALL).pack(side="left")

        # 顯示筆數
        tk.Label(self, text="筆數：", bg="#001a2e", fg=C_FG,
                 font=FONT_TYPE).grid(row=4, column=0, sticky="e", **pad)
        count_frm = tk.Frame(self, bg="#001a2e")
        count_frm.grid(row=4, column=1, sticky="w", **pad)
        self._count_var = tk.IntVar(value=max(1, min(10, current_train_count)))
        tk.Spinbox(count_frm, from_=1, to=10, width=4,
                   textvariable=self._count_var,
                   bg="#002244", fg=C_FG, buttonbackground="#003366",
                   relief="flat", font=FONT_TYPE).pack(side="left")
        tk.Label(count_frm, text=" 筆（1–10）",
                 bg="#001a2e", fg=C_DIM, font=FONT_SMALL).pack(side="left")

        # 顯示車種
        filter_frm = tk.Frame(self, bg="#001a2e")
        filter_frm.grid(row=5, column=0, columnspan=2, sticky="w", padx=14, pady=4)
        tk.Label(filter_frm, text="顯示車種：", bg="#001a2e", fg=C_FG,
                 font=FONT_TYPE).pack(side="left")
        self._show_local_var = tk.IntVar(value=1 if current_show_local else 0)
        tk.Checkbutton(filter_frm, text="區間（含區間快）",
                       variable=self._show_local_var,
                       bg="#001a2e", fg=C_FG, selectcolor="#002244",
                       activebackground="#001a2e", activeforeground=C_FG,
                       font=FONT_TYPE).pack(side="left", padx=(0, 12))
        self._show_express_var = tk.IntVar(value=1 if current_show_express else 0)
        tk.Checkbutton(filter_frm, text="對號",
                       variable=self._show_express_var,
                       bg="#001a2e", fg=C_FG, selectcolor="#002244",
                       activebackground="#001a2e", activeforeground=C_FG,
                       font=FONT_TYPE).pack(side="left")

        # 雨量來源
        tk.Label(self, text="雨量來源：", bg="#001a2e", fg=C_FG,
                 font=FONT_TYPE).grid(row=6, column=0, sticky="e", **pad)
        rain_src_frm = tk.Frame(self, bg="#001a2e")
        rain_src_frm.grid(row=6, column=1, sticky="w", **pad)
        self._rain_custom_var = tk.IntVar(value=1 if current_rain_custom else 0)
        tk.Radiobutton(rain_src_frm, text="使用起訖站",
                       variable=self._rain_custom_var, value=0,
                       bg="#001a2e", fg=C_FG, selectcolor="#002244",
                       activebackground="#001a2e", activeforeground=C_FG,
                       font=FONT_TYPE).pack(side="left")
        tk.Radiobutton(rain_src_frm, text="自訂區域",
                       variable=self._rain_custom_var, value=1,
                       bg="#001a2e", fg=C_FG, selectcolor="#002244",
                       activebackground="#001a2e", activeforeground=C_FG,
                       font=FONT_TYPE).pack(side="left", padx=(8, 4))
        tk.Button(rain_src_frm, text="設定…", command=self._open_rain_area,
                  bg="#003366", fg=C_FG, relief="flat",
                  font=FONT_SMALL).pack(side="left")

        # Debug Log
        debug_frm = tk.Frame(self, bg="#001a2e")
        debug_frm.grid(row=7, column=0, columnspan=2, sticky="w", padx=14, pady=4)
        self._debug_var = tk.IntVar(value=1 if current_debug else 0)
        tk.Checkbutton(debug_frm, text="啟用 Debug Log（記錄至 tdx_billboard_debug.log）",
                       variable=self._debug_var,
                       bg="#001a2e", fg="#cc8855", selectcolor="#002244",
                       activebackground="#001a2e", activeforeground="#cc8855",
                       font=FONT_SMALL).pack(side="left")

        # 即時刷新
        live_frm = tk.Frame(self, bg="#001a2e")
        live_frm.grid(row=8, column=0, columnspan=2, sticky="w", padx=14, pady=4)
        self._live_refresh_var = tk.IntVar(value=1 if current_live_refresh else 0)
        tk.Checkbutton(live_frm, text="即時情報",
                       variable=self._live_refresh_var,
                       bg="#001a2e", fg=C_FG, selectcolor="#002244",
                       activebackground="#001a2e", activeforeground=C_FG,
                       font=FONT_SMALL).pack(side="left")
        self._show_clock_var = tk.IntVar(value=1 if current_show_clock else 0)
        tk.Checkbutton(live_frm, text="顯示時間",
                       variable=self._show_clock_var,
                       bg="#001a2e", fg=C_FG, selectcolor="#002244",
                       activebackground="#001a2e", activeforeground=C_FG,
                       font=FONT_SMALL).pack(side="left", padx=(12, 0))

        # 自動關閉時間
        tk.Label(self, text="自動關閉：", bg="#001a2e", fg=C_FG,
                 font=FONT_TYPE).grid(row=9, column=0, sticky="e", **pad)
        close_frm = tk.Frame(self, bg="#001a2e")
        close_frm.grid(row=9, column=1, sticky="w", **pad)
        self._auto_close_var = tk.StringVar(value=current_auto_close_time)
        tk.Entry(close_frm, textvariable=self._auto_close_var, width=6,
                 bg="#002244", fg=C_FG, insertbackground=C_FG,
                 relief="flat", font=FONT_TYPE).pack(side="left")
        tk.Label(close_frm, text="  HH:MM  空白=不啟用",
                 bg="#001a2e", fg=C_DIM, font=FONT_SMALL).pack(side="left")

        # 提醒開始時間
        tk.Label(self, text="提醒開始：", bg="#001a2e", fg=C_FG,
                 font=FONT_TYPE).grid(row=10, column=0, sticky="e", **pad)
        ron_frm = tk.Frame(self, bg="#001a2e")
        ron_frm.grid(row=10, column=1, sticky="w", **pad)
        self._remind_on_var = tk.StringVar(value=current_remind_on_time)
        tk.Entry(ron_frm, textvariable=self._remind_on_var, width=6,
                 bg="#002244", fg=C_FG, insertbackground=C_FG,
                 relief="flat", font=FONT_TYPE).pack(side="left")
        tk.Label(ron_frm, text="  HH:MM  空白=不啟用",
                 bg="#001a2e", fg=C_DIM, font=FONT_SMALL).pack(side="left")

        # 提醒結束時間
        tk.Label(self, text="提醒結束：", bg="#001a2e", fg=C_FG,
                 font=FONT_TYPE).grid(row=11, column=0, sticky="e", **pad)
        roff_frm = tk.Frame(self, bg="#001a2e")
        roff_frm.grid(row=11, column=1, sticky="w", **pad)
        self._remind_off_var = tk.StringVar(value=current_remind_off_time)
        tk.Entry(roff_frm, textvariable=self._remind_off_var, width=6,
                 bg="#002244", fg=C_FG, insertbackground=C_FG,
                 relief="flat", font=FONT_TYPE).pack(side="left")
        tk.Label(roff_frm, text="  HH:MM  空白=不啟用",
                 bg="#001a2e", fg=C_DIM, font=FONT_SMALL).pack(side="left")

        btn_frame = tk.Frame(self, bg="#001a2e")
        btn_frame.grid(row=12, column=0, columnspan=2, pady=10)
        tk.Button(btn_frame, text="確定", width=9, command=self._ok,
                  bg="#004488", fg=C_FG, relief="flat",
                  activebackground="#0055aa").pack(side="left", padx=8)
        tk.Button(btn_frame, text="取消", width=9, command=self.destroy,
                  bg="#223344", fg=C_FG, relief="flat",
                  activebackground="#334455").pack(side="left", padx=8)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        # 居中在畫面（不依賴透明父視窗）
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w  = self.winfo_width()
        h  = self.winfo_height()
        self.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")
        self.lift()
        self.focus_force()

    def _open_rain_area(self):
        dlg = _RainAreaDialog(self,
                              current_area1=self._rain_area1,
                              current_area2=self._rain_area2)
        self.wait_window(dlg)
        if dlg.result:
            self._rain_area1, self._rain_area2 = dlg.result

    def _open_history(self):
        history = _get_od_history()
        if not history:
            messagebox.showinfo("歷史紀錄", "尚無歷史起訖站紀錄", parent=self)
            return
        BG = "#001a2e"
        win = tk.Toplevel(self)
        win.title("歷史起訖站")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.wm_attributes("-topmost", True)
        win.grab_set()
        tk.Label(win, text="選擇歷史起訖站：", bg=BG, fg=C_FG,
                 font=FONT_TYPE).pack(padx=12, pady=(10, 4))
        lb = tk.Listbox(win, bg="#002244", fg=C_FG, selectbackground="#004488",
                        font=FONT_TYPE, width=26, height=min(len(history), 10),
                        relief="flat", activestyle="none", selectmode="single")
        lb.pack(padx=12, pady=(0, 4))
        for fc, fn, tc, tn in history:
            lb.insert(tk.END, f"{fn} → {tn}")
        lb.selection_set(0)
        def _apply(event=None):
            sel = lb.curselection()
            if not sel:
                return
            fc, fn, tc, tn = history[sel[0]]
            _save_od_to_history(fc, fn, tc, tn)
            self._from_var.set(f"{fn}（{fc}）")
            self._to_var.set(f"{tn}（{tc}）")
            win.destroy()
        lb.bind("<Double-Button-1>", _apply)
        lb.bind("<Return>", _apply)
        btn_frm = tk.Frame(win, bg=BG)
        btn_frm.pack(pady=(2, 10))
        tk.Button(btn_frm, text="套用", width=8, command=_apply,
                  bg="#004488", fg=C_FG, relief="flat",
                  activebackground="#0055aa").pack(side="left", padx=6)
        tk.Button(btn_frm, text="取消", width=8, command=win.destroy,
                  bg="#223344", fg=C_FG, relief="flat",
                  activebackground="#334455").pack(side="left", padx=6)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.update_idletasks()
        x = self.winfo_rootx() + self.winfo_width() + 4
        y = self.winfo_rooty()
        win.geometry(f"+{x}+{y}")
        win.lift()
        lb.focus_set()

    def _ok(self):
        def parse(s: str) -> tuple[str, str] | tuple[None, None]:
            m = re.match(r"^(.+?)（(\d+)）$", s.strip())
            if m:
                return m.group(2), m.group(1)
            name = s.strip()
            if name in STATIONS:
                return STATIONS[name], name
            return None, None

        fc, fn = parse(self._from_var.get())
        tc, tn = parse(self._to_var.get())
        if not fc or not tc:
            messagebox.showerror("錯誤", "請選擇有效的車站", parent=self)
            return
        if fc == tc:
            messagebox.showerror("錯誤", "起訖站不能相同", parent=self)
            return
        try:
            remind_mins = max(0, int(self._remind_var.get()))
        except (ValueError, tk.TclError):
            remind_mins = 0
        try:
            alpha_pct = min(100, max(10, int(self._alpha_var.get())))
        except (ValueError, tk.TclError):
            alpha_pct = 75
        try:
            train_count = min(10, max(1, int(self._count_var.get())))
        except (ValueError, tk.TclError):
            train_count = 3
        show_local   = bool(self._show_local_var.get())
        show_express = bool(self._show_express_var.get())
        auto_close  = self._auto_close_var.get().strip()
        remind_on   = self._remind_on_var.get().strip()
        remind_off  = self._remind_off_var.get().strip()

        def _hhmm_ok(val: str, label: str) -> bool:
            if not val:
                return True
            if not re.match(r'^([01]\d|2[0-3]):([0-5]\d)$', val):
                messagebox.showerror("格式錯誤",
                    f"「{label}」格式錯誤，請輸入 HH:MM（24 小時制，例如 08:30）",
                    parent=self)
                return False
            return True

        if not _hhmm_ok(auto_close, "自動關閉"): return
        if not _hhmm_ok(remind_on,  "提醒開始"): return
        if not _hhmm_ok(remind_off, "提醒結束"): return
        cfg = _load_cfg()
        sec = "tdx_billboard"
        if sec not in cfg:
            cfg[sec] = {}
        cfg[sec]["from_code"]      = fc
        cfg[sec]["from_name"]      = fn
        cfg[sec]["to_code"]        = tc
        cfg[sec]["to_name"]        = tn
        cfg[sec]["remind_mins"]    = str(remind_mins)
        cfg[sec]["alpha_pct"]      = str(alpha_pct)
        cfg[sec]["train_count"]    = str(train_count)
        cfg[sec]["show_local"]     = "1" if show_local else "0"
        cfg[sec]["show_express"]   = "1" if show_express else "0"
        cfg[sec]["debug_log"]        = "1" if bool(self._debug_var.get()) else "0"
        cfg[sec]["live_refresh"]     = "1" if bool(self._live_refresh_var.get()) else "0"
        cfg[sec]["show_clock"]       = "1" if bool(self._show_clock_var.get()) else "0"
        cfg[sec]["auto_close_time"]  = auto_close
        cfg[sec]["remind_on_time"]   = remind_on
        cfg[sec]["remind_off_time"]  = remind_off
        cfg[sec]["rain_custom"]      = "1" if bool(self._rain_custom_var.get()) else "0"
        cfg[sec]["rain_area1_county"] = self._rain_area1[0]
        cfg[sec]["rain_area1_town"]   = self._rain_area1[1]
        cfg[sec]["rain_area2_county"] = self._rain_area2[0]
        cfg[sec]["rain_area2_town"]   = self._rain_area2[1]
        _write_cfg(cfg)
        _save_od_to_history(fc, fn, tc, tn)
        self.result = (fc, fn, tc, tn)
        self.destroy()


# ─── 主視窗 ───────────────────────────────────────────────────────────────
class SimpleApp(tk.Tk):
    _WIN_W      = 310
    _REFRESH_MS = 60_000   # 每分鐘自動重整

    # ── 欄位寬度（字元數，方便日後調整）─────────────────────────────────
    _COL_TYPE  = 5   # 車種（區間、區間快、自強…）
    _COL_NO    = 5   # 車次號碼
    _COL_DEP   = 6   # 出發時間
    _COL_ARR   = 6   # 到達時間
    _COL_DELAY = 5   # 誤點（+Xmin）

    # ── 前三列：列背景色 & 發車時間前景色 ──────────────────────────────────
    _ROW_STYLES: tuple = (
        {"row_bg": "#3a0000", "dep_fg": "#ffff44"},  # 第1筆：亮黃 + 暗紅底
        {"row_bg": "#3a3000", "dep_fg": "#44ff88"},  # 第2筆：亮綠 + 暗黃底
        {"row_bg": "#003a00", "dep_fg": "#ff9933"},  # 第3筆：亮橘 + 暗綠底
    )

    def __init__(self):
        super().__init__()
        # ── 依欄位常數與字型量測動態計算視窗寬度 ──────────────────────────
        _ft = tkfont.Font(family="微軟正黑體", size=13, weight="bold")  # FONT_TRAIN
        _fc = tkfont.Font(family="微軟正黑體", size=11)               # FONT_TYPE
        cw_type  = _fc.measure("0")   # FONT_TYPE 一字元寬
        cw_train = _ft.measure("0")   # FONT_TRAIN 一字元寬
        arrow_w  = _fc.measure("→") + 6  # 符號寬 + padx=3×2
        body_pad = 16                   # 內文議區 padx=8×2
        no_pad   = 8                    # lbl_no padx=(2,6)
        self._WIN_W = (
            self._COL_TYPE * cw_type
            + self._COL_NO * cw_train + no_pad
            + self._COL_DEP * cw_train
            + arrow_w
            + self._COL_ARR * cw_train
            + self._COL_DELAY * cw_type
            + body_pad
        )
        self.overrideredirect(True)            # 無邊框
        self.configure(bg=C_BG)
        self.wm_attributes("-topmost", True)   # 置頂
        self.wm_attributes("-alpha",  _get_prefs()["alpha"] / 100)   # 不透明度（預設 75%）
        self.update()                          # 建立 Win32 HWND，子對話框才能正常顯示
        self.withdraw()                        # 先隱藏，等 OD 設定完再顯示

        self._from_code = ""
        self._from_name = ""
        self._to_code   = ""
        self._to_name   = ""
        self._loading   = False
        self._fetch_gen: int = 0
        self._refresh_id: str | None = None
        self._remind_mins:        int  = 0
        self._alpha:              int  = 75
        self._train_count:        int  = _get_prefs()["train_count"]
        self._show_local:         bool = True
        self._show_express:       bool = True
        self._reminded:           set[str] = set()
        self._today_str:          str  = date.today().isoformat()
        self._delays:             dict = {}
        self._rain_refresh_id:    str | None = None
        self._live_refresh:       bool            = False
        self._auto_close_time:    str             = ""
        self._remind_on_time:     str             = ""
        self._remind_off_time:    str             = ""
        self._sched_id:           str | None      = None
        self._show_clock:         bool            = False
        self._clock_id:           str | None      = None
        self._rain_custom:        bool            = False
        self._rain_area1:         tuple           = ("", "")
        self._rain_area2:         tuple           = ("", "")


        x, y = _get_win_pos()
        self.geometry(f"+{x}+{y}")
        self.minsize(self._WIN_W, 1)

        self._build_ui()
        self._load_config()

    # ── UI 建構 ───────────────────────────────────────────────────────────
    def _build_ui(self):
        # Header bar
        hdr = tk.Frame(self, bg=C_HDR, height=30)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        self._title_lbl = tk.Label(hdr, text="台鐵時刻", bg=C_HDR, fg=C_FG,
                                   font=FONT_HDR)
        self._title_lbl.pack(side="left", padx=10)

        self._clock_lbl = tk.Label(hdr, text="", bg=C_HDR, fg="#00aaff",
                                   font=FONT_HDR, anchor="center")
        self._clock_lbl.pack(side="left", expand=True)

        btn_x = tk.Label(hdr, text="✕", bg=C_HDR, fg=C_DIM,
                         font=FONT_HDR, cursor="hand2")
        btn_x.pack(side="right", padx=8)
        btn_x.bind("<Button-1>", lambda e: self._on_close())

        btn_cfg = tk.Label(hdr, text="⚙", bg=C_HDR, fg=C_DIM,
                           font=FONT_HDR, cursor="hand2")
        btn_cfg.pack(side="right", padx=4)
        btn_cfg.bind("<Button-1>", lambda e: self._open_setup())

        btn_hist = tk.Label(hdr, text="☰", bg=C_HDR, fg=C_DIM,
                            font=FONT_HDR, cursor="hand2")
        btn_hist.pack(side="right", padx=4)
        btn_hist.bind("<Button-1>", lambda e: self._open_history_quick())

        btn_swap = tk.Label(hdr, text="⇄", bg=C_HDR, fg=C_DIM,
                            font=FONT_HDR, cursor="hand2")
        btn_swap.pack(side="right", padx=4)
        btn_swap.bind("<Button-1>", lambda e: self._swap_od())

        # Drag on header
        for w in (hdr, self._title_lbl, self._clock_lbl):
            w.bind("<ButtonPress-1>",  self._drag_start)
            w.bind("<B1-Motion>",      self._drag_move)

        # Separator line
        tk.Frame(self, bg=C_SEP, height=1).pack(fill="x")

        # Train rows (dynamic, stored for rebuild)
        self._body = tk.Frame(self, bg=C_BG)
        self._body.pack(fill="both", expand=True, padx=8, pady=6)
        self._rows: list[dict] = []
        self._build_rows()

        # Rain bar（1 小時降雨量）
        tk.Frame(self, bg=C_SEP, height=1).pack(fill="x")
        rain_frm = tk.Frame(self, bg="#002244")
        rain_frm.pack(fill="x")
        self._rain_lbl = tk.Label(
            rain_frm, text="☔ —",
            bg="#002244", fg="#66ddff",
            font=("微軟正黑體", 10, "bold"), anchor="center")
        self._rain_lbl.pack(fill="x", padx=0, pady=1)

        # Separator + status bar
        tk.Frame(self, bg=C_SEP, height=1).pack(fill="x")

        status_frm = tk.Frame(self, bg=C_HDR, height=22)
        status_frm.pack(fill="x")
        status_frm.pack_propagate(False)
        status_frm.grid_columnconfigure(1, weight=1)

        self._refresh_btn = tk.Label(
            status_frm,
            text="↻",
            bg=C_HDR,
            fg=C_DIM,
            font=FONT_SMALL,
            cursor="hand2",
        )
        self._refresh_btn.grid(row=0, column=0, padx=(8, 6), sticky="w")
        self._refresh_btn.bind("<Button-1>", lambda e: self._start_fetch())

        self._status_lbl = tk.Label(
            status_frm,
            text="讀取中…",
            bg=C_HDR,
            fg=C_DIM,
            font=FONT_SMALL,
            anchor="w",
        )
        self._status_lbl.grid(row=0, column=1, sticky="we")

        self._copyright_lbl = tk.Label(
            status_frm,
            text=f"v{_APP_VERSION}  {_APP_COPYRIGHT}",
            bg=C_HDR,
            fg=C_DIM,
            font=FONT_SMALL,
            anchor="e",
        )
        self._copyright_lbl.grid(row=0, column=2, padx=(6, 8), sticky="e")

    # ── 列車行 建構 / 重建 ────────────────────────────────────────────────
    def _build_rows(self):
        for w in self._body.winfo_children():
            w.destroy()
        self._rows = []
        for idx in range(self._train_count):
            style  = self._ROW_STYLES[idx] if idx < len(self._ROW_STYLES) else None
            row_bg = style["row_bg"] if style else C_BG
            dep_fg = style["dep_fg"] if style else C_FG

            frm = tk.Frame(self._body, bg=row_bg)
            frm.pack(fill="x", pady=2)

            lbl_type = tk.Label(frm, text="",  bg=row_bg, fg=C_LOCAL,
                                font=FONT_TYPE, width=self._COL_TYPE, anchor="w")
            lbl_type.pack(side="left")

            lbl_no = tk.Label(frm, text="",    bg=row_bg, fg=C_FG,
                              font=FONT_TRAIN, width=self._COL_NO, anchor="w")
            lbl_no.pack(side="left", padx=(2, 6))

            lbl_dep = tk.Label(frm, text="",   bg=row_bg, fg=dep_fg,
                               font=FONT_TRAIN, width=self._COL_DEP, anchor="center")
            lbl_dep.pack(side="left")

            lbl_arrow = tk.Label(frm, text="→", bg=row_bg, fg=C_DIM,
                                 font=FONT_TYPE)
            lbl_arrow.pack(side="left", padx=3)

            lbl_arr = tk.Label(frm, text="",   bg=row_bg, fg=C_DIM,
                               font=FONT_TRAIN, width=self._COL_ARR, anchor="center")
            lbl_arr.pack(side="left")

            lbl_delay = tk.Label(frm, text="", bg=row_bg, fg="#ff6666",
                                 font=FONT_TYPE, width=self._COL_DELAY, anchor="w")
            lbl_delay.pack(side="left", padx=(4, 0))

            self._rows.append({"frame": frm, "style": style,
                               "type": lbl_type,  "no": lbl_no,
                               "dep":  lbl_dep,   "arrow": lbl_arrow,
                               "arr":  lbl_arr,   "delay": lbl_delay})

    # ── 設定載入 & OD ─────────────────────────────────────────────────────
    def _apply_prefs(self, prefs: dict) -> None:
        """將 _get_prefs() 結果套用至 self 及全域 _debug_enabled。"""
        global _debug_enabled
        self._remind_mins     = prefs["remind_mins"]
        self._alpha           = prefs["alpha"]
        self._train_count     = prefs["train_count"]
        self._show_local      = prefs["show_local"]
        self._show_express    = prefs["show_express"]
        _debug_enabled        = prefs["debug_log"]
        self._live_refresh    = prefs["live_refresh"]
        self._auto_close_time = prefs["auto_close_time"]
        self._remind_on_time  = prefs["remind_on_time"]
        self._remind_off_time = prefs["remind_off_time"]
        self._rain_custom     = prefs["rain_custom"]
        self._rain_area1      = (prefs["rain_area1_county"], prefs["rain_area1_town"])
        self._rain_area2      = (prefs["rain_area2_county"], prefs["rain_area2_town"])
        self._show_clock      = prefs["show_clock"]
        if self._show_clock:
            self._start_clock()
        else:
            self._stop_clock()

    def _load_config(self):
        od = _get_od()
        self._apply_prefs(_get_prefs())
        if _debug_enabled:
            _rotate_debug_log()   # 超過 7 天先改名，再開新 log
            try:
                with _debug_lock:
                    sep = "─" * 60
                    with open(DEBUG_LOG, "a", encoding="utf-8") as f:
                        f.write(f"\n{sep}\n"
                                f"[{datetime.now():%Y-%m-%d %H:%M:%S}] tdx_billboard 啟動\n"
                                f"{sep}\n")
            except Exception:
                pass
        _dlog(f"_load_config: OD={'有' if od else '無'} "
              f"remind={self._remind_mins} alpha={self._alpha} "
              f"count={self._train_count} debug={_debug_enabled}")
        self.wm_attributes("-alpha", self._alpha / 100)
        if od:
            self._from_code, self._from_name, self._to_code, self._to_name = od
            self._update_title()
            self.after(0, self._show)   # OD 已有設定，直接顯示主視窗
            self._start_fetch()
            self._start_rain_fetch()
            self._arm_schedule()
        else:
            # OD 未設定：先開設定對話框，完成後再顯示主視窗
            self.after(100, self._open_setup)

    def _open_history_quick(self):
        """主視窗歷史按鈕：直接選取後套用 OD 並立即刷新，無需開啟完整設定對話框。"""
        history = _get_od_history()
        if not history:
            return
        BG = "#001a2e"
        win = tk.Toplevel(self)
        win.overrideredirect(False)
        win.title("歷史起訖站")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.wm_attributes("-topmost", True)
        win.grab_set()
        tk.Label(win, text="選擇歷史起訖站：", bg=BG, fg=C_FG,
                 font=FONT_TYPE).pack(padx=12, pady=(10, 4))
        lb = tk.Listbox(win, bg="#002244", fg=C_FG, selectbackground="#004488",
                        font=FONT_TYPE, width=26, height=min(len(history), 10),
                        relief="flat", activestyle="none", selectmode="single")
        lb.pack(padx=12, pady=(0, 4))
        for fc, fn, tc, tn in history:
            lb.insert(tk.END, f"{fn} → {tn}")
        lb.selection_set(0)
        def _apply(event=None):
            sel = lb.curselection()
            if not sel:
                return
            fc, fn, tc, tn = history[sel[0]]
            win.destroy()
            _save_od_to_history(fc, fn, tc, tn)
            self._from_code, self._from_name = fc, fn
            self._to_code,   self._to_name   = tc, tn
            _save_od(fc, fn, tc, tn)
            self._update_title()
            self._start_fetch()
            self._start_rain_fetch()
        lb.bind("<Double-Button-1>", _apply)
        lb.bind("<Return>", _apply)
        btn_frm = tk.Frame(win, bg=BG)
        btn_frm.pack(pady=(2, 10))
        tk.Button(btn_frm, text="套用", width=8, command=_apply,
                  bg="#004488", fg=C_FG, relief="flat",
                  activebackground="#0055aa").pack(side="left", padx=6)
        tk.Button(btn_frm, text="取消", width=8, command=win.destroy,
                  bg="#223344", fg=C_FG, relief="flat",
                  activebackground="#334455").pack(side="left", padx=6)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.update_idletasks()
        x = self.winfo_x()
        y = self.winfo_y() + self.winfo_height() + 4
        win.geometry(f"+{x}+{y}")
        win.lift()
        lb.focus_set()

    def _open_setup(self):
        _dlog("_open_setup: 開啟設定對話框")
        od = (self._from_code, self._from_name, self._to_code, self._to_name)
        dlg = _SetupDialog(self, od if self._from_code else None,
                           current_remind_mins=self._remind_mins,
                           current_alpha=self._alpha,
                           current_train_count=self._train_count,
                           current_show_local=self._show_local,
                           current_show_express=self._show_express,
                           current_debug=_debug_enabled,
                           current_live_refresh=self._live_refresh,
                           current_show_clock=self._show_clock,
                           current_auto_close_time=self._auto_close_time,
                           current_remind_on_time=self._remind_on_time,
                           current_remind_off_time=self._remind_off_time,
                           current_rain_custom=self._rain_custom,
                           current_rain_area1=self._rain_area1,
                           current_rain_area2=self._rain_area2)
        self.wait_window(dlg)
        if dlg.result:
            self._from_code, self._from_name, self._to_code, self._to_name = dlg.result
            old_count = self._train_count
            self._apply_prefs(_get_prefs())
            _dlog(lambda: (
                f"_open_setup: 設定完成 "
                f"from={self._from_name}({self._from_code}) to={self._to_name}({self._to_code}) "
                f"remind={self._remind_mins}min alpha={self._alpha}% count={self._train_count} "
                f"local={self._show_local} express={self._show_express} "
                f"live_refresh={self._live_refresh} debug={_debug_enabled} "
                f"auto_close={self._auto_close_time!r} "
                f"remind_on={self._remind_on_time!r} remind_off={self._remind_off_time!r} "
                f"rain_custom={self._rain_custom}"
            ))
            self.wm_attributes("-alpha", self._alpha / 100)
            if self._train_count != old_count:
                self._build_rows()
            self._update_title()
            self._show()
            self._start_fetch()
            self._start_rain_fetch()
            self._arm_schedule()
        elif not self._from_code:
            # 使用者取消且 OD 從未設定 → 關閉程式
            self._on_close()

    def _show(self):
        """顯示主視窗（overrideredirect 後需 deiconify + lift 才能正常出現）。"""
        self.deiconify()
        self.lift()
        self.update_idletasks()

    def _swap_od(self):
        if not self._from_code:
            return
        self._from_code, self._to_code = self._to_code, self._from_code
        self._from_name, self._to_name = self._to_name, self._from_name
        _save_od(self._from_code, self._from_name, self._to_code, self._to_name)
        self._update_title()
        self._start_fetch()
        self._start_rain_fetch()

    def _update_title(self):
        self._title_lbl.config(
            text=f"{self._from_name} → {self._to_name}")

    # ── 資料取得 ──────────────────────────────────────────────────────────
    def _start_fetch(self):
        _dlog("_start_fetch: 觸發班表下載")
        if self._refresh_id:
            self.after_cancel(self._refresh_id)
            self._refresh_id = None
        self._fetch_gen += 1
        gen = self._fetch_gen
        if self._loading:
            # worker 執行中：提升世代讓舊結果作廢，新 worker 等舊的結束後由計時器觸發
            _dlog(f"_start_fetch: 載入中，標記世代 {gen}，等舊 worker 完成後重啟")
            return
        self._loading = True
        self._status_lbl.config(text="更新中…")
        threading.Thread(target=self._worker, args=(gen,), daemon=True).start()

    def _worker(self, gen: int):
        global CLIENT_ID, CLIENT_SECRET, _CWA_APIKEY, _NO_CREDENTIALS
        # 每次執行前重新讀取金鑰，允許不重啟直接修改 INI 即生效
        CLIENT_ID, CLIENT_SECRET, _CWA_APIKEY = _load_credentials()
        _NO_CREDENTIALS = not (CLIENT_ID and CLIENT_SECRET)
        _dlog(f"_worker[{gen}]: 開始 {self._from_name}→{self._to_name} no_creds={_NO_CREDENTIALS}")
        try:
            all_trains, cache_warn = _get_all_trains()
            trains = _next_local_trains(
                all_trains, self._from_code, self._to_code, self._train_count,
                show_local=self._show_local, show_express=self._show_express)
            _dlog(lambda: f"_worker[{gen}]: 完成，班次={[t['no'] for t in trains]}")
            if self._live_refresh and not _NO_CREDENTIALS:
                delays = _fetch_live_delays(self._from_code)
                _dlog(f"_worker[{gen}]: 誤點完成 {len(delays)} 班")
            else:
                delays = {}
                _dlog(f"_worker[{gen}]: 即時情報關閉，略過 LiveBoard")
            self.after(0, self._on_done, gen, trains, delays, None, cache_warn)
        except Exception as ex:
            _dlog(f"_worker[{gen}]: 失敗 {ex}")
            self.after(0, self._on_done, gen, [], {}, str(ex), None)

    def _on_done(self, gen: int, trains: list, delays: dict, err: str | None,
                 cache_warn: str | None = None):
        self._loading = False
        if gen != self._fetch_gen:
            _dlog(f"_on_done[{gen}]: 世代不符（目前 {self._fetch_gen}），重新取得")
            self._start_fetch()
            return
        self._delays  = delays
        self._render(trains)
        self._check_reminder(trains)
        next_ms     = self._next_event_ms(trains)
        next_time_s = (datetime.now() + timedelta(milliseconds=next_ms)).strftime("%H:%M:%S")
        if err:
            _dlog(f"_on_done[{gen}]: 錯誤 {err}")
            # 只取第一行，防止多行訊息溢出狀態列
            short = err.split("\n")[0]
            self._status_lbl.config(text=f"⚠ {short}", foreground="#ff6666")
        elif cache_warn:
            _dlog(f"_on_done[{gen}]: 快取警告 {cache_warn}")
            self._status_lbl.config(text=cache_warn, foreground="#ffaa33")
        else:
            now_s = datetime.now().strftime("%H:%M")
            self._status_lbl.config(
                text=f"更新: {now_s}　下次: {next_time_s}",
                foreground=C_DIM)
        _dlog(f"_on_done[{gen}]: 設定計時器 {next_ms} ms 後重整（約 {next_ms//1000} 秒）")
        self._refresh_id = self.after(next_ms, self._start_fetch)

    def _next_event_ms(self, trains: list) -> int:
        """計算距離下一個事件（發車移除 or 提醒觸發）的毫秒數。
        live_refresh 啟用時上限 _REFRESH_MS（確保延誤資訊定期更新）；
        live_refresh 停用時不設上限，只在下一個事件觸發才更新。"""
        now       = datetime.now()
        today_str = date.today().isoformat()
        milestones: list[float] = []
        eff_mins = self._remind_effective_mins   # cache once, avoids repeated datetime.now() calls
        for t in trains:
            dep_str = t.get("dep", "")
            if not dep_str:
                continue
            try:
                dep_dt = datetime.strptime(f"{today_str} {dep_str}", "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            # 發車時刻到達 → 需從畫面移除
            secs_to_dep = (dep_dt - now).total_seconds()
            if secs_to_dep > 0:
                milestones.append(secs_to_dep)
            # 提醒門檻 → 需要彈出提醒
            if eff_mins > 0:
                key = f"{t['no']}_{today_str}"
                if key not in self._reminded:
                    secs_remind = (dep_dt - timedelta(minutes=eff_mins) - now).total_seconds()
                    if secs_remind > 0:
                        milestones.append(secs_remind)
        if not milestones:
            _dlog(f"_next_event_ms: 無里程碑，使用預設 {self._REFRESH_MS} ms")
            return self._REFRESH_MS
        next_ms = int((min(milestones) + 1) * 1000)
        # live_refresh 啟用：加上 _REFRESH_MS 上限保持延誤資訊更新頻率
        result_ms = min(next_ms, self._REFRESH_MS) if self._live_refresh else next_ms
        _dlog(lambda: f"_next_event_ms: 最近事件 {min(milestones):.0f}s 後，計時器 {result_ms} ms (live={self._live_refresh})")
        return result_ms

    # ── 標題欄時鐘 ────────────────────────────────────────────────────────
    def _start_clock(self):
        if self._clock_id:
            self.after_cancel(self._clock_id)
            self._clock_id = None
        now = datetime.now()
        self._clock_lbl.config(text=now.strftime("%H:%M"))
        secs = 60 - now.second
        self._clock_id = self.after(secs * 1000, self._tick_clock)

    def _tick_clock(self):
        self._clock_id = None
        if self._show_clock:
            self._start_clock()

    def _stop_clock(self):
        if self._clock_id:
            self.after_cancel(self._clock_id)
            self._clock_id = None
        self._clock_lbl.config(text="")

    # ── 1 小時降雨量 ──────────────────────────────────────────────────────
    def _start_rain_fetch(self):
        if self._from_name:
            _dlog(f"_start_rain_fetch: 觸發降雨查詢 {self._from_name}/{self._to_name}")
            threading.Thread(target=self._rain_worker, daemon=True).start()

    def _rain_worker(self):
        global _CWA_APIKEY
        _, _, _CWA_APIKEY = _load_credentials()
        area1 = self._rain_area1 if (self._rain_custom and self._rain_area1[0]) else None
        area2 = self._rain_area2 if (self._rain_custom and self._rain_area2[0]) else None
        f10, f1h, t10, t1h = _fetch_rain_both(self._from_name, self._to_name,
                                               area1=area1, area2=area2)
        self.after(0, self._on_rain_done, f10, f1h, t10, t1h)

    def _on_rain_done(self, from_10: str, from_1h: str, to_10: str, to_1h: str):
        _dlog(f"_on_rain_done: from 10min={from_10} 1hr={from_1h}  to 10min={to_10} 1hr={to_1h}")
        if self._rain_custom and self._rain_area1[0]:
            name1 = self._rain_area1[1] or self._rain_area1[0]
            name2 = self._rain_area2[1] or self._rain_area2[0]
        else:
            name1 = self._from_name
            name2 = self._to_name
        text = f"☔ {name1} {from_10}/{from_1h}mm | {name2} {to_10}/{to_1h}mm"
        self._rain_lbl.config(text=text)
        if self._rain_refresh_id:
            self.after_cancel(self._rain_refresh_id)
        self._rain_refresh_id = self.after(_RAIN_INTERVAL_MS, self._start_rain_fetch)


    def _render(self, trains: list):
        # 每次渲染前依當前時間重新過濾，確保已過站班次不殘留
        now_str = datetime.now().strftime("%H:%M")
        trains  = [t for t in trains if t["dep"] >= now_str]
        _dlog(f"_render: 顯示 {len(trains)} 班（過濾後）")
        no_data = not trains
        for i, row in enumerate(self._rows):
            style = row["style"]
            if i < len(trains):
                t      = trains[i]
                row_bg = style["row_bg"] if style else C_BG
                dep_fg = style["dep_fg"] if style else C_FG
                row["frame"].config(bg=row_bg)
                row["type"].config( bg=row_bg, text=t["type"], fg=t["color"])
                row["no"].config(   bg=row_bg, text=t["no"])
                row["dep"].config(  bg=row_bg, text=t["dep"], fg=dep_fg)
                row["arrow"].config(bg=row_bg)
                row["arr"].config(  bg=row_bg, text=t["arr"])
                delay = self._delays.get(t["no"], 0)
                row["delay"].config(bg=row_bg, text=f"+{delay}分" if delay > 0 else "")
            else:
                row["frame"].config(bg=C_BG)
                row["type"].config( bg=C_BG, text="")
                row["no"].config(   bg=C_BG, text="")
                if no_data and i == 0:
                    row["dep"].config(bg=C_BG, text="今日已無班次", fg="#666666")
                else:
                    row["dep"].config(bg=C_BG, text="", fg=C_FG)
                row["arrow"].config(bg=C_BG)
                row["arr"].config(  bg=C_BG, text="")
                row["delay"].config(bg=C_BG, text="")

    # ── 排程 (自動關閉 / 提醒時段) ────────────────────────────────────────
    def _arm_schedule(self):
        """若設定了自動關閉時間，計算今日剩餘秒數並安排 after() 計時器。"""
        if self._sched_id:
            self.after_cancel(self._sched_id)
            self._sched_id = None
        if not self._auto_close_time:
            return
        now = datetime.now()
        today_str = date.today().isoformat()
        try:
            target = datetime.strptime(f"{today_str} {self._auto_close_time}", "%Y-%m-%d %H:%M")
            secs = (target - now).total_seconds()
            if secs > 0:
                self._sched_id = self.after(int(secs * 1000) + 500, self._tick_schedule)
                _dlog(f"_arm_schedule: 自動關閉於 {self._auto_close_time}，{secs:.0f}s 後")
        except ValueError:
            pass

    def _tick_schedule(self):
        self._sched_id = None
        _dlog(f"_tick_schedule: 到達自動關閉時間 {self._auto_close_time}，關閉程式")
        self._on_close()

    @property
    def _remind_effective_mins(self) -> int:
        """依提醒開始/結束時間判斷當前是否在提醒時段內。"""
        if self._remind_mins <= 0:
            return 0
        now_hhmm = datetime.now().strftime("%H:%M")
        on_t  = self._remind_on_time
        off_t = self._remind_off_time
        if on_t and off_t:
            if on_t <= off_t:
                active = on_t <= now_hhmm < off_t          # 同日區間
            else:
                active = now_hhmm >= on_t or now_hhmm < off_t  # 跨日區間（如 22:00-06:00）
        elif on_t:
            active = now_hhmm >= on_t
        elif off_t:
            active = now_hhmm < off_t
        else:
            active = True
        return self._remind_mins if active else 0

    # ── 提醒 ──────────────────────────────────────────────────────────────
    def _show_toast(self, train_type: str, train_no: str,
                     from_name: str, dep_str: str, mins_until: int) -> None:
        """以獨立置頂視窗顯示班車提醒，置中螢幕，需按確定才關閉。"""
        BG       = "#0d2a0d"
        FG_HDR   = "#ffffff"
        FG_TYPE  = "#88ff88"   # 車種
        FG_NO    = "#ffff44"   # 車次 → 黃色
        FG_STA   = "#88ff88"   # 站名
        FG_TIME  = "#ff4444"   # 時間 → 紅色
        FG_DEP   = "#ff9933"   # 「出發」文字 → 橘色
        FG_MINS  = "#88ff88"   # 倒數
        FG_BTN   = "#ccffcc"
        BG_BTN   = "#1e7a1e"
        BG_BTN_A = "#28a028"
        FONT_TOAST_HDR  = ("微軟正黑體", 18, "bold")
        FONT_TOAST_BODY = ("微軟正黑體", 22, "bold")
        FONT_TOAST_BTN  = ("微軟正黑體", 14, "bold")

        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.wm_attributes("-topmost", True)
        win.configure(bg=BG)

        # 外框（亮綠邊線）
        border = tk.Frame(win, bg="#33cc33", padx=3, pady=3)
        border.pack(fill="both", expand=True)
        inner = tk.Frame(border, bg=BG)
        inner.pack(fill="both", expand=True)

        # 標題列
        tk.Label(inner, text="⏰  班車提醒", bg=BG, fg=FG_HDR,
                 font=FONT_TOAST_HDR, pady=10).pack(fill="x")
        tk.Frame(inner, bg="#33cc33", height=1).pack(fill="x", padx=16)

        # 第一行：車種 ＋ 車次（黃）＋「次」
        row1 = tk.Frame(inner, bg=BG)
        row1.pack(pady=(12, 2))
        tk.Label(row1, text=train_type, bg=BG, fg=FG_TYPE,
                 font=FONT_TOAST_BODY).pack(side="left")
        tk.Label(row1, text=f" {train_no} ", bg=BG, fg=FG_NO,
                 font=FONT_TOAST_BODY).pack(side="left")
        tk.Label(row1, text="次", bg=BG, fg=FG_TYPE,
                 font=FONT_TOAST_BODY).pack(side="left")

        # 第二行：站名 ＋ 時間（紅）＋「出發」（橘）
        row2 = tk.Frame(inner, bg=BG)
        row2.pack(pady=2)
        tk.Label(row2, text=f"{from_name}  ", bg=BG, fg=FG_STA,
                 font=FONT_TOAST_BODY).pack(side="left")
        tk.Label(row2, text=dep_str, bg=BG, fg=FG_TIME,
                 font=FONT_TOAST_BODY).pack(side="left")
        tk.Label(row2, text="  出發", bg=BG, fg=FG_DEP,
                 font=FONT_TOAST_BODY).pack(side="left")

        # 第三行：倒數分鐘
        tk.Label(inner, text=f"還有約 {mins_until} 分鐘！", bg=BG, fg=FG_MINS,
                 font=FONT_TOAST_BODY, pady=4).pack()

        tk.Frame(inner, bg="#226622", height=1).pack(fill="x", padx=16, pady=(8, 0))

        dismiss = lambda w=win: w.destroy() if w.winfo_exists() else None
        tk.Button(inner, text="確  定", width=12, command=dismiss,
                  bg=BG_BTN, fg=FG_BTN, relief="flat",
                  activebackground=BG_BTN_A, activeforeground="#ffffff",
                  font=FONT_TOAST_BTN, pady=8).pack(pady=14)

        # 置中螢幕
        win.update_idletasks()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        win.geometry(f"+{(sw - win.winfo_width()) // 2}+{(sh - win.winfo_height()) // 2}")
        win.lift()
        win.focus_force()

    def _check_reminder(self, trains: list):
        today_str = date.today().isoformat()
        if today_str != self._today_str:
            self._today_str = today_str
            self._reminded.clear()
        if self._remind_effective_mins <= 0 or not trains:
            return
        _dlog(f"_check_reminder: 檢查 {len(trains)} 班（提醒 {self._remind_effective_mins} 分前）")
        now = datetime.now()
        for t in trains:
            dep_str = t.get("dep", "")
            if not dep_str:
                continue
            try:
                dep_dt = datetime.strptime(f"{today_str} {dep_str}", "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            mins_until = (dep_dt - now).total_seconds() / 60
            key = f"{t['no']}_{today_str}"
            if 0 < mins_until <= self._remind_effective_mins and key not in self._reminded:
                self._reminded.add(key)
                self._show_toast(
                    train_type=t["type"].strip(),
                    train_no=t["no"],
                    from_name=self._from_name,
                    dep_str=dep_str,
                    mins_until=int(mins_until),
                )

    # ── 拖曳 ──────────────────────────────────────────────────────────────
    def _drag_start(self, e):
        self._dx = e.x
        self._dy = e.y

    def _drag_move(self, e):
        x = self.winfo_x() + (e.x - self._dx)
        y = self.winfo_y() + (e.y - self._dy)
        self.geometry(f"+{x}+{y}")

    # ── 關閉 ──────────────────────────────────────────────────────────────
    def _on_close(self):
        x, y = self.winfo_x(), self.winfo_y()
        _save_win_pos(x, y)
        if _debug_enabled:
            try:
                cfg = _load_cfg()
                sec = "tdx_billboard"
                ini = dict(cfg[sec]) if sec in cfg else {}
                sep = "─" * 60
                with _debug_lock:
                    with open(DEBUG_LOG, "a", encoding="utf-8") as f:
                        f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] _on_close: 程式關閉\n"
                                f"  視窗位置  : x={x}, y={y}\n"
                                f"  from      : {ini.get('from_name','')}（{ini.get('from_code','')}）\n"
                                f"  to        : {ini.get('to_name','')}（{ini.get('to_code','')}）\n"
                                f"  remind    : {ini.get('remind_mins','0')} 分鐘\n"
                                f"  alpha     : {ini.get('alpha_pct','75')} %\n"
                                f"  train_cnt : {ini.get('train_count','3')}\n"
                                f"  show_local: {ini.get('show_local','1')}  "
                                f"show_express: {ini.get('show_express','1')}\n"
                                f"  debug_log : {ini.get('debug_log','0')}\n"
                                f"{sep}\n")
            except Exception:
                pass
        if self._clock_id:
            self.after_cancel(self._clock_id)
            self._clock_id = None
        if self._rain_refresh_id:
            self.after_cancel(self._rain_refresh_id)
        if self._refresh_id:
            self.after_cancel(self._refresh_id)
            self._refresh_id = None
        if self._sched_id:
            self.after_cancel(self._sched_id)
            self._sched_id = None
        self.destroy()


# ─── Entry point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    _ensure_station_list()
    app = SimpleApp()
    app.mainloop()
