#!/usr/bin/env python3
"""
kvhsearch_core.py  —  全文倒排索引引擎（trigram）
                      供 Build_Index_cli.py 與 Search_cli.py 共用。
                      亦可直接執行作為合併工具：
                      python kvhsearch_core.py build_main <dir>
                   增量索引自動選用最快路徑（git diff / mtime / md5）
                   多執行緒加速：IO/CPU 密集的讀檔 + 雜湊 + 萃取並行執行，
                   SQLite 寫入由單一 writer thread 序列化。

用法：
  python build_index.py build  <dir> [--workers N] [--hex]
  python build_index.py search <keyword> [kw2 ...]  [--txt|--bin] [--no-sort]
  python build_index.py prefix <prefix>             [--txt|--bin] [--no-sort]
  python build_index.py substr <fragment>           [--txt|--bin] [--no-sort]
  python build_index.py fuzzy  <keyword> [dist]     [--txt|--bin] [--no-sort]
  python build_index.py hex    <DEADBEEF> [pat2 ...]              [--no-sort]
  python build_index.py ext    <.ext|profile> [...]  [--no-sort]
  python build_index.py ext    --list          (show built-in profiles)

  --txt      只搜尋文字檔（is_text=1）
  --bin      只搜尋二進位檔（is_text=0）
  --hex      建立索引時同時索引二進位檔的 16 進位內容（預設關閉）
             注意：hex 搜尋只在以 --hex 建立的索引中有效
  --no-sort  不排序輸出（速度較快）
  --db-dir <path>  將所有 DB 檔案（main_index.db、minor_*.db）集中存放在指定目錄
                   可用於將索引存放在與 repo 不同的路徑，例如：
                   python build_index.py build_main . --db-dir D:/indexes/Cathaya
                   python build_index.py search OemDxe --db-dir D:/indexes/Cathaya

搜尋結果篩選（可自由組合）：
  --ext   .c .h          只顯示這些副檔名的結果
  --path  LenovoWsPkg    只顯示路徑中含此字串的結果（可多個，OR 邏輯）
  --exclude *.inf *.sdl  排除符合 glob 的檔案
  --grep  EFI_STATUS     只顯示同時含此關鍵字的行（可多個，AND 邏輯）

  範例：
    python build_index.py search OemDxe --ext .c .h
    python build_index.py search OemDxe --path LenovoWsPkg
    python build_index.py search OemDxe --exclude *.inf *.sdl
    python build_index.py search OemDxe --grep EFI_STATUS
    python build_index.py search OemDxe --ext .c --path LenovoWsPkg --grep TRACE
    python build_index.py substr OemDxe --path OemDxe --grep EFI_STATUS
    python build_index.py prefix Oem    --ext .h --exclude *Test*

分層索引（git 倉庫頻繁切換分支時推薦）：
  python build_index.py build_main  <dir> [--workers N] [--hex]
      在 main/master 分支上建立完整基礎索引 → main_index.db

  python build_index.py build_minor <dir> [--workers N] [--hex]
      以目前分支 vs main_index.db 的 git diff 建立差量索引
      → minor_<branch>.db  （只包含新增/修改的檔案 + tombstone 刪除清單）

  python build_index.py cleanup [<dir>]
      移除已被刪除分支的孤立 minor_*.db

  python build_index.py status  [<dir>]
      顯示所有 index 的狀態、大小、commit、新舊程度

搜尋時會自動偵測分層模式：
  minor_<branch>.db 存在 → 優先查詢 minor，再查 main（過濾 tombstone）
  只有 main_index.db  → 查詢 main
  只有 file_index.db  → 舊版單一索引（向下相容）
"""

import sys
import os
import json
import re
import fnmatch
import sqlite3
import hashlib
import time
import shutil
import subprocess
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path

# ── App metadata (used by _gen_version_info.py + PyInstaller) ────────────────
_APP_VERSION   = "1.0.0"
_APP_NAME      = "kvhsearch_core"
_APP_COPYRIGHT = "© 2026 KVH"

DB_PATH            = "file_index.db"
MAIN_DB_PATH       = "main_index.db"
MINOR_DB_PREFIX    = "minor_"
REPO_DIR: str | None = None   # override for git repo location (separate from DB dir)
DEBUG_MODE: bool   = False
AUTO_BUILD_THRESHOLD: int = 20  # auto build_minor if ≤ N commits ahead of main; 0 = disable
MIN_BINARY_STR_LEN = 4


def _minor_glob() -> list[Path]:
    """Glob for ALL minor_*.db files in the same directory as MINOR_DB_PREFIX."""
    pfx = Path(MINOR_DB_PREFIX)
    return list(pfx.parent.glob("minor_*.db"))


def _find_minor_db(sha: str) -> str | None:
    """Find any existing minor_*_<sha7>.db for *sha* regardless of label.
    Returns the path string if found, else None."""
    sha7 = sha[:7]
    pfx = Path(MINOR_DB_PREFIX)
    matches = sorted(pfx.parent.glob(f"minor_*_{sha7}.db"), reverse=True)
    return str(matches[0]) if matches else None


def list_main_indexes(db_dir: str) -> list[str]:
    """Return version labels from main_<label>.db files in db_dir, sorted newest-first.

    Examples
    --------
    ``main_v09.db`` → ``"v09"``
    ``main_index.db`` → ``"index"``  (legacy fallback)
    """
    p = Path(db_dir)
    labels: list[str] = []
    for f in sorted(p.glob("main_*.db"), reverse=True):
        label = f.stem[5:]   # strip leading "main_"
        if label:
            labels.append(label)
    return labels


def _fts5_phrase(kw: str) -> str:
    """Wrap *kw* in an FTS5 quoted phrase, escaping embedded double-quotes."""
    return '"' + kw.replace('"', '""') + '"'

# ── Resolve paths correctly both in source and PyInstaller frozen EXE ─────────
_HERE = (Path(sys._MEIPASS) if getattr(sys, "frozen", False)
         else Path(__file__).parent)

# ── Extension profiles — loaded from build_index_profiles.json at startup ─────
# When frozen: read bundled copy from _MEIPASS (read-only reference).
# At runtime the file is also looked for next to the EXE / CWD so users can
# override it without rebuilding.
def _profiles_path() -> Path:
    cwd_copy = Path.cwd() / "build_index_profiles.json"
    if cwd_copy.exists():
        return cwd_copy
    exe_copy = (Path(sys.executable).parent / "build_index_profiles.json"
                if getattr(sys, "frozen", False) else None)
    if exe_copy and exe_copy.exists():
        return exe_copy
    return _HERE / "build_index_profiles.json"

_PROFILES_PATH = _profiles_path()

_DEFAULT_PROFILES: dict[str, list[str]] = {
    "all":  [],
    "c":    [".c", ".cpp", ".h", ".hpp"],
    "sdl":  [".sdl", ".ssp"],
    "inf":  [".inf"],
    "dec":  [".dec"],
    "asm":  [".asm", ".css", ".dat", ".equ", ".inc", ".mac"],
    "cfg":  [".cfg"],
    "mak":  [".mak"],
    "asl":  [".asl", ".asi", ".oem"],
    "dxs":  [".dxs"],
    "bat":  [".bat", ".cmd"],
    "txt":  [".txt"],
    "uni":  [".uni"],
    "vfr":  [".sd", ".vfi", ".vfr", ".hfr"],
    "py":   [".py"],
    "bin":  [".bin", ".rom", ".efi", ".fd"],
}


def _load_profiles() -> dict[str, list[str]]:
    """Load profiles from JSON; write defaults if file absent."""
    if not _PROFILES_PATH.exists():
        with open(_PROFILES_PATH, "w", encoding="utf-8") as f:
            json.dump(_DEFAULT_PROFILES, f, indent=2)
        return dict(_DEFAULT_PROFILES)
    try:
        with open(_PROFILES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        # Normalise: ensure all extension values are lists of lowercase strings
        return {
            k.lower(): [
                e.lower() if e.startswith(".") else f".{e.lower()}"
                for e in v
            ]
            for k, v in data.items()
            if isinstance(k, str) and isinstance(v, list)
        }
    except Exception as exc:
        print(f"[WARN] Cannot load {_PROFILES_PATH.name}: {exc} — using defaults")
        return dict(_DEFAULT_PROFILES)


EXT_PROFILES: dict[str, list[str]] = _load_profiles()

# ── Skip rules — loaded from the same build_index_profiles.json ──────────────
# Reserved key "skip" in the JSON file (ignored as a profile).
_DEFAULT_SKIP_RULES: dict = {
    "max_file_mb":  64,
    "folders":      [".git", "build", "Flash_Image_Tool", "__pycache__", ".vs", ".vscode", "node_modules"],
    "extensions":   [
        ".chm", ".lib", ".mcb", ".exe", ".pdf", ".obj", ".rom", ".zip",
        ".bin", ".fd",  ".jar", ".pdb", ".asar",".pak", ".7z",
        ".dll", ".bmp", ".gif", ".jpg", ".efi", ".png", ".ttf", ".key",
        ".so",  ".sh",  ".ldl", ".mof", ".bsf", ".s",   ".pem", ".cbin",
        ".aid", ".db",  ".dbr", ".csm",
    ],
    "filenames":    [           # exact filename match (case-insensitive), no wildcards
        "system.sys",
    ],
    "patterns":     [            # fnmatch glob on filename — wildcards supported
        "RomAlignment.*",
        "iasl*.*",
        "BiosGuardCryptoCon*.*",
        "HPKTool_Linux*.*",
        "fit.*",
        "cryptocon.*",
        "cutrom.*",
        "BpmKmGen.*",
        "openssl.*",
    ],
}


def _load_skip_rules() -> dict:
    """Read the 'skip' key from build_index_profiles.json.
    Writes defaults if the key is absent."""
    rules = dict(_DEFAULT_SKIP_RULES)
    if not _PROFILES_PATH.exists():
        return rules
    try:
        with open(_PROFILES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if "skip" in data and isinstance(data["skip"], dict):
            s = data["skip"]
            rules["max_file_mb"] = float(s.get("max_file_mb", rules["max_file_mb"]))
            rules["folders"]     = [f.lower() for f in s.get("folders", rules["folders"])]
            rules["extensions"]  = [
                e.lower() if e.startswith(".") else f".{e.lower()}"
                for e in s.get("extensions", rules["extensions"])
            ]
            rules["filenames"] = [f.lower() for f in s.get("filenames", [])]
            rules["patterns"]  = [p.lower() for p in s.get("patterns",  [])]
        else:
            # Write defaults back so user can see and edit the key
            data["skip"] = _DEFAULT_SKIP_RULES
            with open(_PROFILES_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
    except Exception as exc:
        print(f"[WARN] Cannot read skip rules from {_PROFILES_PATH.name}: {exc}")
    return rules


_SKIP_RULES: dict = _load_skip_rules()
_MAX_FILE_SIZE: int = int(_SKIP_RULES["max_file_mb"] * 1024 * 1024)


def _should_skip(path: Path, size: int) -> str | None:
    """
    Return a human-readable reason string if *path* should be skipped,
    or None if it should be indexed.
    Checks (in order): folder, size, extension, filename, glob pattern.
    """
    # Folder check — any part of the path matches a skipped folder name
    skip_folders = _SKIP_RULES["folders"]
    for part in path.parts[:-1]:   # exclude filename itself
        if part.lower() in skip_folders:
            return f"folder:{part}"
    if size > _MAX_FILE_SIZE:
        return f">{_SKIP_RULES['max_file_mb']:.0f} MB"
    name = path.name.lower()
    ext  = path.suffix.lower()
    if ext and ext in _SKIP_RULES["extensions"]:
        return f"ext:{ext}"
    if name in _SKIP_RULES["filenames"]:
        return f"filename:{path.name}"
    for pat in _SKIP_RULES["patterns"]:
        if fnmatch.fnmatch(name, pat):
            return f"pattern:{pat}"
    return None


BATCH_SIZE         = 200
TOUCH_BATCH_SIZE   = 1000
DEFAULT_WORKERS    = min(32, (os.cpu_count() or 4) * 2)
MAX_QUEUED_FUTURES = DEFAULT_WORKERS * 4

# Hex token chunk size: index binary data as N-byte hex tokens
# 4 bytes → "DEADBEEF" tokens; balances index size vs search granularity
HEX_CHUNK_BYTES = 4

_DONE             = object()
_BINARY_STRING_RE = re.compile(rb"[ -~]{%d,}" % MIN_BINARY_STR_LEN)
_HEX_RE           = re.compile(r"^[0-9a-fA-F]{2,}$")

_read_conn: sqlite3.Connection | None = None
_read_conn_lock = threading.Lock()

# ── 工具函式 ──────────────────────────────────────────────────────────────────

def file_hash_bytes(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=20).hexdigest()

def _extract_ascii_strings(data: bytes) -> str:
    """Printable ASCII runs ≥ MIN_BINARY_STR_LEN chars (strings-style)."""
    return "\n".join(
        m.group().decode("ascii", errors="ignore")
        for m in _BINARY_STRING_RE.finditer(data)
    )

def _extract_hex_tokens(data: bytes, chunk: int = HEX_CHUNK_BYTES) -> str:
    """
    Encode binary data as space-separated uppercase hex tokens of `chunk` bytes.
    Overlapping sliding window (stride = 1 byte) ensures any byte sequence of
    length `chunk` appears as a token regardless of alignment.

    Example (chunk=4):  bytes DE AD BE EF 00 →  "DEADBEEF ADBEEF00"

    Stored alongside ASCII strings in the same FTS5 content column so a
    single MATCH query hits both. Tokens are uppercase to normalise queries.
    """
    if len(data) < chunk:
        return ""
    return " ".join(
        data[i : i + chunk].hex().upper()
        for i in range(len(data) - chunk + 1)
    )

def _process_file(
    path: Path,
    stat_result: os.stat_result,
    cached: dict,
    use_mtime_shortcut: bool,
    index_hex: bool,
    blob_sha: str | None = None,
) -> dict | None:
    """
    Worker thread: IO + CPU, never touches SQLite.
    Returns action dict or None (skip).

    If *blob_sha* is provided (file is git-tracked), it is compared against
    the stored blob_sha — if they match the file content is identical and we
    skip without reading the file at all.
    """
    spath = str(path)
    mtime = stat_result.st_mtime
    prev  = cached.get(spath)   # (mtime, hash, hex_indexed, blob_sha)

    # ── Blob SHA shortcut: zero file reads for unchanged tracked files ────────
    if blob_sha and prev:
        prev_blob = prev[3] if len(prev) > 3 else None
        if prev_blob == blob_sha:
            prev_hex_indexed = bool(prev[2]) if len(prev) > 2 else False
            if prev_hex_indexed == index_hex:
                return {"action": "touch", "spath": spath, "mtime": mtime}
            # hex mode changed — must re-index, but reuse blob_sha

    # ── mtime shortcut (non-git / untracked files) ─────────────────────────
    if use_mtime_shortcut and not blob_sha and prev and abs(prev[0] - mtime) < 0.01:
        return None  # skipped_mtime

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None

    fh = file_hash_bytes(data)

    # Content unchanged (BLAKE2b) — fallback for untracked files
    if prev and prev[1] == fh:
        prev_hex_indexed = bool(prev[2]) if len(prev) > 2 else False
        if prev_hex_indexed == index_hex:
            return {"action": "touch", "spath": spath, "mtime": mtime}
        # else: fall through to re-index with new hex setting

    is_text = b"\x00" not in data[:8192]

    if is_text:
        for enc in ("utf-8", "utf-16", "latin-1"):
            try:
                content = data.decode(enc, errors="ignore")
                break
            except Exception:
                continue
        else:
            content = ""
        hex_indexed = False
    else:
        ascii_part = _extract_ascii_strings(data)
        if index_hex:
            hex_part    = _extract_hex_tokens(data)
            content     = ascii_part + ("\n" + hex_part if hex_part else "")
            hex_indexed = True
        else:
            content     = ascii_part
            hex_indexed = False

    return {
        "action":      "update",
        "spath":       spath,
        "mtime":       mtime,
        "hash":        fh,
        "blob_sha":    blob_sha,
        "is_text":     is_text,
        "hex_indexed": hex_indexed,
        "content":     content,
    }


# ── 資料庫初始化 ──────────────────────────────────────────────────────────────

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous  = NORMAL;
        PRAGMA cache_size   = -32768;

        CREATE TABLE IF NOT EXISTS file_meta (
            id          INTEGER PRIMARY KEY,
            path        TEXT UNIQUE NOT NULL,
            mtime       REAL,
            md5         TEXT,
            is_text     INTEGER,
            ext         TEXT,
            hex_indexed INTEGER DEFAULT 0,
            blob_sha    TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_file_meta_ext     ON file_meta(ext);
        CREATE INDEX IF NOT EXISTS idx_file_meta_is_text ON file_meta(is_text);

        CREATE TABLE IF NOT EXISTS index_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS file_content
        USING fts5(
            path      UNINDEXED,
            content,
            tokenize = 'trigram'
        );
    """)
    # Migrate: fill ext for rows that predate the ext column.
    _mig = conn.execute("SELECT path FROM file_meta WHERE ext IS NULL").fetchall()
    if _mig:
        conn.executemany(
            "UPDATE file_meta SET ext=? WHERE path=?",
            [(Path(r[0]).suffix.lower() or None, r[0]) for r in _mig]
        )
    # Migrate: add blob_sha column if absent (pre-blob_sha databases).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(file_meta)")}
    if "blob_sha" not in cols:
        conn.execute("ALTER TABLE file_meta ADD COLUMN blob_sha TEXT")
    conn.commit()


def _ensure_tombstones(conn: sqlite3.Connection):
    """Add the tombstones table to a minor DB if it does not yet exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tombstones (
            path TEXT PRIMARY KEY
        )
    """)
    conn.commit()


# ── Git 整合 ──────────────────────────────────────────────────────────────────

def _git_head(repo_dir: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return None

def get_git_changed_files(repo_dir: str, conn: sqlite3.Connection) -> list[str] | None:
    head = _git_head(repo_dir)
    if head is None:
        return None
    row = conn.execute(
        "SELECT value FROM index_meta WHERE key='last_commit'"
    ).fetchone()
    last_commit = row[0] if row else None
    if last_commit is None or last_commit == head:
        return None
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", last_commit, head],
            cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
        )
        return [str(Path(repo_dir) / f.strip()) for f in out.splitlines() if f.strip()]
    except Exception:
        return None

def save_git_commit(conn: sqlite3.Connection, repo_dir: str):
    import datetime
    head = _git_head(repo_dir)
    if head:
        conn.execute("INSERT OR REPLACE INTO index_meta VALUES('last_commit',?)", (head,))
    conn.execute(
        "INSERT OR REPLACE INTO index_meta VALUES('build_time',?)",
        (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),)
    )


def _current_branch(repo_dir: str) -> str | None:
    """Return current branch name, or None if in detached HEAD state."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
        ).strip()
        return out if out != "HEAD" else None
    except Exception:
        return None


def _sanitize_branch(branch: str) -> str:
    """Convert a branch name to a filesystem-safe string (display only)."""
    return re.sub(r"[^a-zA-Z0-9_.\-]", "_", branch)


def _minor_db_for_sha(sha: str) -> str:
    """Return the minor DB filename keyed on the first 7 hex digits of *sha*."""
    return f"{MINOR_DB_PREFIX}{sha[:7]}.db"


def _get_stored_sha(db_path: str, key: str = "last_commit") -> str | None:
    """Read a SHA stored under *key* in index_meta of *db_path*."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row  = conn.execute(
            "SELECT value FROM index_meta WHERE key=?", (key,)
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _git_commits_ahead(repo_dir: str, base_sha: str, tip_sha: str) -> int | None:
    """Return the number of commits in tip_sha that are not in base_sha."""
    try:
        out = subprocess.check_output(
            ["git", "rev-list", "--count", f"{base_sha}..{tip_sha}"],
            cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
        ).strip()
        return int(out)
    except Exception:
        return None


def _git_ls_files(root: Path) -> list[Path] | None:
    """
    Return all tracked + untracked-not-ignored files via:
        git ls-files --cached --others --exclude-standard
    Automatically respects .gitignore, .git/info/exclude, and global gitignore.
    Returns None if the directory is not a git repo (caller falls back to rglob).
    """
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=str(root), text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    paths = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        p = root / line
        if p.is_file():
            paths.append(p)
    return paths


def _git_blob_shas(root: Path,
                   paths: list[Path] | None = None) -> dict[str, str]:
    """
    Return {absolute_path_str: blob_sha1} for all tracked files via:
        git ls-files -s [-- <paths>]
    Output format: mode SP sha1 SP stage TAB path
    Untracked files are absent from the result; callers fall back to BLAKE2b.
    """
    try:
        cmd = ["git", "ls-files", "-s"]
        if paths:
            # Feed paths relative to root to keep command length reasonable
            rel = [str(p.relative_to(root)) for p in paths]
            cmd += ["--"] + rel
        out = subprocess.check_output(
            cmd, cwd=str(root), text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}
    result: dict[str, str] = {}
    for line in out.splitlines():
        # "100644 <sha1>\t0\t<path>"  — stage is separated by a space before tab
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        sha1 = parts[0].split()[1]   # second token on left side
        rel  = parts[1].strip()
        result[str(root / rel)] = sha1
    return result


# ── Writer thread ─────────────────────────────────────────────────────────────

def _writer_thread(write_queue, db_path: str, root: Path, counters: dict):
    conn = sqlite3.connect(db_path)
    init_db(conn)

    meta_batch:    list[tuple] = []
    content_batch: list[tuple] = []
    delete_paths:  list[str]   = []
    touch_batch:   list[tuple] = []

    def flush(force: bool = False):
        if not (force
                or len(meta_batch)  >= BATCH_SIZE
                or len(touch_batch) >= TOUCH_BATCH_SIZE):
            return
        if delete_paths:
            conn.executemany("DELETE FROM file_content WHERE path=?",
                             [(p,) for p in delete_paths])
        if meta_batch:
            conn.executemany(
                "INSERT OR REPLACE INTO file_meta"
                "(path, mtime, md5, is_text, ext, hex_indexed, blob_sha) VALUES(?,?,?,?,?,?,?)",
                meta_batch,
            )
        if content_batch:
            conn.executemany(
                "INSERT INTO file_content(path, content) VALUES(?,?)",
                content_batch,
            )
        if touch_batch:
            conn.executemany(
                "UPDATE file_meta SET mtime=? WHERE path=?", touch_batch,
            )
        meta_batch.clear(); content_batch.clear()
        delete_paths.clear(); touch_batch.clear()
        conn.commit()

    while True:
        item = write_queue.get()
        if item is _DONE:
            break

        action = item["action"]

        if action == "skip_size":
            print(f"    [SKIP {item['reason']}] {Path(item['spath']).name}")

        elif action == "touch":
            touch_batch.append((item["mtime"], item["spath"]))
            counters["skipped_md5"] += 1
            if len(touch_batch) >= TOUCH_BATCH_SIZE:
                flush(force=True)

        elif action == "update":
            spath       = item["spath"]
            is_text     = item["is_text"]
            hex_indexed = item["hex_indexed"]
            blob_sha    = item.get("blob_sha")
            ext         = Path(spath).suffix.lower() or None
            delete_paths.append(spath)
            meta_batch.append((spath, item["mtime"], item["hash"],
                                int(is_text), ext, int(hex_indexed), blob_sha))
            content_batch.append((spath, item["content"]))
            counters["updated"] += 1
            flush()

    flush(force=True)
    save_git_commit(conn, str(root))
    conn.commit()
    conn.close()


# ── Build entry point ─────────────────────────────────────────────────────────

def build_index(directory: str, num_workers: int = DEFAULT_WORKERS,
                index_hex: bool = False, _db_path: str = None):
    db_path = _db_path or DB_PATH
    root = Path(directory).resolve()

    setup_conn = sqlite3.connect(db_path)
    init_db(setup_conn)
    git_changed = get_git_changed_files(str(root), setup_conn)
    cached: dict[str, tuple] = {
        row[0]: (row[1], row[2], row[3], row[4])
        for row in setup_conn.execute(
            "SELECT path, mtime, md5, hex_indexed, blob_sha FROM file_meta"
        )
    }
    setup_conn.close()

    # ── Fetch git blob SHAs for all tracked files (zero file reads) ────────────
    blob_shas: dict[str, str] = _git_blob_shas(root)

    if git_changed is not None:
        process_paths = [Path(p) for p in git_changed if Path(p).is_file()]
        mode_label = (
            f"git diff 模式（{len(process_paths)}/{len(git_changed)} 個檔案，"
            f"{num_workers} workers{'，HEX' if index_hex else ''}）"
        )
        use_mtime = False
    else:
        process_paths = _git_ls_files(root)
        if process_paths is not None:
            mode_label = (
                f"git ls-files 模式（{len(process_paths)} 個檔案，"
                f"{num_workers} workers{'，HEX' if index_hex else ''}）"
            )
        else:
            process_paths = [
                p for p in root.rglob("*")
                if p.is_file()
            ]
            mode_label = (
                f"mtime 模式（{len(process_paths)} 個檔案，"
                f"{num_workers} workers{'，HEX' if index_hex else ''}）"
            )
        use_mtime = True

    if index_hex:
        print("  [INFO] HEX 索引已啟用 — 二進位檔將額外索引 16 進位內容（索引較大）")
    print(f"  [{mode_label}]")

    counters = {"updated": 0, "skipped_md5": 0, "skipped_mtime": 0}

    wq = queue.Queue(maxsize=BATCH_SIZE * 4)
    writer = threading.Thread(
        target=_writer_thread, args=(wq, db_path, root, counters), daemon=False
    )
    writer.start()

    t0      = time.perf_counter()
    pending: list[Future] = []
    total   = len(process_paths)
    done    = 0

    def _show_progress():
        pct = done * 100 / total if total else 100
        bar_w  = 30
        filled = int(bar_w * pct / 100)
        bar    = "█" * filled + "░" * (bar_w - filled)
        print(f"\r  [{bar}] {pct:5.1f}%  {done}/{total}", end="", flush=True)

    def drain_one():
        result = pending.pop(0).result()
        if result is not None:
            wq.put(result)

    with ThreadPoolExecutor(max_workers=num_workers) as pool:
        for path in process_paths:
            spath = str(path)
            done += 1
            _show_progress()
            try:
                stat_result = path.stat()
            except OSError:
                continue

            skip_reason = _should_skip(path, stat_result.st_size)
            if skip_reason:
                wq.put({"action": "skip_size", "spath": spath, "reason": skip_reason})
                continue

            bsha = blob_shas.get(spath)   # None for untracked files

            if use_mtime and not bsha:
                prev = cached.get(spath)
                if prev and abs(prev[0] - stat_result.st_mtime) < 0.01:
                    counters["skipped_mtime"] += 1
                    continue

            if len(pending) >= MAX_QUEUED_FUTURES:
                drain_one()

            pending.append(pool.submit(
                _process_file, path, stat_result, cached, use_mtime, index_hex, bsha
            ))

        while pending:
            drain_one()

    wq.put(_DONE)
    writer.join()

    print()   # newline after progress bar
    elapsed = time.perf_counter() - t0
    skipped = counters["skipped_mtime"] + counters["skipped_md5"]
    return counters, elapsed, total


def _print_build_summary(label: str, db_path: str, counters: dict,
                         elapsed: float, total: int,
                         extra_lines: list[str] | None = None):
    """Print a formatted summary box after a build command."""
    updated  = counters["updated"]
    skipped  = counters["skipped_mtime"] + counters["skipped_md5"]
    skipped_sz = total - updated - skipped
    rate     = updated / elapsed if elapsed > 0 else 0
    mins, secs = divmod(elapsed, 60)
    time_str = f"{int(mins)}m {secs:.1f}s" if mins else f"{secs:.1f}s"
    db_size  = Path(db_path).stat().st_size / (1024 * 1024) if Path(db_path).exists() else 0

    W = 52
    sep = "─" * W
    print(f"  ┌{sep}┐")
    print(f"  │  {label:<{W-2}}│")
    print(f"  ├{sep}┤")
    print(f"  │  {'Total files scanned':<28}  {total:>10,}    │")
    print(f"  │  {'Indexed / updated':<28}  {updated:>10,}    │")
    print(f"  │  {'Skipped (unchanged)':<28}  {skipped:>10,}    │")
    if skipped_sz > 0:
        print(f"  │  {'Skipped (>64 MB)':<28}  {skipped_sz:>10,}    │")
    print(f"  │  {'Index size':<28}  {db_size:>9.1f} MB  │")
    print(f"  │  {'Throughput':<28}  {rate:>7.0f} files/s  │")
    print(f"  │  {'Elapsed':<28}  {time_str:>10}    │")
    if extra_lines:
        print(f"  ├{sep}┤")
        for line in extra_lines:
            print(f"  │  {line:<{W-2}}│")
    print(f"  └{sep}┘")


# ── 分層索引：build_main / build_minor / cleanup ──────────────────────────────

def build_main(directory: str, num_workers: int = DEFAULT_WORKERS,
               index_hex: bool = False):
    """Full index of the current branch → main_index.db.
    Run this on main/master to establish the base index."""
    t0 = time.perf_counter()
    repo_dir = str(Path(directory).resolve())
    head     = _git_head(repo_dir)
    branch   = _current_branch(repo_dir)
    print(f"  [build_main] → {MAIN_DB_PATH}")
    if branch:
        print(f"  branch: {branch}  commit: {head[:8] if head else 'n/a'}")
    counters, elapsed, total = build_index(
        directory, num_workers=num_workers,
        index_hex=index_hex, _db_path=MAIN_DB_PATH
    )
    wall = time.perf_counter() - t0
    extras = []
    if branch:
        extras.append(f"Branch   {branch}")
    if head:
        extras.append(f"Commit   {head[:12]}")
    extras.append(f"Output   {MAIN_DB_PATH}")
    _print_build_summary("build_main — complete",
                         MAIN_DB_PATH, counters, wall, total, extras)


def build_minor(directory: str, num_workers: int = DEFAULT_WORKERS,
                index_hex: bool = False, _branch_override: str | None = None):
    """
    Index only files changed between main_index.db's base commit and the
    current branch HEAD → minor_<branch>.db.

    Deleted / renamed files are written to the tombstones table so that
    tiered search can suppress them from main_index results.
    """
    root     = Path(directory).resolve()
    repo_dir = str(root)

    branch = _branch_override or _current_branch(repo_dir)
    if not branch:
        print("[ERROR] Detached HEAD — checkout a named branch first.")
        return

    if not Path(MAIN_DB_PATH).exists():
        print(f"[ERROR] {MAIN_DB_PATH} not found. Run 'build_main <dir>' first.")
        return

    base_sha = _get_stored_sha(MAIN_DB_PATH, "last_commit")
    if not base_sha:
        print("[ERROR] Main index has no recorded commit. Run 'build_main' again.")
        return

    branch_head = _git_head(repo_dir)
    if not branch_head:
        print("[ERROR] Cannot read HEAD SHA. Is this a git repo?")
        return

    minor_db = _minor_db_for_sha(branch_head)
    t0_wall  = time.perf_counter()
    print(f"  [build_minor] branch={branch}  base={base_sha[:8]}  head={branch_head[:8]}")
    print(f"  output → {minor_db}")

    # ── Diff against main to find what changed ────────────────────────────────
    try:
        diff_out = subprocess.check_output(
            ["git", "diff", "--name-status", base_sha, branch_head],
            cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] git diff failed: {e}")
        return

    modified_added: list[Path] = []
    tombstone_paths: list[str] = []

    for line in diff_out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts  = line.split("\t")
        status = parts[0][0]   # M / A / D / R / C / T …
        if status in ("M", "A"):
            p = root / parts[1]
            if p.is_file():
                modified_added.append(p)
        elif status == "D":
            tombstone_paths.append(str(root / parts[1]))
        elif status == "R":                    # R100\told\tnew
            if len(parts) >= 3:
                tombstone_paths.append(str(root / parts[1]))
                p = root / parts[2]
                if p.is_file():
                    modified_added.append(p)
        elif status == "C":                    # copy — treat new path as added
            if len(parts) >= 3:
                p = root / parts[2]
                if p.is_file():
                    modified_added.append(p)

    print(f"  Changes vs main: {len(modified_added)} to index, "
          f"{len(tombstone_paths)} tombstone(s)")

    # ── Load existing minor cache (skip re-hashing unchanged files) ───────────
    cached: dict[str, tuple] = {}
    if Path(minor_db).exists():
        try:
            tmp = sqlite3.connect(f"file:{minor_db}?mode=ro", uri=True)
            cached = {
                row[0]: (row[1], row[2], row[3], row[4])
                for row in tmp.execute(
                    "SELECT path, mtime, md5, hex_indexed, blob_sha FROM file_meta"
                )
            }
            tmp.close()
        except Exception:
            pass

    # ── Fetch git blob SHAs for modified/added tracked files ──────────────────
    blob_shas: dict[str, str] = _git_blob_shas(root, paths=modified_added)

    # ── Writer thread (reuses existing _writer_thread infrastructure) ─────────
    counters = {"updated": 0, "skipped_md5": 0, "skipped_mtime": 0}
    wq       = queue.Queue(maxsize=BATCH_SIZE * 4)
    writer   = threading.Thread(
        target=_writer_thread,
        args=(wq, minor_db, root, counters),
        daemon=False,
    )
    writer.start()

    n_files = len(modified_added)
    done_m  = 0
    pending: list[Future] = []

    def _show_minor_progress():
        pct    = done_m * 100 / n_files if n_files else 100
        bar_w  = 30
        filled = int(bar_w * pct / 100)
        bar    = "█" * filled + "░" * (bar_w - filled)
        print(f"\r  [{bar}] {pct:5.1f}%  {done_m}/{n_files}", end="", flush=True)

    def drain_one():
        result = pending.pop(0).result()
        if result is not None:
            wq.put(result)

    with ThreadPoolExecutor(max_workers=num_workers) as pool:
        for path in modified_added:
            done_m += 1
            _show_minor_progress()
            try:
                stat = path.stat()
            except OSError:
                continue
            skip_reason = _should_skip(path, stat.st_size)
            if skip_reason:
                wq.put({"action": "skip_size", "spath": str(path), "reason": skip_reason})
                continue
            bsha = blob_shas.get(str(path))
            if len(pending) >= MAX_QUEUED_FUTURES:
                drain_one()
            pending.append(pool.submit(
                _process_file, path, stat, cached, False, index_hex, bsha
            ))
        while pending:
            drain_one()

    if n_files:
        print()   # newline after progress bar

    wq.put(_DONE)
    writer.join()

    # ── Write tombstones + branch metadata ────────────────────────────────────
    conn = sqlite3.connect(minor_db)
    _ensure_tombstones(conn)
    conn.execute("DELETE FROM tombstones")          # full rebuild each run
    if tombstone_paths:
        conn.executemany(
            "INSERT OR REPLACE INTO tombstones(path) VALUES(?)",
            [(p,) for p in tombstone_paths],
        )
    conn.execute("INSERT OR REPLACE INTO index_meta VALUES('base_commit', ?)", (base_sha,))
    conn.execute("INSERT OR REPLACE INTO index_meta VALUES('branch_head', ?)", (branch_head,))
    conn.execute("INSERT OR REPLACE INTO index_meta VALUES('branch_name',  ?)", (branch,))
    conn.commit()
    conn.close()

    wall = time.perf_counter() - t0_wall
    _print_build_summary(
        f"build_minor — {branch}",
        minor_db, counters, wall, len(modified_added),
        [
            f"Base     {base_sha[:12]}",
            f"Head     {branch_head[:12]}",
            f"Output   {minor_db}",
            f"Tombstones  {len(tombstone_paths)}",
        ],
    )


# ── build_chain ───────────────────────────────────────────────────────────────

def _git_list_branches(repo_dir: str) -> list[tuple[str, str, str]]:
    """Return [(branch_name, sha, iso_date), ...] sorted newest → oldest."""
    try:
        out = subprocess.check_output(
            ["git", "for-each-ref", "--sort=-committerdate",
             "--format=%(refname:short) %(objectname) %(committerdate:iso-strict)",
             "refs/heads/"],
            cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
        )
        result = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                result.append((parts[0], parts[1], parts[2]))
        return result
    except Exception:
        return []


def _git_stash_push(repo_dir: str) -> bool:
    """Stash all local changes. Returns True if anything was stashed."""
    try:
        out = subprocess.check_output(
            ["git", "stash", "push", "--include-untracked", "-m", "kvhsearch_build_chain"],
            cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
        )
        return "No local changes" not in out
    except Exception:
        return False


def _git_stash_pop(repo_dir: str) -> None:
    try:
        subprocess.run(["git", "stash", "pop"], cwd=repo_dir,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _git_checkout(repo_dir: str, branch: str) -> bool:
    try:
        subprocess.check_call(
            ["git", "checkout", branch],
            cwd=repo_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return True
    except Exception:
        return False


def build_chain(directory: str, num_workers: int = DEFAULT_WORKERS,
                index_hex: bool = False):
    """
    Auto-build minor indexes for every branch that is newer than the
    main_index.db baseline, then restore the original branch.

    Algorithm:
      1. List all local branches sorted newest → oldest.
      2. Find the branch whose HEAD == main_index's last_commit  (the
         "main base").
      3. For every branch that is newer (appears before the base in the
         sorted list) and does not yet have a minor DB, checkout that
         branch and run build_minor.
      4. Restore the original branch.

    git stash is used automatically if the working tree is dirty.
    """
    root      = Path(directory).resolve()
    repo_dir  = str(root)

    # ── Pre-flight ────────────────────────────────────────────────────────────
    if not Path(MAIN_DB_PATH).exists():
        print(f"[ERROR] {MAIN_DB_PATH} not found — run 'build_main <dir>' first.")
        return

    base_sha = _get_stored_sha(MAIN_DB_PATH, "last_commit")
    if not base_sha:
        print("[ERROR] Main index has no recorded commit — run 'build_main' again.")
        return

    branches = _git_list_branches(repo_dir)
    if not branches:
        print("[ERROR] No local branches found.")
        return

    # ── Find the base branch (the one whose HEAD == base_sha) ─────────────────
    base_idx = None
    for i, (bname, sha, _) in enumerate(branches):
        if sha.startswith(base_sha[:7]) or base_sha.startswith(sha[:7]):
            base_idx = i
            break

    if base_idx is None:
        print(f"[WARN] Cannot find branch matching main_index commit {base_sha[:8]}.")
        print("       Will build minor for ALL branches except current one.")
        base_idx = len(branches)   # treat all as newer

    orig_branch = _current_branch(repo_dir)

    # ── Preview and confirm ───────────────────────────────────────────────────
    to_build   = []
    to_skip    = []
    for bname, sha, _date in branches[:base_idx]:
        if Path(_minor_db_for_sha(sha)).exists():
            to_skip.append((bname, sha))
        else:
            to_build.append((bname, sha))

    print(f"\n{'='*60}")
    print(f"  build_chain — plan")
    print(f"  main base commit : {base_sha[:12]}")
    print(f"  current branch   : {orig_branch or '(detached)'}")
    print(f"{'='*60}")
    if to_build:
        print(f"  Will build ({len(to_build)}):")
        for bname, sha in to_build:
            print(f"    + {bname:<30} {sha[:12]}")
    if to_skip:
        print(f"  Already exist ({len(to_skip)}):")
        for bname, sha in to_skip:
            print(f"    - {bname:<30} {sha[:12]}  (skip)")
    if not to_build:
        print("  Nothing to build — all minor DBs already exist.")
        return
    print()
    try:
        ans = input("  Proceed? [y/N] ").strip().lower()
    except EOFError:
        ans = "y"   # non-interactive (pipe / frozen EXE)
    if ans not in ("y", "yes"):
        print("  Cancelled.")
        return
    print()

    # ── Stash dirty working tree ───────────────────────────────────────────────
    stashed = _git_stash_push(repo_dir)
    if stashed:
        print("[info] Working tree changes stashed.\n")

    built = []
    skipped_existing = []
    failed = []

    try:
        for bname, sha, _date in branches[:base_idx]:
            # Check if minor DB already exists for this branch HEAD
            existing_minor = _minor_db_for_sha(sha)
            if Path(existing_minor).exists():
                skipped_existing.append(bname)
                continue

            print(f"\n  >>> Checking out '{bname}' ...")
            if not _git_checkout(repo_dir, bname):
                print(f"  [FAIL] Cannot checkout '{bname}' — skipping.")
                failed.append(bname)
                continue

            build_minor(repo_dir, num_workers=num_workers, index_hex=index_hex)
            built.append(bname)

    finally:
        # ── Always restore original branch ────────────────────────────────────
        if orig_branch:
            print(f"\n  >>> Restoring branch '{orig_branch}' ...")
            _git_checkout(repo_dir, orig_branch)
        if stashed:
            _git_stash_pop(repo_dir)
            print("[info] Stashed changes restored.")

    # ── Summary ───────────────────────────────────────────────────────────────
    W = 52
    sep = "─" * W
    print(f"\n  ┌{sep}┐")
    print(f"  │  build_chain summary{'':<{W-21}}│")
    print(f"  ├{sep}┤")
    print(f"  │  Built      : {len(built):<{W-16}}│")
    for b in built:
        print(f"  │    ✓ {b:<{W-6}}│")
    print(f"  │  Skipped    : {len(skipped_existing):<{W-16}}│")
    for b in skipped_existing:
        print(f"  │    - {b:<{W-6}}│")
    if failed:
        print(f"  │  Failed     : {len(failed):<{W-16}}│")
        for b in failed:
            print(f"  │    ✗ {b:<{W-6}}│")
    print(f"  └{sep}┘\n")


def build_history(directory: str, num_workers: int = DEFAULT_WORKERS,
                  index_hex: bool = False):
    """
    Build a minor index for EVERY commit on the first-parent history between
    the main_<label>.db base commit and the current branch HEAD.

    This lets you search the codebase at any historical commit snapshot.

    Algorithm:
      1. Read base_sha from MAIN_DB_PATH's index_meta.last_commit.
      2. git log --first-parent --format=%H <base_sha>..HEAD  → list of SHAs,
         newest-first.
      3. For each SHA (oldest→newest):
         - Skip if minor DB already exists.
         - git checkout <sha>  (detached HEAD)
         - build_minor()
      4. Restore original branch.
    """
    root     = Path(directory).resolve()
    repo_dir = str(root)

    if not Path(MAIN_DB_PATH).exists():
        print(f"[ERROR] {MAIN_DB_PATH} not found — run 'build_main <dir>' first.")
        return

    base_sha = _get_stored_sha(MAIN_DB_PATH, "last_commit")
    if not base_sha:
        print("[ERROR] Main index has no recorded commit — run 'build_main' again.")
        return

    tip_sha = _git_head(repo_dir)
    if not tip_sha:
        print("[ERROR] Cannot read HEAD SHA. Is this a git repo?")
        return

    orig_branch = _current_branch(repo_dir)

    # Get linear (first-parent) commit list between base and tip, newest-first
    try:
        out = subprocess.check_output(
            ["git", "log", "--first-parent", "--format=%H", f"{base_sha}..{tip_sha}"],
            cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] git log failed: {e}")
        return

    shas = [s.strip() for s in out.splitlines() if s.strip()]
    if not shas:
        print("[INFO] No commits between main base and HEAD — nothing to build.")
        return

    # Fetch commit subjects for preview
    try:
        log_out = subprocess.check_output(
            ["git", "log", "--first-parent", "--format=%H %s", f"{base_sha}..{tip_sha}"],
            cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
        )
        _subj = {}
        for line in log_out.splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2:
                _subj[parts[0][:12]] = parts[1][:55]
    except Exception:
        _subj = {}

    total = len(shas)

    # ── Preview and confirm ───────────────────────────────────────────────────
    to_build = [(sha, _subj.get(sha[:12], "")) for sha in reversed(shas)
                if not Path(_minor_db_for_sha(sha)).exists()]
    to_skip  = [sha for sha in shas if Path(_minor_db_for_sha(sha)).exists()]

    print(f"\n{'='*64}")
    print(f"  build_history — plan")
    print(f"  main base  : {base_sha[:12]}")
    print(f"  HEAD       : {tip_sha[:12]}")
    print(f"  MAIN_DB    : {MAIN_DB_PATH}")
    print(f"{'='*64}")
    if to_build:
        print(f"  Will build ({len(to_build)}):")
        for sha, subj in to_build:
            print(f"    + {sha[:12]}  {subj}")
    if to_skip:
        print(f"  Already exist ({len(to_skip)}): (skip)")
    if not to_build:
        print("  Nothing to build — all minor DBs already exist.")
        return
    print()
    try:
        ans = input("  Proceed? [y/N] ").strip().lower()
    except EOFError:
        ans = "y"
    if ans not in ("y", "yes"):
        print("  Cancelled.")
        return
    print()

    stashed = _git_stash_push(repo_dir)
    if stashed:
        print("[info] Working tree changes stashed.\n")

    built            : list[str] = []
    skipped_existing : list[str] = []
    failed           : list[str] = []

    try:
        # Process oldest→newest so minor DBs accumulate in logical order
        for idx, sha in enumerate(reversed(shas), 1):
            minor_db = _minor_db_for_sha(sha)
            short    = sha[:12]
            if Path(minor_db).exists():
                skipped_existing.append(short)
                print(f"  [{idx:>4}/{total}] skip {short} — already exists ({Path(minor_db).name})")
                continue

            print(f"  [{idx:>4}/{total}] checkout {short} ...")
            if not _git_checkout(repo_dir, sha):
                print(f"  [FAIL] Cannot checkout {short} — skipping.")
                failed.append(short)
                continue

            build_minor(repo_dir, num_workers=num_workers, index_hex=index_hex,
                        _branch_override=short)
            built.append(short)

    finally:
        if orig_branch:
            print(f"\n  >>> Restoring branch '{orig_branch}' ...")
            _git_checkout(repo_dir, orig_branch)
        elif tip_sha:
            _git_checkout(repo_dir, tip_sha)
        if stashed:
            _git_stash_pop(repo_dir)
            print("[info] Stashed changes restored.")

    # Summary
    W   = 52
    sep = "─" * W
    print(f"\n  ┌{sep}┐")
    print(f"  │{'build_history summary':^{W}}│")
    print(f"  ├{sep}┤")
    print(f"  │  Built      : {len(built):<{W-14}}│")
    for s in built:
        print(f"  │    ✓ {s:<{W-6}}│")
    if skipped_existing:
        print(f"  │  Skipped    : {len(skipped_existing):<{W-14}}│")
        for s in skipped_existing:
            print(f"  │    - {s:<{W-6}}│")
    if failed:
        print(f"  │  Failed     : {len(failed):<{W-14}}│")
        for s in failed:
            print(f"  │    ✗ {s:<{W-6}}│")
    print(f"  └{sep}┘\n")


def build_promote(from_label: str, minor_sha: str, to_label: str):
    """
    Create a new main_<to_label>.db by merging main_<from_label>.db with
    minor_<from_label>_<minor_sha>.db.

    Algorithm:
      1. Open source main DB (read-only).
      2. Find the minor DB whose filename starts with MINOR_DB_PREFIX and
         contains minor_sha (prefix match on 7+ chars).
      3. Create new main_<to_label>.db, copy all file_meta + file_content
         from source main.
      4. Apply minor: upsert changed rows, delete tombstoned rows.
      5. Update index_meta: last_commit = minor's branch_head,
         build_time = now, label = to_label.
    """
    db_dir   = Path(MAIN_DB_PATH).parent
    src_path = db_dir / f"main_{from_label}.db"
    dst_path = db_dir / f"main_{to_label}.db"

    # Locate the minor DB by SHA prefix
    minor_prefix = db_dir / f"minor_{from_label}_"
    candidates   = list(db_dir.glob(f"minor_{from_label}_*.db"))
    minor_path   = None
    for c in candidates:
        # stem = minor_v07_ec67f5c  → sha part after last underscore
        sha_part = c.stem.split("_")[-1]
        if sha_part.startswith(minor_sha[:7]):
            minor_path = c
            break

    if not src_path.exists():
        print(f"[ERROR] Source main DB not found: {src_path}")
        return
    if minor_path is None:
        print(f"[ERROR] No minor DB found matching sha '{minor_sha[:7]}' in {db_dir}")
        print(f"        Available: {[c.name for c in candidates]}")
        return
    if dst_path.exists():
        print(f"[ERROR] Target DB already exists: {dst_path}")
        print(f"        Delete it first if you want to overwrite.")
        return

    print(f"\n  build_promote")
    print(f"  from : {src_path.name}")
    print(f"  minor: {minor_path.name}")
    print(f"  to   : {dst_path.name}\n")

    t0 = time.perf_counter()

    # Step 1 — copy main DB as starting point
    shutil.copy2(src_path, dst_path)

    dst_conn   = sqlite3.connect(str(dst_path))
    minor_conn = sqlite3.connect(f"file:{minor_path}?mode=ro", uri=True)

    try:
        dst_conn.execute("PRAGMA journal_mode = WAL")
        dst_conn.execute("PRAGMA synchronous  = NORMAL")

        # Step 2 — read tombstones (deleted/renamed files)
        try:
            tombstones = {r[0] for r in minor_conn.execute("SELECT path FROM tombstones")}
        except Exception:
            tombstones = set()

        # Step 3 — delete tombstoned rows from dst
        if tombstones:
            for path in tombstones:
                dst_conn.execute("DELETE FROM file_meta WHERE path=?", (path,))
                dst_conn.execute("DELETE FROM file_content WHERE path=?", (path,))
            print(f"  Removed {len(tombstones)} tombstoned files.")

        # Step 4 — upsert changed files from minor into dst
        minor_rows = minor_conn.execute(
            "SELECT path, mtime, md5, is_text, ext, hex_indexed, blob_sha FROM file_meta"
        ).fetchall()

        upserted = 0
        for row in minor_rows:
            path = row[0]
            # Remove old entries first (FTS5 can't do upsert)
            dst_conn.execute("DELETE FROM file_meta WHERE path=?", (path,))
            dst_conn.execute("DELETE FROM file_content WHERE path=?", (path,))
            # Insert new meta
            dst_conn.execute(
                "INSERT INTO file_meta(path,mtime,md5,is_text,ext,hex_indexed,blob_sha) "
                "VALUES(?,?,?,?,?,?,?)",
                row
            )
            # Insert new content
            content_row = minor_conn.execute(
                "SELECT content FROM file_content WHERE path=?", (path,)
            ).fetchone()
            if content_row:
                dst_conn.execute(
                    "INSERT INTO file_content(path, content) VALUES(?,?)",
                    (path, content_row[0])
                )
            upserted += 1

        print(f"  Upserted {upserted} changed files.")

        # Step 5 — update index_meta
        branch_head = minor_conn.execute(
            "SELECT value FROM index_meta WHERE key='branch_head'"
        ).fetchone()
        new_commit = branch_head[0] if branch_head else None

        dst_conn.execute("INSERT OR REPLACE INTO index_meta VALUES('label',?)",    (to_label,))
        dst_conn.execute("INSERT OR REPLACE INTO index_meta VALUES('build_time',?)",
                         (time.strftime("%Y-%m-%d %H:%M:%S"),))
        if new_commit:
            dst_conn.execute("INSERT OR REPLACE INTO index_meta VALUES('last_commit',?)", (new_commit,))

        dst_conn.commit()

        print(f"  Running VACUUM to reclaim freed space…")
        dst_conn.execute("VACUUM")

        elapsed = time.perf_counter() - t0
        size_mb = dst_path.stat().st_size / 1024 / 1024
        total   = dst_conn.execute("SELECT COUNT(*) FROM file_meta").fetchone()[0]
        print(f"  Total files in {dst_path.name}: {total}")
        print(f"  Final size: {size_mb:.1f} MB")
        print(f"  Done in {elapsed:.1f}s\n")

    finally:
        minor_conn.close()
        dst_conn.close()


def cleanup_indexes(repo_dir: str = "."):
    """Remove minor_*.db files whose branch commit no longer exists in git."""
    repo_dir   = str(Path(repo_dir).resolve())
    minor_files = _minor_glob()
    if not minor_files:
        print("[cleanup] No minor index files found.")
        return

    removed = 0
    for db_file in minor_files:
        branch_head = _get_stored_sha(str(db_file), "branch_head")
        branch_name = _get_stored_sha(str(db_file), "branch_name")
        keep = False
        if branch_head:
            try:
                subprocess.check_output(
                    ["git", "cat-file", "-t", branch_head],
                    cwd=repo_dir, stderr=subprocess.DEVNULL
                )
                keep = True
            except subprocess.CalledProcessError:
                keep = False
        if keep:
            print(f"  [keep]   {db_file.name}  (branch={branch_name})")
        else:
            db_file.unlink()
            removed += 1
            print(f"  [remove] {db_file.name}  (branch={branch_name}, orphaned)")

    print(f"\n  完成：移除 {removed}/{len(minor_files)} 個孤立 minor index")


def status_indexes(repo_dir: str = "."):
    """
    Print a dashboard showing the state of all index files in the current
    directory: main_index.db, minor_*.db, and legacy file_index.db.
    """
    repo_dir  = str(Path(repo_dir).resolve())
    cur_branch = _current_branch(repo_dir)
    cur_head   = _git_head(repo_dir)
    W = 60

    def _db_info(db_path: str) -> dict:
        info = {"size_mb": 0.0, "files": 0, "commit": None,
                "branch": None, "base": None, "head": None, "tombstones": 0}
        p = Path(db_path)
        if not p.exists():
            return info
        info["size_mb"] = p.stat().st_size / (1024 * 1024)
        try:
            c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = c.execute("SELECT COUNT(*) FROM file_meta").fetchone()
            info["files"] = row[0] if row else 0
            for key in ("last_commit", "branch_name", "base_commit",
                        "branch_head"):
                r = c.execute(
                    "SELECT value FROM index_meta WHERE key=?", (key,)
                ).fetchone()
                if r:
                    info[{"last_commit": "commit", "branch_name": "branch",
                           "base_commit": "base", "branch_head": "head"}[key]] = r[0]
            try:
                r = c.execute("SELECT COUNT(*) FROM tombstones").fetchone()
                info["tombstones"] = r[0] if r else 0
            except Exception:
                pass
            c.close()
        except Exception:
            pass
        return info

    def _age(sha: str | None) -> str:
        if not sha:
            return "n/a"
        try:
            ts = subprocess.check_output(
                ["git", "log", "-1", "--format=%cr", sha],
                cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
            ).strip()
            if ts:
                return ts
        except Exception:
            pass
        return "?"

    def _stale(stored_sha: str | None) -> str:
        """Return a staleness hint vs current HEAD."""
        if not stored_sha or not cur_head:
            return ""
        if stored_sha == cur_head:
            return "  ✓ up-to-date"
        try:
            out = subprocess.check_output(
                ["git", "rev-list", "--count", f"{stored_sha}..{cur_head}"],
                cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
            ).strip()
            n = int(out)
            return f"  ⚠ {n} commit{'s' if n != 1 else ''} behind HEAD"
        except Exception:
            return "  ? unknown"

    sep  = "─" * W
    sep2 = "═" * W

    def _p(content: str = "") -> None:
        """Print one box content row with exactly W chars between the borders."""
        print(f"  │{content:<{W}}│")

    print(f"  ╔{sep2}╗")
    print(f"  ║  {'Index Status':^{W-2}}║")
    print(f"  ╚{sep2}╝")
    if cur_branch:
        print(f"  Current branch : {cur_branch}")
    if cur_head:
        print(f"  Current HEAD   : {cur_head[:12]}  ({_age(cur_head)})")
    print()

    # ── all main_*.db ─────────────────────────────────────────────────────────
    db_dir      = Path(MAIN_DB_PATH).parent
    all_mains   = sorted(db_dir.glob("main_*.db"), reverse=True)
    # Always include the currently configured MAIN_DB_PATH even if it doesn't
    # match the glob pattern (e.g. legacy main_index.db)
    main_p = Path(MAIN_DB_PATH)
    if main_p not in all_mains and main_p.exists():
        all_mains.insert(0, main_p)

    active_label = main_p.stem[5:] if main_p.stem.startswith("main_") else ""

    for mpath in all_mains:
        is_active = (mpath == main_p)
        active_tag = "  [*]" if is_active else ""
        label = mpath.stem[5:] if mpath.stem.startswith("main_") else mpath.stem
        print(f"  ┌{sep}┐")
        if mpath.exists():
            mi    = _db_info(str(mpath))
            stale = _stale(mi["commit"])
            hdr   = f"main_{label}.db{active_tag}"
            _p(f"  {hdr}")
            print(f"  ├{sep}┤")
            _p(f"  {'Files indexed':<22}  {mi['files']:>10,}")
            _p(f"  {'Size':<22}  {mi['size_mb']:>9.1f} MB")
            c_str = mi['commit'][:12] if mi['commit'] else 'n/a'
            _p(f"  {'Indexed at commit':<22}  {c_str:<12}{stale}")
            build_time = _get_stored_sha(str(mpath), "build_time") or _age(mi['commit'])
            _p(f"  {'Age':<22}  {build_time}")
        else:
            hdr = f"main_{label}.db  — NOT FOUND"
            _p(f"  {hdr}")
            _p(f"  Run: build_main <dir>")
        print(f"  └{sep}┘")
        print()

    # ── minor_*.db ────────────────────────────────────────────────────────────
    minor_files = sorted(_minor_glob())
    print(f"  ┌{sep}┐")
    _p(f"  Minor indexes ({len(minor_files)} found)")
    print(f"  ├{sep}┤")
    if not minor_files:
        _p(f"  (none)  — run: build_minor <dir>")
    for idx, db_file in enumerate(minor_files):
        mi   = _db_info(str(db_file))
        name = db_file.name
        br   = mi["branch"] or "?"
        is_active = (br == cur_branch)
        active_tag = " [active]" if is_active else ""
        stale = _stale(mi["head"])
        _p(f"  {name}{active_tag}")
        _p(f"    {'Branch':<20}  {br}")
        h_str = mi["head"][:12] if mi["head"] else "n/a"
        _p(f"    {'Head commit':<20}  {h_str:<12}{stale}")
        b_str = mi["base"][:12] if mi["base"] else "n/a"
        _p(f"    {'Based on':<20}  {b_str}")
        ft = f"{mi['files']:,} / {mi['tombstones']}"
        _p(f"    {'Files / tombstones':<20}  {ft}")
        _p(f"    {'Size':<20}  {mi['size_mb']:.1f} MB")
        # Orphan check
        if mi["head"]:
            try:
                subprocess.check_output(
                    ["git", "cat-file", "-t", mi["head"]],
                    cwd=repo_dir, stderr=subprocess.DEVNULL
                )
            except subprocess.CalledProcessError:
                _p(f"    ⚠ ORPHANED — branch SHA not in repo. Run: cleanup")
        if idx < len(minor_files) - 1:
            print(f"  ├{sep}┤")
    print(f"  └{sep}┘")
    print()

    # ── legacy file_index.db ──────────────────────────────────────────────────
    legacy_p = Path(DB_PATH)
    if legacy_p.exists():
        li = _db_info(DB_PATH)
        stale = _stale(li["commit"])
        print(f"  ┌{sep}┐")
        _p(f"  file_index.db  (legacy single-index)")
        print(f"  ├{sep}┤")
        _p(f"  {'Files indexed':<22}  {li['files']:>10,}")
        _p(f"  {'Size':<22}  {li['size_mb']:>9.1f} MB")
        c_str = li['commit'][:12] if li['commit'] else 'n/a'
        _p(f"  {'Indexed at commit':<22}  {c_str:<12}{stale}")
        print(f"  └{sep}┘")
        print()

    # ── Active search mode hint ────────────────────────────────────────────────
    print(f"  Active search mode:", end="  ")
    if main_p.exists():
        if cur_branch and cur_head:
            minor_active = _find_minor_db(cur_head)
            if minor_active:
                print(f"TIERED  ({minor_active} → {MAIN_DB_PATH})")
            else:
                stale = [
                    f for f in _minor_glob()
                    if _get_stored_sha(str(f), "branch_name") == cur_branch
                ]
                if stale:
                    print(f"STALE MINOR  ({stale[0].name})  — HEAD moved, run build_minor")
                else:
                    print(f"MAIN ONLY  ({MAIN_DB_PATH})  — run build_minor to enable tiered")
        else:
            print(f"MAIN ONLY  ({MAIN_DB_PATH})")
    elif legacy_p.exists():
        print(f"LEGACY  ({DB_PATH})")
    else:
        print("NO INDEX FOUND")


# ── 搜尋共用工具 ──────────────────────────────────────────────────────────────

def _get_read_conn() -> sqlite3.Connection:
    global _read_conn
    with _read_conn_lock:
        if _read_conn is None:
            _read_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            _read_conn.execute("PRAGMA query_only = ON")
            _read_conn.execute("PRAGMA mmap_size  = 268435456")
            _read_conn.execute("PRAGMA cache_size = -32768")
        return _read_conn


def _open_ro(path: str) -> sqlite3.Connection:
    """Open a DB file in read-only mode with standard perf pragmas."""
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.execute("PRAGMA query_only = ON")
    c.execute("PRAGMA mmap_size  = 268435456")
    c.execute("PRAGMA cache_size = -32768")
    return c


def _get_active_conns() -> tuple:
    """
    Determine which DB connection(s) to query based on what exists in CWD.

    Returns
    -------
    (primary, secondary_or_None, tombstone_paths: set)

    Modes
    -----
    Tiered    — minor_<branch>.db + main_index.db both present
                primary=minor, secondary=main, tombstones from minor DB
    Main-only — only main_index.db present
                primary=main, secondary=None, tombstones=∅
    Legacy    — file_index.db present (old single-DB mode)
                primary=file_index, secondary=None, tombstones=∅
    """
    main_exists = Path(MAIN_DB_PATH).exists()
    if main_exists:
        repo_dir    = REPO_DIR or str(Path.cwd())
        if DEBUG_MODE:
            print(f"[INFO] CWD       : {Path.cwd()}")
            print(f"[INFO] Repo dir  : {repo_dir}")
        branch      = _current_branch(repo_dir)
        branch_head = _git_head(repo_dir)
        if branch and branch_head:
            minor_db = _find_minor_db(branch_head)
            if minor_db:
                print(f"[分支索引] {Path(minor_db).name}  →  {Path(MAIN_DB_PATH).name}")
                minor_conn = _open_ro(minor_db)
                main_conn  = _open_ro(MAIN_DB_PATH)
                try:
                    tombstones = {
                        r[0] for r in minor_conn.execute(
                            "SELECT path FROM tombstones"
                        )
                    }
                except Exception:
                    tombstones = set()
                return minor_conn, main_conn, tombstones
            else:
                # ── No minor for current HEAD — try auto-build ────────────
                main_indexed = _get_stored_sha(MAIN_DB_PATH, "last_commit")
                n = None
                if main_indexed:
                    n = _git_commits_ahead(repo_dir, main_indexed, branch_head)

                # Auto-build if threshold enabled and commits ahead is small
                if (AUTO_BUILD_THRESHOLD > 0
                        and n is not None and 0 < n <= AUTO_BUILD_THRESHOLD
                        and branch):
                    print(f"[自動建立] 分支 '{branch}' 領先 main {n} 個 commit，自動建立 minor index…")
                    try:
                        build_minor(repo_dir)
                    except Exception as exc:
                        print(f"[自動建立] 失敗: {exc}")
                    # Re-check after build
                    minor_db = _find_minor_db(branch_head)
                    if minor_db:
                        print(f"[分支索引] {Path(minor_db).name}  →  {Path(MAIN_DB_PATH).name}")
                        minor_conn = _open_ro(minor_db)
                        main_conn  = _open_ro(MAIN_DB_PATH)
                        try:
                            tombstones = {
                                r[0] for r in minor_conn.execute(
                                    "SELECT path FROM tombstones"
                                )
                            }
                        except Exception:
                            tombstones = set()
                        return minor_conn, main_conn, tombstones

                # ── Fall through: warn and use MAIN ONLY ──────────────────
                ahead_msg = ""
                if n is not None and n > 0:
                    ahead_msg = f"\n       分支領先 main 索引 {n} 個 commit。"
                # Check whether an older minor DB exists for this branch
                stale_dbs = [
                    f for f in _minor_glob()
                    if _get_stored_sha(str(f), "branch_name") == branch
                ]
                if stale_dbs:
                    stale_name = stale_dbs[0].name
                    stale_head = _get_stored_sha(str(stale_dbs[0]), "branch_head") or "?"
                    print(
                        f"\033[33m[警告] 分支 '{branch}' 的索引已過時。"
                        f"{ahead_msg}\n"
                        f"       索引建立於 commit {stale_head[:7]}，目前 HEAD 為 {branch_head[:7]}。\n"
                        f"       搜尋結果僅來自 main_index.db，可能遺漏此分支的異動。\n"
                        f"       請執行: python build_index.py build_minor\033[0m"
                    )
                else:
                    expected = _minor_db_for_sha(branch_head)
                    print(
                        f"\033[33m[警告] 分支 '{branch}' 尚未建立索引 ({expected} 不存在)。"
                        f"{ahead_msg}\n"
                        f"       搜尋結果僅來自 main_index.db，可能遺漏此分支的異動。\n"
                        f"       請執行: python build_index.py build_minor\033[0m"
                    )
        main_conn = _open_ro(MAIN_DB_PATH)
        return main_conn, None, set()

    # Legacy fallback
    return _get_read_conn(), None, set()


def _run_query(primary, secondary, tombstones: set,
               sql: str, params: tuple) -> list:
    """
    Execute *sql* on *primary*; if *secondary* exists also execute on it,
    then merge — minor results take precedence, tombstones filter main results.
    """
    rows = primary.execute(sql, params).fetchall()
    if secondary:
        seen = {r[0] for r in rows}
        for r in secondary.execute(sql, params).fetchall():
            if r[0] not in tombstones and r[0] not in seen:
                rows.append(r)
    return rows


def _scope_clause(scope: str) -> str:
    """Return a SQL fragment to filter by file type scope."""
    if scope == "txt":
        return "AND m.is_text = 1"
    if scope == "bin":
        return "AND m.is_text = 0"
    return ""

def _find_matching_lines(path: str, terms: list[str]) -> list[tuple[int, str]]:
    """
    Return (1-based lineno, stripped line text) for every line where any of
    *terms* appears (case-insensitive).  Returns [] for binary files or on IO error.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lower_terms = [t.lower() for t in terms if t]
            hits: list[tuple[int, str]] = []
            for lineno, line in enumerate(f, 1):
                if any(t in line.lower() for t in lower_terms):
                    hits.append((lineno, line.rstrip()))
            return hits
    except Exception:
        return []


def _apply_search_filters(
    rows: list,
    ext_filter:   list[str],   # keep only these extensions, e.g. ['.c', '.h']
    path_filter:  list[str],   # keep only paths containing any of these substrings
    excl_filter:  list[str],   # drop paths matching any of these fnmatch globs
    grep_filter:  list[str],   # keep only result-lines containing all of these terms
) -> list:
    """
    Apply post-query result filters.  All filters are case-insensitive.
    Returns the filtered row list (same (path, is_text) tuples).
    """
    import fnmatch
    out = []
    for path, is_text in rows:
        pl = path.lower()
        # Extension filter
        if ext_filter:
            ext = Path(path).suffix.lower()
            if ext not in ext_filter:
                continue
        # Path inclusion filter
        if path_filter:
            if not any(p in pl for p in path_filter):
                continue
        # Exclude glob filter
        if excl_filter:
            name = Path(path).name.lower()
            if any(fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(pl, pat)
                   for pat in excl_filter):
                continue
        out.append((path, is_text))
    return out


def _print_results(rows: list, elapsed: float, label: str,
                   sort: bool = True, terms: list[str] | None = None,
                   ext_filter:  list[str] | None = None,
                   path_filter: list[str] | None = None,
                   excl_filter: list[str] | None = None,
                   grep_filter: list[str] | None = None):
    # Apply post-query filters
    if ext_filter or path_filter or excl_filter:
        rows = _apply_search_filters(
            rows,
            ext_filter  or [],
            [p.lower() for p in (path_filter  or [])],
            [p.lower() for p in (excl_filter  or [])],
            [g.lower() for g in (grep_filter  or [])],
        )
    if not rows:
        print(f"[{label}] 沒有符合的檔案。")
        return
    display = sorted(rows, key=lambda r: r[0]) if sort else rows

    matched_files = 0
    matched_lines = 0
    output: list[str] = []
    for path, is_text in display:
        if terms and is_text:
            hits = _find_matching_lines(path, terms)
            # Apply grep filter to individual lines
            if grep_filter:
                gl = [g.lower() for g in grep_filter]
                hits = [(n, t) for n, t in hits if all(g in t.lower() for g in gl)]
            if hits:
                matched_files += 1
                matched_lines += len(hits)
                for lineno, text in hits:
                    output.append(f"{path}({lineno}) :{text}")
                continue
        # Binary or no-hit text
        matched_files += 1
        tag = 'TXT' if is_text else 'BIN'
        output.append(f"  [{tag}] {path}")

    print(f"[{label}] 找到 {matched_files} 個檔案，{matched_lines} 行（{elapsed*1000:.1f} ms）：\n")
    for line in output:
        print(line)


# ── 精確搜尋 ──────────────────────────────────────────────────────────────────

def search_exact(keywords: list[str], sort: bool = True, scope: str = "all",
                 ext_filter=None, path_filter=None, excl_filter=None, grep_filter=None):
    """
    精確 AND 查詢。
    scope: 'all' | 'txt' | 'bin'  — 限制搜尋範圍，跳過不相關的檔案類型。
    """
    if not keywords:
        print("沒有符合的結果。")
        return
    query = " ".join(f'"{kw}"' for kw in keywords)
    sc    = _scope_clause(scope)
    sql   = ("SELECT fc.path, m.is_text "
             "FROM file_content fc "
             "JOIN file_meta m ON fc.path = m.path "
             f"WHERE file_content MATCH ? {sc}")
    primary, secondary, tombstones = _get_active_conns()
    t0    = time.perf_counter()
    rows  = _run_query(primary, secondary, tombstones, sql, (query,))
    elapsed = time.perf_counter() - t0
    label = f'精確: {" AND ".join(keywords)}'
    if scope != "all":
        label += f" [{scope.upper()} only]"
    _print_results(rows, elapsed, label, sort=sort, terms=keywords,
                   ext_filter=ext_filter, path_filter=path_filter,
                   excl_filter=excl_filter, grep_filter=grep_filter)


# ── 前綴搜尋 ──────────────────────────────────────────────────────────────────

def search_prefix(prefix: str, sort: bool = True, scope: str = "all",
                  ext_filter=None, path_filter=None, excl_filter=None, grep_filter=None):
    sc      = _scope_clause(scope)
    sql     = ("SELECT fc.path, m.is_text "
               "FROM file_content fc "
               "JOIN file_meta m ON fc.path = m.path "
               f"WHERE file_content MATCH ? {sc}")
    primary, secondary, tombstones = _get_active_conns()
    t0      = time.perf_counter()
    rows    = _run_query(primary, secondary, tombstones, sql, (_fts5_phrase(prefix) + "*",))
    elapsed = time.perf_counter() - t0
    label   = f"前綴: {prefix}*"
    if scope != "all":
        label += f" [{scope.upper()} only]"
    _print_results(rows, elapsed, label, sort=sort, terms=[prefix],
                   ext_filter=ext_filter, path_filter=path_filter,
                   excl_filter=excl_filter, grep_filter=grep_filter)


# ── 子字串搜尋 ────────────────────────────────────────────────────────────────

def search_substr(fragment: str, sort: bool = True, scope: str = "all",
                  ext_filter=None, path_filter=None, excl_filter=None, grep_filter=None):
    sc      = _scope_clause(scope)
    primary, secondary, tombstones = _get_active_conns()
    t0      = time.perf_counter()

    if len(fragment) >= 3:
        sql    = ("SELECT fc.path, m.is_text "
                  "FROM file_content fc "
                  "JOIN file_meta m ON fc.path = m.path "
                  f"WHERE file_content MATCH ? {sc}")
        params = (f'"{fragment}"',)
    else:
        sql    = ("SELECT fc.path, m.is_text "
                  "FROM file_content fc "
                  "JOIN file_meta m ON fc.path = m.path "
                  f"WHERE fc.content LIKE ? {sc}")
        params = (f"%{fragment}%",)

    rows    = _run_query(primary, secondary, tombstones, sql, params)
    elapsed = time.perf_counter() - t0
    label   = f"子字串: *{fragment}*"
    if scope != "all":
        label += f" [{scope.upper()} only]"
    _print_results(rows, elapsed, label, sort=sort, terms=[fragment],
                   ext_filter=ext_filter, path_filter=path_filter,
                   excl_filter=excl_filter, grep_filter=grep_filter)


# ── Glob 萬用字元搜尋 ─────────────────────────────────────────────────────────

def _glob_to_regex(pattern: str) -> re.Pattern:
    """Convert a glob pattern to a compiled regex (case-insensitive).

    Wildcards:
      *      → any sequence of characters (including none)
      ?      → exactly one character
      [...]  → character class, e.g. [0-9], [a-zA-Z], [^x]
    All other characters are regex-escaped as literals.
    """
    i = 0
    buf: list[str] = []
    while i < len(pattern):
        c = pattern[i]
        if c == '*':
            buf.append('.*')
            i += 1
        elif c == '?':
            buf.append('.')
            i += 1
        elif c == '[':
            j = pattern.find(']', i + 1)
            if j == -1:                         # no closing ] → treat as literal
                buf.append(re.escape(c))
                i += 1
            else:
                buf.append(pattern[i:j + 1])    # pass [...] straight to regex
                i = j + 1
        else:
            buf.append(re.escape(c))
            i += 1
    return re.compile(''.join(buf), re.IGNORECASE)


def _glob_literal_parts(pattern: str) -> list[str]:
    """Extract literal (non-wildcard) segments ≥ 3 chars for FTS5 pre-filtering.

    Splits on *, ?, and [...] wildcards.
    """
    return [s for s in re.split(r'\*|\?|\[[^\]]*\]', pattern) if len(s) >= 3]


def search_glob(pattern: str, sort: bool = True, scope: str = "all",
                ext_filter=None, path_filter=None, excl_filter=None, grep_filter=None):
    """
    Wildcard search supporting *, ?, and [...] glob syntax.

      *           — any sequence of characters  (AAA*BBB  → 'AAA xxxxxx BBB')
      ?           — exactly one character        (AAA?BBB  → 'AAAXBBB')
      [0-9]       — character class              (AAA[0-9]BBB → 'AAA5BBB')

    Phase 1: extract literal parts ≥ 3 chars, require all in file (FTS5 AND).
    Phase 2: compile full pattern to regex and verify line by line.
    """
    literal_parts = _glob_literal_parts(pattern)
    if not literal_parts and not re.sub(r'\*|\?|\[[^\]]*\]', '', pattern).strip():
        print("[Glob] Pattern must contain at least one non-wildcard term (≥ 1 char).")
        return

    regex = _glob_to_regex(pattern)

    sc  = _scope_clause(scope)
    primary, secondary, tombstones = _get_active_conns()
    t0  = time.perf_counter()

    if literal_parts:
        and_query = " ".join(_fts5_phrase(p) for p in literal_parts)
        sql = ("SELECT fc.path, m.is_text "
               "FROM file_content fc "
               "JOIN file_meta m ON fc.path = m.path "
               f"WHERE file_content MATCH ? {sc}")
        rows = _run_query(primary, secondary, tombstones, sql, (and_query,))
    else:
        # All literals < 3 chars — LIKE fallback on stripped literal text
        first_lit = re.sub(r'\*|\?|\[[^\]]*\]', '', pattern)
        sql = ("SELECT fc.path, m.is_text "
               "FROM file_content fc "
               "JOIN file_meta m ON fc.path = m.path "
               f"WHERE fc.content LIKE ? {sc}")
        rows = _run_query(primary, secondary, tombstones, sql, (f"%{first_lit}%",))

    elapsed = time.perf_counter() - t0

    # Apply path/ext/excl filters
    if ext_filter or path_filter or excl_filter:
        rows = _apply_search_filters(
            rows,
            ext_filter  or [],
            [p.lower() for p in (path_filter  or [])],
            [p.lower() for p in (excl_filter  or [])],
            [],
        )

    label = f"Glob: {pattern}"
    if scope != "all":
        label += f" [{scope.upper()} only]"

    if not rows:
        print(f"[{label}] 沒有符合的檔案。")
        return

    if sort:
        rows = sorted(rows, key=lambda r: r[0])

    matched_files = 0
    matched_lines = 0
    output: list[str] = []
    for path, is_text in rows:
        if is_text:
            hits: list[tuple[int, str]] = []
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for lineno, line in enumerate(f, 1):
                        if regex.search(line):
                            hits.append((lineno, line.rstrip()))
            except Exception:
                pass
            if grep_filter:
                gl = [g.lower() for g in grep_filter]
                hits = [(n, t) for n, t in hits if all(g in t.lower() for g in gl)]
            if hits:
                matched_files += 1
                matched_lines += len(hits)
                for lineno, text in hits:
                    output.append(f"{path}({lineno}) :{text}")
                continue
        matched_files += 1
        tag = "TXT" if is_text else "BIN"
        output.append(f"  [{tag}] {path}")

    print(f"[{label}] 找到 {matched_files} 個檔案，{matched_lines} 行（{elapsed*1000:.1f} ms）：\n")
    for line in output:
        print(line)



_FUZZY_ALPHABET    = "abcdefghijklmnopqrstuvwxyz0123456789_"
_FUZZY_MAX_DIST    = 3       # hard cap on edit distance


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    la, lb = len(a), len(b)
    if la < lb:
        a, b, la, lb = b, a, lb, la
    prev = list(range(lb + 1))
    for ca in a:
        curr = [prev[0] + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def _parse_structure(keyword: str) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Parse a compound C expression into identifier parts and ordering constraints.

    Returns:
      parts       — all identifier tokens (>= 3 chars) for fuzzy FTS5 + line matching
      constraints — list of (left_id, sep, right_id) ordering rules where sep is
                    '->' | '.' | '('  indicating structural relationship

    Examples:
      'A->B.C'           parts=['A','B','C']  constraints=[('A','->','B'),('B','.','C')]
      'A(B)'             parts=['A','B']       constraints=[('A','(','B')]
      'A->B(C)'          parts=['A','B','C']   constraints=[('A','->','B'),('B','(','C')]
      'plain'            parts=['plain']       constraints=[]
    """
    parts: list[str] = []
    constraints: list[tuple[str, str, str]] = []

    # Tokenise: find identifiers and separators in order
    token_re = re.compile(r'([A-Za-z0-9_]+)|(->|\.|\()')
    tokens = token_re.findall(keyword)   # list of (ident_or_empty, sep_or_empty)

    idents: list[str] = []
    seps:   list[str] = []
    for ident, sep in tokens:
        if ident:
            idents.append(ident)
        elif sep:
            seps.append(sep)

    parts = [t for t in idents if len(t) >= 3]
    if not parts:
        parts = idents if idents else [keyword]

    # Build constraints: pair each separator with the identifiers on either side
    # Walk through idents and interleaved seps
    raw: list[str] = []
    for ident, sep in tokens:
        if ident:
            raw.append(ident)
        elif sep:
            raw.append(sep)

    i = 0
    while i < len(raw) - 2:
        left  = raw[i]
        mid   = raw[i + 1]
        right = raw[i + 2]
        if re.match(r'[A-Za-z0-9_]+', left) and mid in ('->', '.', '(') and re.match(r'[A-Za-z0-9_]+', right):
            if len(left) >= 2 and len(right) >= 2:   # skip very short tokens
                constraints.append((left, mid, right))
            i += 2
        else:
            i += 1

    return parts, constraints


def _constraint_to_regex(left: str, sep: str, right: str, max_dist: int) -> re.Pattern | None:
    """Build a regex that checks the structural ordering of a constraint on a line.

    For sep '->' or '.':  left_fuzzy  sep_literal  right_fuzzy
    For sep '(':          left_fuzzy  \\(  .*  right_fuzzy

    Uses the original tokens as anchors (exact); fuzzy is handled by the token
    scanner. This regex only enforces ORDER and STRUCTURE, not exact spelling.
    We use .{0,N} to allow for 1-3 char differences near the token.
    """
    # We intentionally keep this loose — just check left appears before right
    # with the correct separator structure. Spelling is verified by _levenshtein.
    l_pat = re.escape(left[:max(3, len(left) - max_dist)])   # prefix anchor
    r_pat = re.escape(right[:max(3, len(right) - max_dist)])
    if sep == '(':
        return re.compile(rf'{l_pat}.{{0,{max_dist + 3}}}\(.*?{r_pat}', re.IGNORECASE)
    else:
        sep_pat = re.escape(sep)
        return re.compile(rf'{l_pat}.{{0,{max_dist + 3}}}{sep_pat}.{{0,{max_dist + 3}}}{r_pat}', re.IGNORECASE)


def _split_compound(keyword: str) -> list[str]:
    """Extract all identifier tokens (≥ 3 chars) from a compound C expression.

    Handles ->, ., (, ), ,, and any other non-word separators.
    e.g. 'gRedfishHProtocol->IsOneShotIventorySupported(gRedfishHiProtocol)'
         → ['gRedfishHProtocol', 'IsOneShotIventorySupported', 'gRedfishHiProtocol']
    """
    parts, _ = _parse_structure(keyword)
    return parts


def _scan_lines_fuzzy(path: str, parts: list[str], max_dist: int,
                      constraints: list[tuple[str, str, str]] | None = None) -> list[tuple[int, str]]:
    """Return (1-based lineno, line) where ALL parts have a fuzzy-matching token
    AND all structural constraints (A->B, A(B), A.B) are satisfied.

    Each part is matched independently against word tokens in the line.
    Constraints enforce ordering/structure via prefix-anchor regex.
    """
    parts_lower = [p.lower() for p in parts]
    part_lens   = [len(p) for p in parts_lower]
    # Pre-compile constraint regexes
    c_patterns: list[re.Pattern] = []
    if constraints:
        for left, sep, right in constraints:
            pat = _constraint_to_regex(left, sep, right, max_dist)
            if pat:
                c_patterns.append(pat)
    hits: list[tuple[int, str]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for lineno, line in enumerate(f, 1):
                tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9_]+", line)]
                # All parts must fuzzy-match some token
                if not all(
                    any(abs(len(t) - klen) <= max_dist and
                        _levenshtein(kw, t) <= max_dist
                        for t in tokens)
                    for kw, klen in zip(parts_lower, part_lens)
                ):
                    continue
                # All structural constraints must match
                if c_patterns and not all(p.search(line) for p in c_patterns):
                    continue
                hits.append((lineno, line.rstrip()))
    except Exception:
        pass
    return hits


def _fuzzy_variants_dist1(keyword: str) -> list[str]:
    """Return all words within edit distance 1 of *keyword* (deletions + substitutions)."""
    kw_lower = keyword.lower()
    variants: set[str] = {kw_lower}
    for i in range(len(kw_lower)):
        variants.add(kw_lower[:i] + kw_lower[i + 1:])          # deletion
        for c in _FUZZY_ALPHABET:
            if c != kw_lower[i]:
                variants.add(kw_lower[:i] + c + kw_lower[i + 1:])  # substitution
    return [v for v in variants if len(v) >= 3]


def _pigeonhole_parts(keyword: str, max_dist: int) -> list[str]:
    """
    Pigeonhole principle: if edit_dist(query, target) ≤ d, then splitting query
    into (d+1) equal parts guarantees at least one part appears verbatim in target.
    Returns the parts (those ≥ 3 chars long) for use as FTS5 substring filters.
    """
    kw = keyword.lower()
    n  = len(kw)
    num_parts = max_dist + 1
    part_len  = n // num_parts
    parts: list[str] = []
    for i in range(num_parts):
        start = i * part_len
        end   = start + part_len if i < num_parts - 1 else n
        part  = kw[start:end]
        if len(part) >= 3:
            parts.append(part)
    return parts


def _bk_tree_fuzzy(conn, per_part_terms: list[list[str]], scope: str) -> list[tuple]:
    """Query FTS5 requiring at least one variant of EACH part to appear in the file.

    per_part_terms: list of variant-lists, one per identifier part.
    Files must satisfy ALL parts (AND), each part matched via OR across its variants.
    """
    sc = _scope_clause(scope)
    if not per_part_terms:
        return []
    BATCH = 500

    # Build one sub-query per part (OR across variants), intersect results
    result_sets: list[set[str]] = []
    result_rows: dict[str, tuple] = {}

    for part_variants in per_part_terms:
        part_paths: set[str] = set()
        for i in range(0, len(part_variants), BATCH):
            batch = part_variants[i:i + BATCH]
            query = " OR ".join(_fts5_phrase(v) for v in batch)
            try:
                for row in conn.execute(
                    f"""SELECT DISTINCT fc.path, m.is_text
                        FROM file_content fc
                        JOIN file_meta m ON fc.path = m.path
                        WHERE file_content MATCH ? {sc}""",
                    (query,)
                ).fetchall():
                    part_paths.add(row[0])
                    result_rows[row[0]] = row
            except Exception:
                pass
        result_sets.append(part_paths)

    # Intersection: file must contain fuzzy match of every part
    if not result_sets:
        return []
    common = result_sets[0]
    for s in result_sets[1:]:
        common = common & s
    return [result_rows[p] for p in common if p in result_rows]


def search_fuzzy(keyword: str, max_dist: int = 1, sort: bool = True, scope: str = "all",
                 ext_filter=None, path_filter=None, excl_filter=None, grep_filter=None,
                 _stop_event=None, _progress_cb=None):
    effective_dist = min(max_dist, _FUZZY_MAX_DIST)
    if effective_dist < max_dist:
        print(f"[WARN] Fuzzy dist capped at {_FUZZY_MAX_DIST} (requested {max_dist}).")

    # Split compound expressions into identifier parts + structural constraints
    parts, constraints = _parse_structure(keyword)

    # Phase 1: build per-part FTS5 term lists, then AND between parts
    per_part_terms: list[list[str]] = []
    for part in parts:
        variants: set[str] = set(_fuzzy_variants_dist1(part))
        if effective_dist >= 2:
            variants.update(_pigeonhole_parts(part, effective_dist))
        per_part_terms.append(list(variants))

    primary, secondary, tombstones = _get_active_conns()
    t0   = time.perf_counter()
    rows = _bk_tree_fuzzy(primary, per_part_terms, scope)
    if secondary:
        seen = {r[0] for r in rows}
        for r in _bk_tree_fuzzy(secondary, per_part_terms, scope):
            if r[0] not in tombstones and r[0] not in seen:
                rows.append(r)
    elapsed = time.perf_counter() - t0

    # Apply path/ext/excl filters
    if ext_filter or path_filter or excl_filter:
        rows = _apply_search_filters(
            rows,
            ext_filter  or [],
            [p.lower() for p in (path_filter  or [])],
            [p.lower() for p in (excl_filter  or [])],
            [],
        )

    label = f"Fuzzy(dist={effective_dist}): {keyword}"
    if scope != "all":
        label += f" [{scope.upper()} only]"

    if not rows:
        print(f"[{label}] 沒有符合的檔案。")
        return

    if sort:
        rows = sorted(rows, key=lambda r: r[0])

    # Phase 2: Levenshtein verify on lines with progress reporting and stop support
    total         = len(rows)
    matched_files = 0
    matched_lines = 0
    output: list[str] = []
    for idx, (path, is_text) in enumerate(rows):
        if _stop_event is not None and _stop_event.is_set():
            print(f"[{label}] 已中止（已掃描 {idx}/{total} 個檔案）。")
            break
        if _progress_cb is not None:
            _progress_cb(idx + 1, total)
        if is_text:
            hits = _scan_lines_fuzzy(path, parts, effective_dist, constraints)
            if grep_filter:
                gl = [g.lower() for g in grep_filter]
                hits = [(n, t) for n, t in hits if all(g in t.lower() for g in gl)]
            if hits:
                matched_files += 1
                matched_lines += len(hits)
                for lineno, text in hits:
                    output.append(f"{path}({lineno}) :{text}")
                continue
        matched_files += 1
        tag = "TXT" if is_text else "BIN"
        output.append(f"  [{tag}] {path}")

    print(f"[{label}] 找到 {matched_files} 個檔案，{matched_lines} 行（{elapsed*1000:.1f} ms）：\n")
    for line in output:
        print(line)



# ── HEX 搜尋 ──────────────────────────────────────────────────────────────────

def _normalise_hex_pattern(pat: str) -> str | None:
    """
    Accept flexible hex input and normalise to the token format used at index time.

    Supported input formats:
      DEADBEEF          → "DEADBEEF"          (already aligned 4-byte token)
      DE AD BE EF       → "DEADBEEF"          (space-separated bytes)
      0xDEADBEEF        → "DEADBEEF"          (C-style prefix)
      DE:AD:BE:EF       → "DEADBEEF"          (colon-separated)
      \\xDE\\xAD\\xBE\\xEF → "DEADBEEF"        (Python escape style)

    For patterns longer than HEX_CHUNK_BYTES*2 hex chars, the input is split
    into overlapping HEX_CHUNK_BYTES-sized tokens and joined with ' OR ' so the
    query still hits the sliding-window index.

    Returns None if the input contains non-hex characters after normalisation.
    """
    # Strip known prefixes / separators
    cleaned = (pat
               .replace("0x", "").replace("0X", "")
               .replace("\\x", "").replace(" ", "")
               .replace(":", "").replace("-", "")
               .upper())

    if not _HEX_RE.match(cleaned):
        return None

    # Pad to even length
    if len(cleaned) % 2:
        cleaned = "0" + cleaned

    # Convert to bytes then re-emit as sliding-window tokens
    try:
        raw = bytes.fromhex(cleaned)
    except ValueError:
        return None

    if len(raw) <= HEX_CHUNK_BYTES:
        # Short pattern — pad to chunk size with a wildcard isn't possible in
        # trigram FTS5, so we search the hex string directly (substring match)
        return cleaned.upper()

    # Long pattern — emit overlapping tokens, all must appear → AND query
    tokens = [
        raw[i : i + HEX_CHUNK_BYTES].hex().upper()
        for i in range(len(raw) - HEX_CHUNK_BYTES + 1)
    ]
    # AND of all tokens = all must appear in the same file
    return " ".join(f'"{t}"' for t in tokens)


def search_hex(patterns: list[str], sort: bool = True):
    """
    搜尋二進位檔中的 16 進位位元組序列。
    僅在以 --hex 建立的索引中有效（其他檔案會被自動跳過）。

    支援輸入格式：DEADBEEF / DE:AD:BE:EF / 0xDEADBEEF / \\xDE\\xAD\\xBE\\xEF
    多個 pattern 之間為 AND 邏輯（全部都必須出現在同一個檔案中）。
    """
    primary, secondary, tombstones = _get_active_conns()

    # Warn if no files were hex-indexed in any active DB
    count = primary.execute(
        "SELECT COUNT(*) FROM file_meta WHERE hex_indexed=1"
    ).fetchone()[0]
    if secondary:
        count += secondary.execute(
            "SELECT COUNT(*) FROM file_meta WHERE hex_indexed=1"
        ).fetchone()[0]
    if count == 0:
        print("[HEX] 警告：索引中沒有 HEX 索引的檔案。"
              "請使用 build --hex 重新建立索引。")
        return

    # Normalise and validate all patterns
    fts_parts = []
    for pat in patterns:
        norm = _normalise_hex_pattern(pat)
        if norm is None:
            print(f"[HEX] 無效的 16 進位 pattern，已跳過：{pat!r}")
            continue
        fts_parts.append(norm)

    if not fts_parts:
        print("[HEX] 沒有有效的 pattern。")
        return

    query = " ".join(fts_parts)
    sql   = ("SELECT fc.path, m.is_text "
             "FROM file_content fc "
             "JOIN file_meta m ON fc.path = m.path "
             "WHERE file_content MATCH ? "
             "  AND m.is_text = 0 AND m.hex_indexed = 1")
    t0    = time.perf_counter()
    rows  = _run_query(primary, secondary, tombstones, sql, (query,))
    elapsed = time.perf_counter() - t0
    label   = f"HEX: {' AND '.join(patterns)}"
    _print_results(rows, elapsed, label, sort=sort)


# ── 副檔名搜尋 ────────────────────────────────────────────────────────────────

_SEARCH_CMDS = {"search", "prefix", "substr", "fuzzy", "hex", "ext",
                "build", "build_main", "build_minor", "cleanup", "status"}


def search_ext(args: list[str], sort: bool = True):
    """
    Search by extension or built-in profile name.
    Profile names: all c sdl inf dec asm cfg mak asl dxs bat txt uni vfr py bin
    Mix freely: ext c .py   →  C/C++/H/HPP + .py files
    """
    # Guard: catch accidental multi-command input like "ext c search keyword"
    for arg in args:
        if arg.lower() in _SEARCH_CMDS:
            print(
                f"[ERROR] '{arg}' looks like a command name, not an extension.\n"
                f"        Each invocation takes ONE command. Examples:\n"
                f"          search keyword --ext .c .h\n"
                f"          search keyword --ext c       (profile name also works)\n"
                f"          ext c                        (list all .c/.h files)"
            )
            return
    exts: list[str] = []
    labels: list[str] = []
    for arg in args:
        key = arg.lstrip(".").lower()
        if arg.lower() in EXT_PROFILES:
            profile_exts = EXT_PROFILES[arg.lower()]
            if not profile_exts:          # "all" profile — no ext filter
                _list_all_ext(sort)
                return
            exts.extend(profile_exts)
            labels.append(arg.lower())
        elif key in EXT_PROFILES:
            profile_exts = EXT_PROFILES[key]
            if not profile_exts:
                _list_all_ext(sort)
                return
            exts.extend(profile_exts)
            labels.append(key)
        else:
            e = arg.lower() if arg.startswith(".") else f".{arg.lower()}"
            exts.append(e)
            labels.append(e)

    # Deduplicate while preserving order
    seen: set[str] = set()
    exts = [e for e in exts if not (e in seen or seen.add(e))]

    primary, secondary, tombstones = _get_active_conns()
    t0           = time.perf_counter()
    placeholders = ",".join("?" * len(exts))
    sql  = (f"SELECT path, is_text FROM file_meta "
            f"WHERE ext IN ({placeholders})")
    rows = _run_query(primary, secondary, tombstones, sql, tuple(exts))
    elapsed = time.perf_counter() - t0
    _print_results(rows, elapsed,
                   f"副檔名: {' | '.join(labels)} → {' '.join(exts)}",
                   sort=sort)


def _list_all_ext(sort: bool):
    """List all files (no extension filter — 'all' profile)."""
    primary, secondary, tombstones = _get_active_conns()
    t0   = time.perf_counter()
    sql  = "SELECT path, is_text FROM file_meta"
    rows = _run_query(primary, secondary, tombstones, sql, ())
    elapsed = time.perf_counter() - t0
    _print_results(rows, elapsed, "副檔名: all", sort=sort)


def list_ext_profiles():
    """Print the available extension profiles (from build_index_profiles.json)."""
    print(f"Extension profiles  [{_PROFILES_PATH}]")
    print(f"  {'Profile':<12}  Extensions")
    print(f"  {'-'*12}  {'-'*44}")
    for name, exts in EXT_PROFILES.items():
        exts_str = "  ".join(exts) if exts else "(all files)"
        print(f"  {name:<12}  {exts_str}")


# ── 主程式 ────────────────────────────────────────────────────────────────────

def _pop_flag(args: list[str], flag: str) -> bool:
    """Remove flag from args list and return True if it was present."""
    if flag in args:
        args.remove(flag)
        return True
    return False


def _pop_multi_values(args: list[str], flag: str) -> list[str]:
    """
    Remove *flag* and all immediately following non-flag tokens from *args*.
    E.g. args=['--ext', '.c', '.h', 'foo'] → returns ['.c', '.h'],
    args becomes ['foo'].
    """
    if flag not in args:
        return []
    idx = args.index(flag)
    args.pop(idx)          # remove the flag itself
    values: list[str] = []
    while idx < len(args) and not args[idx].startswith("--"):
        values.append(args.pop(idx))
    return values


def _pop_scope(args: list[str]) -> str:
    """Extract --txt / --bin scope flag, default 'all'."""
    if _pop_flag(args, "--txt"):
        return "txt"
    if _pop_flag(args, "--bin"):
        return "bin"
    return "all"

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd  = sys.argv[1]
    args = list(sys.argv[2:])

    # ── --db-dir: redirect all DB paths to a specific directory ─────────────
    _db_dir_vals = _pop_multi_values(args, "--db-dir")
    if _db_dir_vals:
        _db_dir = Path(_db_dir_vals[0]).resolve()
        _db_dir.mkdir(parents=True, exist_ok=True)
        globals()["DB_PATH"]         = str(_db_dir / "file_index.db")
        globals()["MAIN_DB_PATH"]    = str(_db_dir / "main_index.db")
        globals()["MINOR_DB_PREFIX"] = str(_db_dir / "minor_")

    # ── --label: version-label the main and minor DB filenames ───────────────
    _label_vals = _pop_multi_values(args, "--label")
    if _label_vals:
        _label   = _label_vals[0]
        _db_base = Path(globals()["MAIN_DB_PATH"]).parent
        globals()["MAIN_DB_PATH"]    = str(_db_base / f"main_{_label}.db")
        globals()["MINOR_DB_PREFIX"] = str(_db_base / f"minor_{_label}_")

    sort      = not _pop_flag(args, "--no-sort")
    scope     = _pop_scope(args)
    index_hex = _pop_flag(args, "--hex")

    # ── Search-time filters (consumed before search keyword args) ──────────────
    _raw_ext = _pop_multi_values(args, "--ext")
    ext_filter: list[str] = []
    for _e in _raw_ext:
        _key = _e.lstrip(".").lower()
        if _key in EXT_PROFILES and EXT_PROFILES[_key]:
            ext_filter.extend(EXT_PROFILES[_key])   # expand profile name
        elif _e.startswith("."):
            ext_filter.append(_e.lower())
        else:
            ext_filter.append(f".{_e.lower()}")
    path_filter = _pop_multi_values(args, "--path")
    excl_filter = _pop_multi_values(args, "--exclude")
    grep_filter = _pop_multi_values(args, "--grep")

    _sf = dict(ext_filter=ext_filter or None, path_filter=path_filter or None,
               excl_filter=excl_filter or None, grep_filter=grep_filter or None)

    if cmd == "build" and args:
        workers = DEFAULT_WORKERS
        if "--workers" in args:
            idx     = args.index("--workers")
            workers = int(args[idx + 1])
            args    = [a for i, a in enumerate(args) if i not in (idx, idx + 1)]
        counters, elapsed, total = build_index(
            args[0], num_workers=workers, index_hex=index_hex
        )
        skipped = counters["skipped_mtime"] + counters["skipped_md5"]
        print(
            f"  完成：更新 {counters['updated']}，跳過 {skipped}，"
            f"共 {total} 個檔案（耗時 {elapsed:.1f}s）"
        )

    elif cmd == "build_main" and args:
        workers = DEFAULT_WORKERS
        if "--workers" in args:
            idx     = args.index("--workers")
            workers = int(args[idx + 1])
            args    = [a for i, a in enumerate(args) if i not in (idx, idx + 1)]
        build_main(args[0], num_workers=workers, index_hex=index_hex)

    elif cmd == "build_minor" and args:
        workers = DEFAULT_WORKERS
        if "--workers" in args:
            idx     = args.index("--workers")
            workers = int(args[idx + 1])
            args    = [a for i, a in enumerate(args) if i not in (idx, idx + 1)]
        build_minor(args[0], num_workers=workers, index_hex=index_hex)

    elif cmd in ("build_chain", "build_history") and args:
        workers = DEFAULT_WORKERS
        if "--workers" in args:
            idx     = args.index("--workers")
            workers = int(args[idx + 1])
            args    = [a for i, a in enumerate(args) if i not in (idx, idx + 1)]
        fn = build_chain if cmd == "build_chain" else build_history
        fn(args[0], num_workers=workers, index_hex=index_hex)

    elif cmd == "build_promote":
        _from  = _pop_multi_values(args, "--from")
        _minor = _pop_multi_values(args, "--minor")
        _to    = _pop_multi_values(args, "--to")
        if not _from or not _minor or not _to:
            print("Usage: build_promote --from <ver> --minor <sha7> --to <new_ver>")
            sys.exit(1)
        build_promote(_from[0], _minor[0], _to[0])

    elif cmd == "cleanup":
        cleanup_indexes(args[0] if args else ".")

    elif cmd == "status":
        status_indexes(args[0] if args else ".")

    elif cmd == "search" and args:
        search_exact(args, sort=sort, scope=scope, **_sf)

    elif cmd == "prefix" and args:
        search_prefix(args[0], sort=sort, scope=scope, **_sf)

    elif cmd == "substr" and args:
        search_substr(args[0], sort=sort, scope=scope, **_sf)

    elif cmd == "fuzzy" and args:
        dist = int(args[1]) if len(args) >= 2 and args[1].isdigit() else 1
        search_fuzzy(args[0], dist, sort=sort, scope=scope, **_sf)

    elif cmd == "hex" and args:
        search_hex(args, sort=sort)

    elif cmd == "ext" and args:
        if args[0] == "--list":
            list_ext_profiles()
        else:
            search_ext(args, sort=sort)

    elif cmd == "ext":
        list_ext_profiles()

    else:
        print(__doc__)
