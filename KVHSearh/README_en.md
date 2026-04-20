# KVHSearch

Full-text trigram search engine for source code repositories.  
Built for large UEFI / firmware codebases that frequently switch git branches.

---

## Contents

1. [Overview](#1-overview)
2. [Requirements](#2-requirements)
3. [File Layout](#3-file-layout)
4. [Algorithm & Design](#4-algorithm--design)
5. [Indexing Flow](#5-indexing-flow)
6. [Search Flow](#6-search-flow)
7. [Build Commands](#7-build-commands)
8. [Search Commands](#8-search-commands)
   - [search](#search--exact-and), [prefix](#prefix--prefix-match), [substr](#substr--substring-match), [glob](#glob--wildcard-match), [fuzzy](#fuzzy--typo-tolerant), [hex](#hex--binary-byte-pattern-search), [ext](#ext--extension--profile-filter)
9. [Search Filters](#9-search-filters)
10. [Management Commands](#10-management-commands)
11. [Multi-Version Indexes](#11-multi-version-indexes)
12. [GUI (KVHSearch_Gui)](#12-gui-kvhsearch_gui)
13. [Skip Rules](#13-skip-rules)
14. [Extension Profiles](#14-extension-profiles)
15. [--db-dir: External Index Storage](#15---db-dir-external-index-storage)
16. [Git Hooks](#16-git-hooks)
17. [Typical Daily Workflow](#17-typical-daily-workflow)
18. [Performance Notes](#18-performance-notes)

---

## 1. Overview

KVHSearch indexes source files into a local SQLite FTS5 trigram database and provides fast command-line search. It is designed for codebases where:

- Files are large in number (10 000–100 000+)
- Git branches are switched frequently
- Searches need to be fast (< 10 ms typical)
- Both text and binary files must be searchable

The **tiered index** architecture means you build a full base index once on `main`, then only diff-index each branch — keeping `build_minor` runs under a few seconds even on large repos.

---

## 2. Requirements

- Python 3.10+
- Git (must be in `PATH`)
- No third-party packages — stdlib only (`sqlite3`, `hashlib`, `threading`, `subprocess`)

---

## 3. File Layout

```
KVHSearch/
  kvhsearch_core.py           ← full engine (library + combined CLI)
  Build_Index_cli.py          ← build-only entry point
  Search_cli.py               ← search-only entry point
  KVHSearch_Gui.py            ← tkinter GUI front-end
  build_index.py              ← backward-compat shim → delegates to core
  build_index_profiles.json   ← user-editable extension profiles + skip rules
  hooks/
    post-checkout             ← git hook: auto build_minor after checkout
    post-merge                ← git hook: auto build_minor after merge
```

**Generated at runtime (in `--db-dir` or CWD):**

| File | Description |
|---|---|
| `main_<label>.db` | Full index of a version (e.g. `main_v08.db`) |
| `minor_<label>_<sha7>.db` | Delta index keyed by label + branch HEAD SHA |
| `main_index.db` | Legacy main (no `--label`) |
| `minor_<sha7>.db` | Legacy minor (no `--label`) |
| `file_index.db` | Legacy single-index (backward compatible) |

---

## 4. Algorithm & Design

### 4.1 Storage — SQLite FTS5 Trigram

Every file's content is broken into overlapping 3-character sequences (trigrams) and stored in an FTS5 virtual table. SQLite's built-in tokenizer handles the indexing.

```
"OemDxe" → "Oem", "emD", "mDx", "Dxe"
```

Queries hit the trigram index directly — no linear file scan at search time.

### 4.2 Content Extraction

| File type | Extraction method |
|---|---|
| Text (UTF-8 / UTF-16 / Latin-1) | Full content decoded |
| Binary | ASCII strings ≥ 4 chars extracted; optionally sliding-window hex tokens |

Hex token format for binary: `DEADBEEF`, `CAFEBABE` (4-byte chunks, overlapping windows).

### 4.3 Content Hash — Three-tier Shortcut

On every incremental build, each file goes through the fastest applicable shortcut:

```
1. Git blob SHA1  (tracked files only)
   ├─ Match stored blob_sha → SKIP  (zero file reads)
   └─ No match → continue

2. mtime check  (untracked files / non-git repos)
   ├─ mtime unchanged → SKIP
   └─ Changed → read file

3. BLAKE2b content hash
   ├─ Hash unchanged → SKIP  (update mtime only)
   └─ Hash changed   → re-index content
```

Blob SHA1 is the most powerful shortcut: after a branch switch where file bytes are identical, the entire file read is eliminated.

### 4.4 Tiered Index — Minor DB Keyed by SHA

The minor DB filename embeds the branch HEAD SHA:

```
minor_<first7hex>.db   e.g.  minor_a3f8c12.db
```

This means:
- Two branches at the same commit automatically share one minor DB
- Staleness is implicit — if HEAD moves, the old minor DB no longer matches; the engine detects this and warns you at search time
- No branch-name sanitization needed

### 4.5 Threading Model

```
Main thread
  │
  ├─ git ls-files / rglob     →  file list
  ├─ git ls-files -s          →  blob SHA1 map  {path: sha1}
  │
  └─ ThreadPoolExecutor  (N workers)
       │
       │  each worker:
       │    read file + hash + extract content
       │    never touches SQLite
       │
       └─▶  write_queue  ──▶  writer thread (single)
                                   │
                                   └─  SQLite INSERT / UPDATE  (serialized)
```

`DEFAULT_WORKERS = min(32, cpu_count × 2)`

---

## 5. Indexing Flow

```
build_main / build_minor
        │
        ▼
  git ls-files ──────────────────── file list (respects .gitignore)
        │
        ▼
  git ls-files -s ────────────────── blob SHA1 map  {path: sha1}
        │
        ▼
  For each file:
    ┌─ blob SHA1 matches cached? ──YES──▶ touch  (zero file read)
    │
    NO
    ├─ mtime unchanged (untracked)? ──YES──▶ skip
    │
    NO
    ├─ read file bytes
    ├─ BLAKE2b hash matches cached? ──YES──▶ touch
    │
    NO
    ├─ detect text / binary
    ├─ extract content
    │     text  → decode (UTF-8 / UTF-16 / Latin-1)
    │     bin   → ASCII strings + (optional) hex tokens
    └─▶  write queue  ──▶  SQLite INSERT OR REPLACE
                │
                ▼
          store: path, mtime, hash, blob_sha, is_text, ext, hex_indexed, content

  Finalize:
    store last_commit SHA + build_time in index_meta
    print summary box
```

**build_minor additionally:**
```
  git diff --name-status <base_sha> <branch_head>
        │
        ├─ A / M files  →  index into minor DB
        └─ D / R files  →  insert into tombstones table

  store: base_commit, branch_head, branch_name in minor index_meta
```

---

## 6. Search Flow

```
Search command  (any of: search / prefix / substr / fuzzy / hex / ext)
        │
        ▼
_get_active_conns()
        │
        ├─ minor_<HEAD_sha7>.db exists?
        │       YES ──▶  TIERED mode
        │                 primary   = minor DB
        │                 secondary = main DB
        │                 tombstones = set from minor.tombstones
        │
        ├─ minor DB exists but SHA mismatch?
        │       YES ──▶  STALE MINOR warning + MAIN ONLY fallback
        │
        ├─ no minor DB, main_index.db exists?
        │       YES ──▶  MAIN ONLY  (warn: N commits ahead of index)
        │
        └─ only file_index.db?
                YES ──▶  LEGACY mode
        │
        ▼
  FTS5 MATCH query on primary DB
        │
        ├─ secondary DB?
        │     YES ──▶  FTS5 MATCH on secondary
        │              merge results, deduplicate, filter tombstones
        ▼
  _apply_search_filters()
        │  --ext    → keep only matching extensions
        │  --path   → keep only paths containing substring  (OR)
        │  --exclude → drop paths matching fnmatch glob
        │  (--grep handled per-line below)
        ▼
  For each matched file:
        │
        ├─ text file  ──▶  _find_matching_lines()
        │                    scan file for terms + grep_filter
        │                    print:  path(lineno) :line content
        │
        └─ binary file ──▶  print:  [BIN] path
```

---

## 7. Build Commands

### Entry Points

| Script | Purpose |
|---|---|
| `Build_Index_cli.py` | Build-only CLI |
| `kvhsearch_core.py` | Combined (build + search) |
| `build_index.py` | Shim — delegates to `kvhsearch_core.py` |

---

### `build_main` — Full Base Index

```powershell
python Build_Index_cli.py build_main <dir> [--workers N] [--hex] [--db-dir <path>] [--label <ver>]
```

Indexes the entire working tree into `main_<label>.db`. Incremental on re-runs.

| Parameter | Default | Description |
|---|---|---|
| `<dir>` | required | Root directory to index |
| `--workers N` | `cpu_count × 2` | Number of parallel worker threads |
| `--hex` | off | Also index binary files as hex tokens |
| `--db-dir <path>` | CWD | Directory to store DB files |
| `--label <ver>` | _(none)_ | Version label — produces `main_<ver>.db` / `minor_<ver>_*.db` |

**Examples:**
```powershell
python Build_Index_cli.py build_main .
python Build_Index_cli.py build_main E:\Git\Cathaya --workers 16 --hex --label v08
python Build_Index_cli.py build_main . --db-dir D:\indexes\Cathaya --label v08
```

---

### `build_minor` — Branch Delta Index

```powershell
python Build_Index_cli.py build_minor <dir> [--workers N] [--hex] [--db-dir <path>] [--label <ver>]
```

Requires `main_<label>.db`. Diffs current branch HEAD vs. base commit. Outputs `minor_<label>_<sha7>.db`.

**Examples:**
```powershell
git checkout feature/oem-dxe
python Build_Index_cli.py build_minor . --label v08
python Build_Index_cli.py build_minor . --db-dir D:\indexes\Cathaya --label v08
```

---

### `build_chain` — All-Branch Minor Sweep

```powershell
python Build_Index_cli.py build_chain <dir> [--workers N] [--hex] [--db-dir <path>] [--label <ver>]
```

Enumerates local branches sorted newest-first, auto checkouts → `build_minor` for each branch ahead of the main base commit, then restores the original branch. Stashes uncommitted changes automatically.

Shows a preview and prompts `y/N` before executing.

---

### `build_history` — Per-Commit Minor Indexes

```powershell
python Build_Index_cli.py build_history <dir> [--workers N] [--hex] [--db-dir <path>] [--label <ver>]
```

Walks `--first-parent` linear history from HEAD back to the main base commit. For each commit, checks out in detached HEAD → builds a minor index → restores. Shows commit subjects in preview and prompts `y/N`.

---

### `build_promote` — Merge Minor into New Main

```powershell
python Build_Index_cli.py build_promote --from <ver> --minor <sha7> --to <new_ver> [--db-dir <path>]
```

Creates a new `main_<new_ver>.db` by copying `main_<from_ver>.db`, then applying the changes from `minor_<from_ver>_<sha7>.db` (upsert modified files, remove tombstoned files). Runs `VACUUM` to reclaim space.

**Example:**
```powershell
python Build_Index_cli.py build_promote --from v07 --minor 42ea613 --to v08
python Build_Index_cli.py build_promote --from v07 --minor 42ea613 --to v08 --db-dir D:\indexes\Cathaya
```

---

### `build` — Legacy Single Index

```powershell
python Build_Index_cli.py build <dir> [--workers N] [--hex]
```

Writes to `file_index.db`. No tiered architecture.

---

### `--hex` Flag

Indexes binary files as overlapping 4-byte hex token windows. Required for `hex` search. Increases index size ~20–40% for firmware repos.

---

## 8. Search Commands

**Entry point:** `Search_cli.py`  
All commands auto-detect TIERED / MAIN ONLY / LEGACY mode.

---

### `search` — Exact AND

```powershell
python Search_cli.py search <kw1> [kw2 ...] [--txt|--bin] [--no-sort] [--db-dir <path>]
```

All keywords must appear in the file. Case-insensitive.

```powershell
python Search_cli.py search OemDxe
python Search_cli.py search OemDxe EFI_STATUS      # AND — both must appear
python Search_cli.py search PeiCore --txt          # text files only
python Search_cli.py search GUID --bin             # binary files only
python Search_cli.py search OemDxe --db-dir D:\indexes\Cathaya
```

**Output:**
```
E:\Git\Cathaya\LenovoWsPkg\OemDxe\OemDxe.c(29) :// Name:    OemDxe.c
E:\Git\Cathaya\LenovoWsPkg\OemDxe\OemDxe.c(372) :CreateOemDxeEvent ()
  [BIN] E:\Git\Cathaya\LenovoWsPkg\OemDxe\OemDxe.efi
```

---

### `prefix` — Prefix Match

```powershell
python Search_cli.py prefix <prefix> [--txt|--bin] [--no-sort]
```

```powershell
python Search_cli.py prefix gEfi
python Search_cli.py prefix AMI_ --txt
```

---

### `substr` — Substring Match

```powershell
python Search_cli.py substr <fragment> [--txt|--bin] [--no-sort]
```

FTS5 trigram for fragments ≥ 3 chars; SQL `LIKE` for shorter.

```powershell
python Search_cli.py substr HandleEvent
python Search_cli.py substr Ev --bin
```

---

### `glob` — Wildcard Match

```powershell
python Search_cli.py glob <pattern> [--txt|--bin] [--no-sort]
```

Supports `*` (any sequence), `?` (single char), `[...]` (character class).  
Phase 1: FTS5 AND pre-filter on literal parts ≥ 3 chars.  
Phase 2: per-line regex verify.

```powershell
python Search_cli.py glob "Oem*Dxe"
python Search_cli.py glob "PcdGet[0-9][0-9]" --ext .c .h
python Search_cli.py glob "gEfi*Protocol" --path LenovoWsPkg
```

---

### `fuzzy` — Typo-Tolerant

```powershell
python Search_cli.py fuzzy <keyword> [dist] [--txt|--bin] [--no-sort]
```

`dist` = max edit distance. Default `1`.  
At dist=1: expands **deletions + substitutions** across `_FUZZY_ALPHABET` (`a-z0-9_`).  
At dist≥2: adds **Pigeonhole** part-split pre-filter (guarantees ≥1 verbatim part in FTS5).  
Phase 2: per-token Levenshtein verify on each matched line.

Supports **compound C expressions** — `A->B`, `A.B`, `A(B)` — structural order is enforced per line.

```powershell
python Search_cli.py fuzzy inicialize             # matches "initialize" (substitution)
python Search_cli.py fuzzy inicialize 2
python Search_cli.py fuzzy "gProtocol->IsSupported" 1
python Search_cli.py fuzzy "PcdGet32(PcdDebugLevel)" 1
```

---

### `hex` — Binary Byte-Pattern Search

```powershell
python Search_cli.py hex <pattern> [pattern2 ...] [--no-sort]
```

Requires index built with `--hex`. Multiple patterns = AND logic.

```powershell
python Search_cli.py hex DEADBEEF
python Search_cli.py hex DE:AD:BE:EF
python Search_cli.py hex 0xDEADBEEF
python Search_cli.py hex DEADBEEF CAFEBABE   # both must appear in same file
```

---

### `ext` — Extension / Profile Filter

```powershell
python Search_cli.py ext <profile|.ext> [...] [--no-sort]
python Search_cli.py ext --list
```

```powershell
python Search_cli.py ext c               # profile → .c .cpp .h .hpp
python Search_cli.py ext sdl             # profile → .sdl .ssp
python Search_cli.py ext c .py           # mix profile + raw extension
python Search_cli.py ext .inf .dec
python Search_cli.py ext all             # every indexed file
python Search_cli.py ext --list          # show profiles + config path
```

---

### Common Search Flags

| Flag | Effect |
|---|---|
| `--txt` | Text files only |
| `--bin` | Binary files only |
| `--no-sort` | Skip alphabetic sort (faster for large results) |
| `--db-dir <path>` | Read DBs from specified directory |

---

## 9. Search Filters

Applied after the FTS5 query. All combinable.

| Flag | Args | Logic | Description |
|---|---|---|---|
| `--ext` | `.c .h ...` | OR | Keep only these file extensions |
| `--path` | `substr ...` | OR | Keep only paths containing substring |
| `--exclude` | `glob ...` | OR | Drop paths matching fnmatch glob |
| `--grep` | `term ...` | AND | Show only lines also containing all terms |

**Examples:**
```powershell
# Extension filter
python Search_cli.py search OemDxe --ext .c .h

# Path inclusion
python Search_cli.py search OemDxe --path LenovoWsPkg

# Exclude files
python Search_cli.py search OemDxe --exclude *.inf *.sdl *.cif

# Line-level grep
python Search_cli.py search OemDxe --grep EFI_STATUS

# Combine all four
python Search_cli.py search OemDxe --ext .c --path LenovoWsPkg --exclude *Test* --grep TRACE

# Substring + path + grep
python Search_cli.py substr OemDxe --path OemDxe --grep EFI_STATUS

# Prefix + extension + exclude
python Search_cli.py prefix Oem --ext .h --exclude *PdaAccess*
```

---

## 10. Management Commands

### `status` — Dashboard

```powershell
python Build_Index_cli.py status [<dir>] [--db-dir <path>] [--repo-dir <path>]
```

Shows all `main_*.db` and `minor_*.db` files with size, file count, commit, staleness, and active search mode. The currently selected version is marked with `[*]`.

| Active Mode | Meaning |
|---|---|
| `TIERED` | `minor_*_<sha7>.db` matches current HEAD — full accuracy |
| `STALE MINOR` | Minor DB exists but HEAD has moved — run `build_minor` |
| `MAIN ONLY` | No minor DB for this branch — branch changes invisible to search |
| `LEGACY` | Only `file_index.db` present |
| `NO INDEX FOUND` | No DB files found |

---

### `cleanup` — Remove Orphaned Indexes

```powershell
python Build_Index_cli.py cleanup [<dir>] [--db-dir <path>] [--repo-dir <path>]
```

Deletes `minor_*.db` files whose stored `branch_head` SHA no longer exists in git.

---

## 11. Multi-Version Indexes

The `--label` flag enables named versions of the index, allowing you to keep multiple snapshots side by side.

### Naming Convention

| `--label` | Main DB | Minor DB |
|---|---|---|
| `v07` | `main_v07.db` | `minor_v07_<sha7>.db` |
| `v08` | `main_v08.db` | `minor_v08_<sha7>.db` |
| _(none)_ | `main_index.db` | `minor_<sha7>.db` |

### Typical Version Workflow

```powershell
# 1. Build main at v07 tag
git checkout v07
python Build_Index_cli.py build_main . --label v07 --db-dir D:\indexes\Cathaya

# 2. Build per-commit minors on v07 history
python Build_Index_cli.py build_history . --label v07 --db-dir D:\indexes\Cathaya

# 3. Promote a specific minor into v08
python Build_Index_cli.py build_promote --from v07 --minor 42ea613 --to v08 --db-dir D:\indexes\Cathaya

# 4. Check all versions
python Build_Index_cli.py status --db-dir D:\indexes\Cathaya
```

### Cross-Label Minor Detection

When searching, the engine looks for **any** `minor_*_<sha7>.db` matching the current HEAD, regardless of label. This means a minor built against v07 will still be found and used for tiered search even when v08 is selected.

---

## 12. GUI (KVHSearch_Gui)

`KVHSearch_Gui.py` provides a dark-themed tkinter GUI with all search and management features.

### Launch

```powershell
python KVHSearch_Gui.py [--db-dir <path>] [--repo-dir <path>]
```

Or use the packed EXE from `_pack_KVHSearch_Gui.bat`.

### Features

- **Search bar** with mode selector (search / prefix / substr / glob / fuzzy / hex / ext)
- **Filters**: File types, `--path`, `--exclude`, `--grep`
- **Version dropdown** — selects which `main_<label>.db` to search; refreshable with `⟳`
- **Config menu** — set IndexDB path, File types, Sort, Debug mode toggle
- **Manage DB menu**:
  - List Main / List Minor — show index status
  - Build Main / Build Branch / Build SHA1 / Build Chain / Build History
  - Promote — merge minor into new main version
  - Delete DB — checkbox dialog to remove selected DB files
- **Settings** saved to `kvhsearch_gui.json` next to the EXE (db_dir, ext, path, exclude, grep)
- **Tiered search** with automatic minor detection and stale warnings

---

## 13. Skip Rules

Configured in `build_index_profiles.json` under `"skip"`. Applied at index time.

| Rule | Default |
|---|---|
| `max_file_mb` | `64` |
| `folders` | `.git`, `build`, `Flash_Image_Tool`, `__pycache__`, `.vs`, `.vscode`, `node_modules` |
| `extensions` | `.exe .dll .lib .obj .pdb .zip .7z .bin .fd .rom .efi .pdf .chm .bmp .jpg .png .gif .ttf` ... (34 types) |
| `filenames` | `system.sys` |
| `patterns` (fnmatch) | `RomAlignment.*`, `iasl*.*`, `BiosGuardCryptoCon*.*`, `fit.*`, `cryptocon.*`, `openssl.*` ... |

Edit and re-run `build_main` to apply changes.

---

## 14. Extension Profiles

Stored in `build_index_profiles.json`. Auto-created with defaults on first run.

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

Use `python Search_cli.py ext --list` to see active profiles and config file path.

---

## 15. `--db-dir`: External Index Storage

Store DB files outside the repo — useful for:
- Keeping the working tree clean (no `.db` files committed)
- Sharing one index directory for multiple checkouts of the same repo
- Storing indexes on a faster drive

```powershell
# Build into external directory
python Build_Index_cli.py build_main  E:\Git\Cathaya --db-dir D:\indexes\Cathaya
python Build_Index_cli.py build_minor E:\Git\Cathaya --db-dir D:\indexes\Cathaya

# Search using external directory
python Search_cli.py search OemDxe    --db-dir D:\indexes\Cathaya
python Build_Index_cli.py status      --db-dir D:\indexes\Cathaya
python Build_Index_cli.py cleanup     --db-dir D:\indexes\Cathaya
```

The directory is created automatically if it does not exist.

---

## 16. Git Hooks

Auto-run `build_minor` after every branch switch or pull:

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

## 17. Typical Daily Workflow

```powershell
# ── First-time setup (run on main branch) ────────────────────────────────────
cd E:\Git\Cathaya
python E:\Git\repository\KVHSearch\Build_Index_cli.py build_main . --label v08

# ── Switch to a feature branch ───────────────────────────────────────────────
git checkout feature/oem-dxe
python E:\Git\repository\KVHSearch\Build_Index_cli.py build_minor . --label v08

# ── Check index state ────────────────────────────────────────────────────────
python E:\Git\repository\KVHSearch\Build_Index_cli.py status

# ── Search ───────────────────────────────────────────────────────────────────
python E:\Git\repository\KVHSearch\Search_cli.py search OemDxe
python E:\Git\repository\KVHSearch\Search_cli.py search OemDxe --ext .c .h
python E:\Git\repository\KVHSearch\Search_cli.py search OemDxe --path LenovoWsPkg --grep EFI_STATUS
python E:\Git\repository\KVHSearch\Search_cli.py prefix gBS_
python E:\Git\repository\KVHSearch\Search_cli.py ext c
python E:\Git\repository\KVHSearch\Search_cli.py fuzzy inicialize

# ── After new commits on the branch ──────────────────────────────────────────
git commit -am "fix: ..."
python E:\Git\repository\KVHSearch\Build_Index_cli.py build_minor . --label v08   # seconds

# ── After main is updated ─────────────────────────────────────────────────────
git checkout main
git pull
python E:\Git\repository\KVHSearch\Build_Index_cli.py build_main . --label v09   # new version

# ── Housekeeping ──────────────────────────────────────────────────────────────
python E:\Git\repository\KVHSearch\Build_Index_cli.py cleanup
```

---

## 18. Performance Notes

| Operation | Typical time |
|---|---|
| `build_main` first run (30 000 files) | 30–90 s |
| `build_main` incremental (few new commits) | 1–5 s |
| `build_minor` (normal) | 1–10 s |
| `build_minor` after branch switch, no file changes | < 1 s (blob SHA1 shortcut) |
| Any search command | < 10 ms |

### Minor DB Count vs Search Overhead

Only **one** minor DB is used per search (the one matching current HEAD).
However, when no exact match exists (stale / no minor for this branch),
`_get_active_conns()` globs all `minor_*.db` and reads each to find a stale
match. This stale-detection cost grows linearly with minor count:

| Minor DBs | `glob` (ms) | `find` (ms) | `glob + read all` (ms) |
|----------:|------------:|------------:|-----------------------:|
| 9         | 0.04        | 0.03        | 28                     |
| 10        | 0.19        | 0.08        | 33                     |
| 50        | 0.37        | 0.28        | 33                     |
| 100       | 0.78        | 0.69        | 39                     |
| 200       | 0.35        | 0.20        | 51                     |
| 326       | 0.49        | 0.28        | 59                     |

> Measured on NVMe SSD, 30 000-file repo (E:\Git\Cathaya). Each minor DB ≈ 48 KB.

**Columns explained:**

- **glob** — `Path.glob("minor_*.db")` only (directory listing)
- **find** — `Path.glob("minor_*_<sha7>.db")` (targeted, used in happy path)
- **glob + read all** — glob + open every minor DB to read `branch_name` (worst-case stale detection)

**Key takeaway:**

| Path | When | Cost |
|---|---|---|
| Happy (minor found) | HEAD matches a `minor_*_<sha7>.db` | **< 0.5 ms** regardless of minor count |
| Stale (no match) | No minor for current HEAD | **~30 ms + 0.1 ms per minor** |

**Recommendation:** Run `cleanup` periodically to remove orphaned minors. Keeping minor count under ~50 keeps stale-detection overhead negligible (< 35 ms).
