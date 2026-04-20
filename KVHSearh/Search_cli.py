#!/usr/bin/env python3
"""
Search_cli.py  —  Search interface for KVHSearch
Delegates all logic to build_index.py; this script only handles CLI parsing.

用法：
  python Search_cli.py search <keyword> [kw2 ...]  [--txt|--bin] [--no-sort]
  python Search_cli.py prefix <prefix>             [--txt|--bin] [--no-sort]
  python Search_cli.py substr <fragment>           [--txt|--bin] [--no-sort]
  python Search_cli.py fuzzy  <keyword> [dist]     [--txt|--bin] [--no-sort]
  python Search_cli.py hex    <DEADBEEF> [pat2 ...]              [--no-sort]
  python Search_cli.py ext    <.ext|profile> [...]               [--no-sort]
  python Search_cli.py ext    --list

  --txt           只搜尋文字檔
  --bin           只搜尋二進位檔
  --no-sort       不排序輸出（速度較快）
  --db-dir <path> 從指定目錄讀取 DB 檔案

搜尋結果篩選（可自由組合）：
  --ext   .c .h         只顯示這些副檔名的結果
  --path  LenovoWsPkg   只顯示路徑中含此字串的結果（可多個，OR 邏輯）
  --exclude *.inf *.sdl 排除符合 glob 的檔案
  --grep  EFI_STATUS    只顯示同時含此關鍵字的行（可多個，AND 邏輯）

  範例：
    python Search_cli.py search OemDxe
    python Search_cli.py search OemDxe --ext .c .h
    python Search_cli.py search OemDxe --path LenovoWsPkg --grep EFI_STATUS
    python Search_cli.py search OemDxe --exclude *.inf --db-dir D:/indexes/Cathaya
    python Search_cli.py prefix Oem    --ext .h --exclude *Test*
    python Search_cli.py substr OemDxe --path OemDxe --grep TRACE
    python Search_cli.py fuzzy  OemDxe 1
    python Search_cli.py hex    DEADBEEF
    python Search_cli.py ext    .c .h
    python Search_cli.py ext    --list
"""

import sys
from pathlib import Path

# ── Import engine ─────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

import kvhsearch_core as _eng
from kvhsearch_core import (
    search_exact, search_prefix, search_substr,
    search_fuzzy, search_hex, search_ext, list_ext_profiles,
    EXT_PROFILES,
    _pop_flag, _pop_multi_values, _pop_scope,
)


def _apply_db_dir(args: list[str]) -> None:
    """Redirect DB paths when --db-dir is given; set REPO_DIR when --repo-dir is given."""
    vals = _pop_multi_values(args, "--db-dir")
    if vals:
        db_dir = Path(vals[0]).resolve()
        _eng.DB_PATH         = str(db_dir / "file_index.db")
        _eng.MAIN_DB_PATH    = str(db_dir / "main_index.db")
        _eng.MINOR_DB_PREFIX = str(db_dir / "minor_")
    repo_vals = _pop_multi_values(args, "--repo-dir")
    if repo_vals:
        _eng.REPO_DIR = str(Path(repo_vals[0]).resolve())


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd  = sys.argv[1]
    args = list(sys.argv[2:])

    _apply_db_dir(args)

    sort  = not _pop_flag(args, "--no-sort")
    scope = _pop_scope(args)

    # Search result filters — --ext accepts both raw extensions and profile names
    _raw_ext = _pop_multi_values(args, "--ext")
    ext_filter: list[str] = []
    for _e in _raw_ext:
        _key = _e.lstrip(".").lower()
        if _key in _eng.EXT_PROFILES and _eng.EXT_PROFILES[_key]:
            ext_filter.extend(_eng.EXT_PROFILES[_key])   # expand profile name
        elif _e.startswith("."):
            ext_filter.append(_e.lower())
        else:
            ext_filter.append(f".{_e.lower()}")
    path_filter = _pop_multi_values(args, "--path")
    excl_filter = _pop_multi_values(args, "--exclude")
    grep_filter = _pop_multi_values(args, "--grep")

    sf = dict(
        ext_filter  = ext_filter  or None,
        path_filter = path_filter or None,
        excl_filter = excl_filter or None,
        grep_filter = grep_filter or None,
    )

    if cmd == "search" and args:
        search_exact(args, sort=sort, scope=scope, **sf)

    elif cmd == "prefix" and args:
        search_prefix(args[0], sort=sort, scope=scope, **sf)

    elif cmd == "substr" and args:
        search_substr(args[0], sort=sort, scope=scope, **sf)

    elif cmd == "fuzzy" and args:
        dist = int(args[1]) if len(args) >= 2 and args[1].isdigit() else 1
        search_fuzzy(args[0], dist, sort=sort, scope=scope, **sf)

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
        sys.exit(1)


if __name__ == "__main__":
    main()
