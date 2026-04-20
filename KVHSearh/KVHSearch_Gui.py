#!/usr/bin/env python3
"""
KVHSearch_Gui.py — Tkinter GUI front-end for KVHSearch
"""
import ctypes
import builtins
import os
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path

# ── App metadata ───────────────────────────────────────────────────────────────
_APP_NAME      = "KVHSearch GUI"
_APP_VERSION   = "1.0.0"
_APP_COPYRIGHT = "© 2026 KVH"

# ── Taskbar icon fix ──────────────────────────────────────────────────────────
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "KVH.KVHSearchGui.1.0.0"
    )
except Exception:
    pass

# ── Paths ──────────────────────────────────────────────────────────────────────
import json
_HERE = (Path(sys._MEIPASS) if getattr(sys, "frozen", False)
         else Path(__file__).parent)
_ICO_PATH = (_HERE / "Rewolf.ico" if (_HERE / "Rewolf.ico").exists()
             else _HERE.parent / "Rewolf.ico")
_SETTINGS_PATH = (Path(sys.executable).parent / "kvhsearch_gui.json"
                  if getattr(sys, "frozen", False)
                  else Path(__file__).parent / "kvhsearch_gui.json")


def _load_settings() -> dict:
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_settings(data: dict) -> None:
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

# ── Import engine ─────────────────────────────────────────────────────────────
sys.path.insert(0, str(_HERE))
import kvhsearch_core as _eng
from kvhsearch_core import (
    search_exact, search_prefix, search_substr,
    search_fuzzy, search_glob, search_hex, search_ext, list_ext_profiles,
    list_main_indexes,
    build_main, build_minor, build_chain, build_history, build_promote,
    status_indexes,
    EXT_PROFILES,
)

# ── Dark-theme palette ────────────────────────────────────────────────────────
BG       = "#2b2b2b"
BG_ENTRY = "#1e1e1e"
BG_BTN   = "#3a3a3a"
BG_BTN_H = "#4a4a4a"
FG       = "#d4d4d4"
FG_DIM   = "#888888"
CYAN     = "#56d1ff"
GOLD     = "#f0c040"
GREEN    = "#4ec94e"
RED      = "#e05050"
ORANGE   = "#e09040"
FONT     = ("Consolas", 10)
FONT_UI  = ("Segoe UI", 9)
FONT_LBL = ("Segoe UI", 9, "bold")

# Sentinel object posted to the output queue to signal search completion.
_SEARCH_DONE = object()

# Save the real print() before any patching.
_REAL_PRINT = builtins.print
_ANSI_PAT   = re.compile(r"\x1b\[[0-9;]*m")


def _make_gui_print(out_queue: "queue.Queue"):
    """Return a print() that queues cleaned text for the GUI widget.
    Also mirrors to the real console (best-effort, silently ignored in noconsole exe)."""
    def _print(*args, sep=" ", end="\n", file=None, flush=False):
        # Mirror to real console (always flush so output appears immediately)
        try:
            _REAL_PRINT(*args, sep=sep, end=end, file=file, flush=True)
        except Exception:
            pass
        # Only capture stdout output (file=None means stdout)
        if file is None:
            text  = sep.join(str(a) for a in args) + end
            clean = _ANSI_PAT.sub("", text)
            if clean:
                out_queue.put(clean)
    return _print


# ── About dialog ──────────────────────────────────────────────────────────────
class _AboutDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title(f"About {_APP_NAME}")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.wm_attributes("-topmost", True)
        self.grab_set()
        if _ICO_PATH.exists():
            try:
                self.iconbitmap(str(_ICO_PATH))
            except Exception:
                pass

        tk.Label(self, text=f"{_APP_NAME}  v{_APP_VERSION}",
                 font=("Segoe UI", 13, "bold"), bg=BG, fg=CYAN).pack(padx=30, pady=(20, 4))
        tk.Label(self, text=_APP_COPYRIGHT,
                 font=FONT_UI, bg=BG, fg=FG_DIM).pack(pady=(0, 16))
        tk.Button(self, text="Close", width=10, font=FONT_LBL,
                  bg=BG_BTN, fg=FG, activebackground=BG_BTN_H,
                  relief=tk.FLAT, cursor="hand2",
                  command=self.destroy).pack(pady=(0, 16))

        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{(sw-self.winfo_width())//2}+{(sh-self.winfo_height())//2}")


# ── Main application ───────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{_APP_NAME}  v{_APP_VERSION}")
        self.configure(bg=BG)
        self.minsize(800, 560)

        if _ICO_PATH.exists():
            try:
                self.iconbitmap(str(_ICO_PATH))
            except Exception:
                pass

        self._busy = False
        self._stop_event: threading.Event = threading.Event()
        self._out_queue: queue.Queue[str] = queue.Queue()
        self._settings = _load_settings()
        # Init vars needed by _build_menu before it is called
        self._var_debug = tk.BooleanVar(value=False)
        self._build_menu()
        self._build_ui()
        # Restore saved settings
        self._var_dbdir.set(self._settings.get("db_dir", ""))
        self._var_repodir.set(self._settings.get("repo_dir", ""))
        self._var_ext.set(self._settings.get("ext", "*.*"))
        self._var_path.set(self._settings.get("path", ""))
        self._var_exclude.set(self._settings.get("exclude", ""))
        self._var_grep.set(self._settings.get("grep", ""))
        # Parse command-line args (override saved settings)
        self._apply_argv()
        # Default repo dir to CWD (caller's directory) if not explicitly set
        if not self._var_repodir.get().strip():
            self._var_repodir.set(str(Path.cwd()))
        # Populate version dropdown from db_dir
        self._refresh_versions()
        self._drain_queue()   # start the main-thread output poll
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # Log startup info (only when debug mode is on)
        if self._var_debug.get():
            self._txt.configure(state=tk.NORMAL)
            self._txt.insert(tk.END, f"[INFO] exe : {sys.executable}\n")
            self._txt.insert(tk.END, f"[INFO] cwd : {Path.cwd()}\n")
            self._txt.insert(tk.END, f"[INFO] argv: {sys.argv}\n")
            self._txt.configure(state=tk.DISABLED)

        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h   = max(self.winfo_width(), 860), max(self.winfo_height(), 580)
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    # ── Menu ──────────────────────────────────────────────────────────────────
    def _build_menu(self):
        mb = tk.Menu(self, bg=BG, fg=FG, activebackground="#3c3c3c",
                     activeforeground=CYAN, relief=tk.FLAT, bd=0)
        self.config(menu=mb)

        # Config menu
        cfg_menu = tk.Menu(mb, tearoff=0, bg=BG, fg=FG,
                           activebackground="#3c3c3c", activeforeground=CYAN)
        mb.add_cascade(label="Config", menu=cfg_menu)
        cfg_menu.add_command(label="IndexDB Path…", command=self._config_db_path)
        cfg_menu.add_command(label="Repo Path…", command=self._config_repo_path)
        cfg_menu.add_separator()
        cfg_menu.add_checkbutton(label="Debug", variable=self._var_debug,
                                 command=self._on_debug_toggle)

        # Manage DB menu
        mgr_menu = tk.Menu(mb, tearoff=0, bg=BG, fg=FG,
                           activebackground="#3c3c3c", activeforeground=CYAN)
        mb.add_cascade(label="Manage DB", menu=mgr_menu)
        mgr_menu.add_command(label="List Main Indexes",   command=self._mgr_list_main)
        mgr_menu.add_command(label="List Minor Indexes",  command=self._mgr_list_minor)
        mgr_menu.add_separator()
        mgr_menu.add_command(label="Build Main…",         command=self._mgr_build_main)
        mgr_menu.add_command(label="Build Branch…",       command=self._mgr_build_branch)
        mgr_menu.add_command(label="Build SHA1…",         command=self._mgr_build_sha)
        mgr_menu.add_command(label="Build Chain…",        command=self._mgr_build_chain)
        mgr_menu.add_command(label="Build History…",      command=self._mgr_build_history)
        mgr_menu.add_separator()
        mgr_menu.add_command(label="Promote…",            command=self._mgr_promote)
        mgr_menu.add_separator()
        mgr_menu.add_command(label="Delete DB…",          command=self._mgr_delete)

        # Help menu
        hm = tk.Menu(mb, tearoff=0, bg=BG, fg=FG,
                     activebackground="#3c3c3c", activeforeground=CYAN)
        mb.add_cascade(label="Help", menu=hm)
        hm.add_command(label=f"About {_APP_NAME}…", command=lambda: _AboutDialog(self))

    def _config_db_path(self):
        """Open a dialog to set the IndexDB directory."""
        d = filedialog.askdirectory(
            title="Select IndexDB directory (folder containing main_index.db)",
            initialdir=self._var_dbdir.get() or None,
            parent=self,
        )
        if d:
            self._var_dbdir.set(str(Path(d).resolve()))
            self._save_all_settings()
            self._set_status(f"IndexDB path: {self._var_dbdir.get()}", CYAN)
            self._refresh_versions()

    def _refresh_versions(self):
        """Scan db_dir for main_*.db files and populate the Version combobox."""
        db_dir = self._var_dbdir.get().strip()
        if not db_dir or not Path(db_dir).is_dir():
            return
        labels = list_main_indexes(db_dir)
        if not labels:
            return
        self._cb_version.configure(values=labels)
        if self._var_version.get() not in labels:
            self._var_version.set(labels[0])   # default: newest
        self._on_version_change()

    def _on_version_change(self):
        """Update MAIN_DB_PATH / MINOR_DB_PREFIX when the version selection changes."""
        db_dir = self._var_dbdir.get().strip()
        ver    = self._var_version.get().strip()
        if not db_dir or not ver:
            return
        d = Path(db_dir)
        _eng.MAIN_DB_PATH    = str(d / f"main_{ver}.db")
        _eng.MINOR_DB_PREFIX = str(d / f"minor_{ver}_")

    def _config_repo_path(self):
        """Open a dialog to set the git repo directory (for branch detection)."""
        d = filedialog.askdirectory(
            title="Select git repo directory (where .git lives)",
            initialdir=self._var_repodir.get() or self._var_dbdir.get() or None,
            parent=self,
        )
        if d:
            self._var_repodir.set(str(Path(d).resolve()))
            self._save_all_settings()
            self._set_status(f"Repo path: {self._var_repodir.get()}", CYAN)

    def _on_debug_toggle(self):
        _eng.DEBUG_MODE = self._var_debug.get()

    def _apply_argv(self):
        """Parse --db-dir and --repo-dir from sys.argv and apply to StringVars.
        Skips values that look like unexpanded shell variables (e.g. %CD%)."""
        args = sys.argv[1:]
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("--db-dir", "--repo-dir") and i + 1 < len(args):
                raw = args[i + 1]
                # Skip unexpanded batch/shell variables
                if raw.startswith("%") or raw.startswith("$"):
                    i += 2
                    continue
                val = str(Path(raw).resolve())
                if a == "--db-dir":
                    self._var_dbdir.set(val)
                else:
                    self._var_repodir.set(val)
                i += 2
            else:
                i += 1

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Style combobox first ──────────────────────────────────────────────
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=BG_ENTRY, background=BG,
                        foreground=FG, arrowcolor=FG, bordercolor="#555",
                        selectbackground=BG_ENTRY, selectforeground=FG)
        style.map("TCombobox", fieldbackground=[("readonly", BG_ENTRY)],
                  foreground=[("readonly", FG)])

        LBL_W = 11   # uniform label width
        # Hidden vars (set via Config menu, not shown in form)
        self._var_dbdir   = tk.StringVar()
        self._var_repodir = tk.StringVar()
        # ── Form area ─────────────────────────────────────────────────────────
        form = tk.Frame(self, bg=BG)
        form.pack(fill=tk.X, padx=12, pady=(10, 0))
        form.columnconfigure(1, weight=1)   # fields stretch

        row = 0
        # ── Find ──────────────────────────────────────────────────────────────
        tk.Label(form, text="Find", width=LBL_W, anchor="w",
                 font=FONT_LBL, bg=BG, fg=FG_DIM).grid(row=row, column=0, sticky="w")
        find_frame = tk.Frame(form, bg=BG)
        find_frame.grid(row=row, column=1, sticky="ew", pady=2)
        find_frame.columnconfigure(0, weight=1)
        self._var_keyword = tk.StringVar()
        kw_entry = tk.Entry(find_frame, textvariable=self._var_keyword, font=FONT,
                            bg=BG_ENTRY, fg=FG, insertbackground=FG,
                            relief=tk.FLAT, bd=3)
        kw_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        kw_entry.bind("<Return>", lambda _e: self._run_search())
        self._btn_search = tk.Button(find_frame, text="Search", font=FONT_LBL,
                                     bg="#1e5f8e", fg=FG, activebackground="#2a7ab0",
                                     relief=tk.FLAT, cursor="hand2", width=8,
                                     command=self._run_search)
        self._btn_search.pack(side=tk.LEFT, padx=(6, 0))
        self._btn_stop = tk.Button(find_frame, text="Stop", font=FONT_LBL,
                                   bg="#8e1e1e", fg=FG, activebackground="#b02a2a",
                                   relief=tk.FLAT, cursor="hand2", width=6,
                                   command=self._stop_search, state=tk.DISABLED)
        self._btn_stop.pack(side=tk.LEFT, padx=(4, 0))

        # ── File types ────────────────────────────────────────────────────────
        row += 1
        tk.Label(form, text="File types", width=LBL_W, anchor="w",
                 font=FONT_LBL, bg=BG, fg=FG_DIM).grid(row=row, column=0, sticky="w")
        ft_frame = tk.Frame(form, bg=BG)
        ft_frame.grid(row=row, column=1, sticky="ew", pady=2)
        self._var_ext = tk.StringVar()
        # Build display values: profile name → semicolon-separated globs
        self._ext_display: list[str] = ["*.*"]
        for k in sorted(EXT_PROFILES):
            if k == "all":
                continue
            exts = EXT_PROFILES[k]
            if exts:
                self._ext_display.append(";".join(f"*{e}" for e in exts))
        _ext_cb = ttk.Combobox(ft_frame, textvariable=self._var_ext,
                               values=self._ext_display, font=FONT)
        _ext_cb.set("*.*")
        _ext_cb.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # ── Options row (checkboxes / radios) ─────────────────────────────────
        row += 1
        tk.Label(form, text=" ", width=LBL_W, bg=BG).grid(row=row, column=0)
        opts = tk.Frame(form, bg=BG)
        opts.grid(row=row, column=1, sticky="w", pady=(6, 0))

        # Left column
        self._var_scope = tk.StringVar(value="all")
        for val, lbl in (("all", "All files"), ("txt", "Text only"), ("bin", "Binary only")):
            tk.Radiobutton(opts, text=lbl, variable=self._var_scope, value=val,
                           font=FONT_UI, bg=BG, fg=FG, selectcolor=BG,
                           activebackground=BG, activeforeground=CYAN,
                           cursor="hand2").pack(side=tk.LEFT, padx=(0, 10))

        self._var_sort = tk.BooleanVar(value=True)
        tk.Checkbutton(opts, text="Sort results", variable=self._var_sort,
                       font=FONT_UI, bg=BG, fg=FG, selectcolor=BG,
                       activebackground=BG, activeforeground=CYAN,
                       cursor="hand2").pack(side=tk.LEFT, padx=(16, 0))

        # Mode dropdown
        self._var_mode = tk.StringVar(value="substr")
        tk.Label(opts, text="  Mode:", font=FONT_UI, bg=BG, fg=FG_DIM).pack(side=tk.LEFT, padx=(16, 2))
        ttk.Combobox(opts, textvariable=self._var_mode, state="readonly",
                     values=["search", "prefix", "substr", "glob", "fuzzy"],
                     width=8, font=FONT).pack(side=tk.LEFT)

        # ── Filter fields (grid) ─────────────────────────────────────────────
        row += 1
        tk.Label(form, text=" ", width=LBL_W, bg=BG).grid(row=row, column=0)
        flt = tk.Frame(form, bg=BG)
        flt.grid(row=row, column=1, sticky="ew", pady=(4, 0))
        flt.columnconfigure(1, weight=1)
        flt.columnconfigure(3, weight=1)

        tk.Label(flt, text="--path", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).grid(row=0, column=0, sticky="w", padx=(0, 4))
        self._var_path = tk.StringVar()
        tk.Entry(flt, textvariable=self._var_path, font=FONT, width=20,
                 bg=BG_ENTRY, fg=FG, insertbackground=FG,
                 relief=tk.FLAT, bd=3).grid(row=0, column=1, sticky="ew", padx=(0, 12))

        tk.Label(flt, text="--grep", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).grid(row=0, column=2, sticky="w", padx=(0, 4))
        self._var_grep = tk.StringVar()
        tk.Entry(flt, textvariable=self._var_grep, font=FONT, width=20,
                 bg=BG_ENTRY, fg=FG, insertbackground=FG,
                 relief=tk.FLAT, bd=3).grid(row=0, column=3, sticky="ew")

        row += 1
        tk.Label(form, text=" ", width=LBL_W, bg=BG).grid(row=row, column=0)
        flt2 = tk.Frame(form, bg=BG)
        flt2.grid(row=row, column=1, sticky="ew", pady=(2, 0))
        flt2.columnconfigure(1, weight=1)

        tk.Label(flt2, text="--exclude", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).grid(row=0, column=0, sticky="w", padx=(0, 4))
        self._var_exclude = tk.StringVar()
        tk.Entry(flt2, textvariable=self._var_exclude, font=FONT,
                 bg=BG_ENTRY, fg=FG, insertbackground=FG,
                 relief=tk.FLAT, bd=3).grid(row=0, column=1, sticky="ew")

        # ── Separator ─────────────────────────────────────────────────────────
        tk.Frame(self, bg="#444444", height=1).pack(fill=tk.X, padx=12, pady=6)

        # ── Toolbar: Clear + status label ──────────────────────────────────────
        tb = tk.Frame(self, bg=BG)
        tb.pack(fill=tk.X, padx=12)
        self._lbl_status = tk.Label(tb, text="Ready", font=FONT_UI,
                                    bg=BG, fg=FG_DIM, anchor="w")
        self._lbl_status.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(tb, text="Clear", font=FONT_UI,
                  bg=BG_BTN, fg=FG, activebackground=BG_BTN_H,
                  relief=tk.FLAT, cursor="hand2",
                  command=self._clear_results).pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(tb, text="⟳", font=FONT_UI,
                  bg=BG_BTN, fg=FG, activebackground=BG_BTN_H,
                  relief=tk.FLAT, cursor="hand2",
                  command=self._refresh_versions).pack(side=tk.RIGHT, padx=(2, 0))
        self._var_version = tk.StringVar()
        self._cb_version  = ttk.Combobox(tb, textvariable=self._var_version,
                                         state="readonly", width=10, font=FONT)
        self._cb_version.pack(side=tk.RIGHT, padx=(2, 0))
        self._cb_version.bind("<<ComboboxSelected>>", lambda _e: self._on_version_change())
        tk.Label(tb, text="Version:", font=FONT_UI, bg=BG, fg=FG_DIM
                 ).pack(side=tk.RIGHT, padx=(8, 2))

        # ── Results text area ──────────────────────────────────────────────────
        res_frame = tk.Frame(self, bg=BG)
        res_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 10))

        vsb = tk.Scrollbar(res_frame, orient=tk.VERTICAL)
        hsb = tk.Scrollbar(res_frame, orient=tk.HORIZONTAL)

        self._txt = tk.Text(res_frame, font=FONT, bg=BG_ENTRY, fg=FG,
                            insertbackground=FG, relief=tk.FLAT, bd=0,
                            wrap=tk.NONE, state=tk.DISABLED,
                            yscrollcommand=vsb.set, xscrollcommand=hsb.set,
                            selectbackground="#3c6aab", selectforeground=FG)
        vsb.config(command=self._txt.yview)
        hsb.config(command=self._txt.xview)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._txt.pack(fill=tk.BOTH, expand=True)

        # colour tags
        self._txt.tag_config("header",  foreground=CYAN)
        self._txt.tag_config("warn",    foreground=ORANGE)
        self._txt.tag_config("nodata",  foreground=FG_DIM)
        self._txt.tag_config("path",    foreground=GOLD)
        self._txt.tag_config("linenum", foreground="#a0a0a0")
    def _save_all_settings(self):
        self._settings["db_dir"]   = self._var_dbdir.get().strip()
        self._settings["repo_dir"] = self._var_repodir.get().strip()
        self._settings["ext"]      = self._var_ext.get().strip()
        self._settings["path"]     = self._var_path.get().strip()
        self._settings["exclude"]  = self._var_exclude.get().strip()
        self._settings["grep"]     = self._var_grep.get().strip()
        _save_settings(self._settings)

    def _on_close(self):
        self._save_all_settings()
        self.destroy()

    # ── Output queue poll (main thread only) ────────────────────────────────
    def _drain_queue(self):
        """Drain the output queue into the text widget; reschedules itself every 30 ms."""
        try:
            while True:
                item = self._out_queue.get_nowait()
                if item is _SEARCH_DONE:
                    # Clear the progress line from status bar on completion
                    self._search_done()
                    break
                self._txt.configure(state=tk.NORMAL)
                if isinstance(item, str) and item.startswith("\r"):
                    # Progress update — show in status bar, not in text widget
                    self._lbl_status.config(text=item.strip(), fg=CYAN)
                else:
                    self._txt.insert(tk.END, item)
                    self._txt.see(tk.END)
                self._txt.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.after(30, self._drain_queue)
    # ── Helpers ───────────────────────────────────────────────────────────────
    def _clear_results(self):
        self._txt.configure(state=tk.NORMAL)
        self._txt.delete("1.0", tk.END)
        self._txt.configure(state=tk.DISABLED)
        self._lbl_status.config(text="Ready", fg=FG_DIM)

    def _set_status(self, text: str, color: str = FG_DIM):
        self._lbl_status.config(text=text, fg=color)

    def _split_values(self, raw: str) -> list[str]:
        """Split a whitespace-separated filter string into tokens."""
        return raw.split() if raw.strip() else []

    # ── Search ────────────────────────────────────────────────────────────────
    def _run_search(self):
        if self._busy:
            return
        keyword = self._var_keyword.get().strip()
        mode    = self._var_mode.get()
        if not keyword and mode != "ext":
            self._set_status("Enter a keyword first.", RED)
            return

        # Apply --db-dir
        dbdir_raw = self._var_dbdir.get().strip()
        if dbdir_raw:
            db_dir = Path(dbdir_raw).resolve()
            ver    = self._var_version.get().strip()
            _eng.DB_PATH         = str(db_dir / "file_index.db")
            if ver:
                _eng.MAIN_DB_PATH    = str(db_dir / f"main_{ver}.db")
                _eng.MINOR_DB_PREFIX = str(db_dir / f"minor_{ver}_")
            else:
                _eng.MAIN_DB_PATH    = str(db_dir / "main_index.db")
                _eng.MINOR_DB_PREFIX = str(db_dir / "minor_")
        else:
            _eng.DB_PATH         = "file_index.db"
            _eng.MAIN_DB_PATH    = "main_index.db"
            _eng.MINOR_DB_PREFIX = "minor_"

        # Apply --repo-dir
        repodir_raw = self._var_repodir.get().strip()
        _eng.REPO_DIR = str(Path(repodir_raw).resolve()) if repodir_raw else None

        # Build ext filter from File types combobox
        ext_raw = self._var_ext.get().strip()
        ext_filter: list[str] = []
        if ext_raw and ext_raw != "*.*":
            # Check if it's a display string like "*.c;*.cpp;*.h;*.hpp"
            if ";" in ext_raw or ext_raw.startswith("*"):
                for part in ext_raw.split(";"):
                    part = part.strip().lstrip("*")
                    if part:
                        ext_filter.append(part.lower())
            else:
                # Profile name or raw extensions
                for e in ext_raw.split():
                    key = e.lstrip(".").lower()
                    if key in EXT_PROFILES and EXT_PROFILES[key]:
                        ext_filter.extend(EXT_PROFILES[key])
                    elif e.startswith("."):
                        ext_filter.append(e.lower())
                    else:
                        ext_filter.append(f".{e.lower()}")

        path_filter = self._split_values(self._var_path.get())    or None
        excl_filter = self._split_values(self._var_exclude.get()) or None
        grep_filter = self._split_values(self._var_grep.get())    or None
        sort        = self._var_sort.get()
        scope       = self._var_scope.get()

        # Inject our print() directly into the engine module's namespace.
        # LOAD_GLOBAL checks module __dict__ BEFORE builtins, so this is
        # the most reliable interception — immune to frozen-exe quirks.
        _gui_print = _make_gui_print(self._out_queue)
        _eng.print = _gui_print          # module-level shadow
        builtins.print = _gui_print       # belt-and-suspenders
        # Save all fields whenever a search is run
        self._save_all_settings()

        self._busy = True
        self._stop_event.clear()
        self._btn_search.configure(state=tk.DISABLED)
        self._btn_stop.configure(state=tk.NORMAL)
        self._set_status("Searching…", CYAN)

        def _worker():
            try:
                sf = dict(
                    ext_filter  = ext_filter or None,
                    path_filter = path_filter,
                    excl_filter = excl_filter,
                    grep_filter = grep_filter,
                )
                if mode == "search":
                    # If the input is wrapped in quotes, treat as an exact phrase
                    if keyword.startswith('"') and keyword.endswith('"') and len(keyword) > 2:
                        search_substr(keyword[1:-1], sort=sort, scope=scope, **sf)
                    else:
                        keywords = keyword.split()
                        search_exact(keywords, sort=sort, scope=scope, **sf)
                elif mode == "prefix":
                    search_prefix(keyword, sort=sort, scope=scope, **sf)
                elif mode == "substr":
                    search_substr(keyword, sort=sort, scope=scope, **sf)
                elif mode == "glob":
                    search_glob(keyword, sort=sort, scope=scope, **sf)
                elif mode == "fuzzy":
                    parts = keyword.split()
                    kw    = parts[0]
                    dist  = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 1
                    def _progress_cb(done: int, total: int):
                        pct = int(done * 100 / total) if total else 100
                        self._out_queue.put(f"\r[Fuzzy] 掃描中… {done}/{total}  ({pct}%)")
                    search_fuzzy(kw, dist, sort=sort, scope=scope, **sf,
                                 _stop_event=self._stop_event,
                                 _progress_cb=_progress_cb)
                elif mode == "hex":
                    search_hex(keyword.split(), sort=sort, **sf)
                elif mode == "ext":
                    if keyword == "--list" or not keyword:
                        list_ext_profiles()
                    else:
                        search_ext(keyword.split(), sort=sort, **sf)
            except Exception as exc:
                self._out_queue.put(f"[ERROR] {exc}\n")
            finally:
                # Restore original print in both locations
                try:
                    del _eng.print
                except AttributeError:
                    pass
                builtins.print = _REAL_PRINT
                self._out_queue.put(_SEARCH_DONE)   # signal main thread

        threading.Thread(target=_worker, daemon=True).start()

    def _search_done(self):
        self._busy = False
        self._btn_search.configure(state=tk.NORMAL)
        self._btn_stop.configure(state=tk.DISABLED)
        self._set_status("Done.", GREEN)

    def _stop_search(self):
        self._stop_event.set()
        self._btn_stop.configure(state=tk.DISABLED)
        self._set_status("Stopping…", ORANGE)

    # ── Background runner (Manage DB operations) ─────────────────────────────
    def _run_bg(self, label: str, fn, *args, **kwargs):
        """Run *fn* in a background thread with output captured to the text widget."""
        if self._busy:
            self._set_status("Busy — wait for current operation to finish.", RED)
            return
        _gui_print = _make_gui_print(self._out_queue)
        _eng.print = _gui_print
        builtins.print = _gui_print
        self._busy = True
        self._btn_search.configure(state=tk.DISABLED)
        self._set_status(label, CYAN)

        def _worker():
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                self._out_queue.put(f"[ERROR] {exc}\n")
            finally:
                try:
                    del _eng.print
                except AttributeError:
                    pass
                builtins.print = _REAL_PRINT
                self._out_queue.put(_SEARCH_DONE)

        threading.Thread(target=_worker, daemon=True).start()

    def _db_dir(self) -> str | None:
        """Return the configured db_dir or show an error."""
        d = self._var_dbdir.get().strip()
        if not d:
            self._set_status("Set IndexDB Path first (Config menu).", RED)
            return None
        return d

    def _repo_dir(self) -> str:
        return self._var_repodir.get().strip() or str(Path.cwd())

    def _apply_version_paths(self):
        """Set _eng.MAIN_DB_PATH / MINOR_DB_PREFIX from current db_dir + version."""
        db_dir = self._var_dbdir.get().strip()
        ver    = self._var_version.get().strip()
        if db_dir and ver:
            d = Path(db_dir)
            _eng.MAIN_DB_PATH    = str(d / f"main_{ver}.db")
            _eng.MINOR_DB_PREFIX = str(d / f"minor_{ver}_")

    # ── Manage DB — list ──────────────────────────────────────────────────────
    def _mgr_list_main(self):
        db_dir = self._db_dir()
        if not db_dir:
            return
        self._apply_version_paths()
        self._run_bg("Listing main indexes…", status_indexes, db_dir)

    def _mgr_list_minor(self):
        db_dir = self._db_dir()
        if not db_dir:
            return
        self._apply_version_paths()
        self._run_bg("Listing minor indexes…", self._do_list_minor, db_dir)

    def _do_list_minor(self, db_dir: str):
        minors = sorted(Path(db_dir).glob("minor_*.db"))
        if not minors:
            print("No minor indexes found.")
            return
        print(f"Minor indexes in {db_dir}:\n")
        for f in minors:
            sz = f.stat().st_size // 1024
            print(f"  {f.name:<50}  {sz:>6} KB")
        print(f"\nTotal: {len(minors)}")

    # ── Manage DB — build ─────────────────────────────────────────────────────
    def _mgr_build_main(self):
        db_dir = self._db_dir()
        if not db_dir:
            return
        label = simpledialog.askstring("Build Main", "Version label (e.g. v09):", parent=self)
        if not label or not label.strip():
            return
        label = label.strip()
        d = Path(db_dir)
        _eng.MAIN_DB_PATH    = str(d / f"main_{label}.db")
        _eng.MINOR_DB_PREFIX = str(d / f"minor_{label}_")
        repo = self._repo_dir()
        self._run_bg(f"Building main_{label}.db…", build_main, repo)

    def _mgr_build_branch(self):
        db_dir = self._db_dir()
        if not db_dir:
            return
        branch = simpledialog.askstring("Build Branch Minor",
                                        "Branch name to build minor for:", parent=self)
        if not branch:
            return
        self._apply_version_paths()
        repo = self._repo_dir()
        def _do():
            import subprocess as sp
            from pathlib import Path as _P
            ok = bool(sp.run(["git", "checkout", branch], cwd=repo,
                             stdout=sp.DEVNULL, stderr=sp.DEVNULL).returncode == 0)
            if not ok:
                print(f"[ERROR] Cannot checkout branch '{branch}'")
                return
            build_minor(repo)
            sp.run(["git", "checkout", "-"], cwd=repo,
                   stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        self._run_bg(f"Building minor for branch {branch}…", _do)

    def _mgr_build_sha(self):
        db_dir = self._db_dir()
        if not db_dir:
            return
        sha = simpledialog.askstring("Build SHA1 Minor",
                                     "SHA1 (or prefix) to build minor for:", parent=self)
        if not sha:
            return
        self._apply_version_paths()
        repo = self._repo_dir()
        def _do():
            import subprocess as sp
            ok = bool(sp.run(["git", "checkout", sha], cwd=repo,
                             stdout=sp.DEVNULL, stderr=sp.DEVNULL).returncode == 0)
            if not ok:
                print(f"[ERROR] Cannot checkout '{sha}'")
                return
            build_minor(repo, _branch_override=sha[:12])
            sp.run(["git", "checkout", "-"], cwd=repo,
                   stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        self._run_bg(f"Building minor for {sha[:12]}…", _do)

    def _mgr_build_chain(self):
        db_dir = self._db_dir()
        if not db_dir:
            return
        self._apply_version_paths()
        repo = self._repo_dir()
        self._run_bg("Running build_chain…", build_chain, repo)

    def _mgr_build_history(self):
        db_dir = self._db_dir()
        if not db_dir:
            return
        self._apply_version_paths()
        repo = self._repo_dir()
        self._run_bg("Running build_history…", build_history, repo)

    # ── Manage DB — promote ───────────────────────────────────────────────────
    def _mgr_promote(self):
        db_dir = self._db_dir()
        if not db_dir:
            return
        from_label = simpledialog.askstring("Promote — From",
                                            "From version label (e.g. v07):", parent=self)
        if not from_label or not from_label.strip():
            return
        from_label = from_label.strip()
        minor_sha = simpledialog.askstring("Promote — Minor SHA",
                                           "Minor SHA1 (7+ chars):", parent=self)
        if not minor_sha or len(minor_sha.strip()) < 7:
            return
        minor_sha = minor_sha.strip()
        to_label = simpledialog.askstring("Promote — To",
                                          "New version label (e.g. v08):", parent=self)
        if not to_label or not to_label.strip():
            return
        to_label = to_label.strip()
        # Set db dir context for build_promote
        d = Path(db_dir)
        _eng.MAIN_DB_PATH    = str(d / f"main_{from_label}.db")
        _eng.MINOR_DB_PREFIX = str(d / f"minor_{from_label}_")
        self._run_bg(f"Promoting {from_label}+{minor_sha[:7]} → {to_label}…",
                     build_promote, from_label, minor_sha, to_label)

    # ── Manage DB — delete ────────────────────────────────────────────────────
    def _mgr_delete(self):
        db_dir = self._db_dir()
        if not db_dir:
            return
        dbs = sorted(Path(db_dir).glob("*.db"))
        if not dbs:
            self._set_status("No DB files found.", RED)
            return
        _DeleteDbDialog(self, dbs)


class _DeleteDbDialog(tk.Toplevel):
    """Checkbox list for deleting DB files."""
    def __init__(self, parent, dbs: list):
        super().__init__(parent)
        self.title("Delete DB files")
        self.configure(bg=BG)
        self.resizable(False, False)
        self._dbs  = dbs
        self._vars = [tk.BooleanVar(value=False) for _ in dbs]

        tk.Label(self, text="Select files to delete:", font=FONT_LBL,
                 bg=BG, fg=FG).pack(anchor="w", padx=12, pady=(10, 4))

        frame = tk.Frame(self, bg=BG)
        frame.pack(fill=tk.BOTH, padx=12)
        for db, var in zip(dbs, self._vars):
            sz = db.stat().st_size // 1024
            tk.Checkbutton(frame, text=f"{db.name}  ({sz} KB)",
                           variable=var, font=FONT_UI,
                           bg=BG, fg=FG, selectcolor=BG,
                           activebackground=BG, activeforeground=CYAN
                           ).pack(anchor="w")

        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(fill=tk.X, padx=12, pady=10)
        tk.Button(btn_frame, text="Delete Selected", font=FONT_LBL,
                  bg="#8e1e1e", fg=FG, activebackground="#b02828",
                  relief=tk.FLAT, cursor="hand2",
                  command=self._confirm).pack(side=tk.LEFT)
        tk.Button(btn_frame, text="Cancel", font=FONT_LBL,
                  bg=BG_BTN, fg=FG, activebackground=BG_BTN_H,
                  relief=tk.FLAT, cursor="hand2",
                  command=self.destroy).pack(side=tk.LEFT, padx=(8, 0))

        self.transient(parent)
        self.grab_set()

    def _confirm(self):
        selected = [db for db, var in zip(self._dbs, self._vars) if var.get()]
        if not selected:
            self.destroy()
            return
        names = "\n".join(f"  {db.name}" for db in selected)
        if not messagebox.askyesno("Confirm Delete",
                                   f"Delete {len(selected)} file(s)?\n\n{names}",
                                   parent=self):
            return
        errors = []
        for db in selected:
            try:
                db.unlink()
            except Exception as e:
                errors.append(f"{db.name}: {e}")
        if errors:
            messagebox.showerror("Error", "Failed to delete:\n" + "\n".join(errors), parent=self)
        self.destroy()
        self.master._refresh_versions()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    App().mainloop()
