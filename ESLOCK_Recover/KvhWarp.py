#!/usr/bin/env python
"""KvhWarp - GUI entry point.
All crypto/warp logic lives in kvhwarp_core; this module handles UI only.
"""
from __future__ import annotations

import os
import sys
import shutil
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from kvhwarp_core import (  # noqa: F401
    _APP_VERSION, _APP_NAME, _APP_COPYRIGHT,
    _ICO_FILE,
    _DEFAULTS,
    KDF_SHA256, KDF_SCRYPT,
    ENCRYPT_SIZE, ENCRYPT_MIDDLE_SIZE,
    KS_EXT, MIN_FILE_SIZE,
    warp_file, warp_file_inplace,
    unwarp_auto,
    rename_subfolders, restore_subfolders, cleanup_empty_dirs,
    _load_opts, _save_opts,
    _range_resolve_bc, _skip_exts_from_opts,
)


THEMES = {
    "Dark": {
        "BG":       "#23272e",
        "FG":       "#e0e0e0",
        "CYAN":     "#00e0e0",
        "GREEN":    "#27ae60",
        "GOLD":     "#f5d060",
        "BG_PANEL": "#2c313c",
        "ACTIVE_BG":"#3c3c3c",
    },
    "Light": {
        "BG":       "#f0f0f0",
        "FG":       "#1e1e1e",
        "CYAN":     "#0078d4",
        "GREEN":    "#107c10",
        "GOLD":     "#ca5010",
        "BG_PANEL": "#ffffff",
        "ACTIVE_BG":"#d0d0d0",
    },
    "Solarized Dark": {
        "BG":       "#002b36",
        "FG":       "#839496",
        "CYAN":     "#2aa198",
        "GREEN":    "#859900",
        "GOLD":     "#e8b400",
        "BG_PANEL": "#073642",
        "ACTIVE_BG":"#094552",
    },
    "High Contrast": {
        "BG":       "#000000",
        "FG":       "#ffffff",
        "CYAN":     "#00ffff",
        "GREEN":    "#00ff00",
        "GOLD":     "#ffff00",
        "BG_PANEL": "#1a1a1a",
        "ACTIVE_BG":"#333333",
    },
    "Dracula": {
        "BG":       "#282a36",
        "FG":       "#f8f8f2",
        "CYAN":     "#8be9fd",
        "GREEN":    "#50fa7b",
        "GOLD":     "#f1fa8c",
        "BG_PANEL": "#44475a",
        "ACTIVE_BG":"#6272a4",
    },
    "Nord": {
        "BG":       "#3b4252",
        "FG":       "#d8dee9",
        "CYAN":     "#88c0d0",
        "GREEN":    "#a3be8c",
        "GOLD":     "#ebcb8b",
        "BG_PANEL": "#4c566a",
        "ACTIVE_BG":"#5e6a80",
    },
    "Monokai": {
        "BG":       "#272822",
        "FG":       "#f8f8f2",
        "CYAN":     "#66d9e8",
        "GREEN":    "#a6e22e",
        "GOLD":     "#e6db74",
        "BG_PANEL": "#35342a",
        "ACTIVE_BG":"#423f34",
    },
    "Custom": dict(THEMES["Dark"] if False else {  # placeholder; overwritten at runtime
        "BG": "#23272e", "FG": "#e0e0e0", "CYAN": "#00e0e0",
        "GREEN": "#27ae60", "GOLD": "#f5d060",
        "BG_PANEL": "#2c313c", "ACTIVE_BG": "#3c3c3c",
    }),
}

def _resolve_theme(opts: dict) -> dict:
    name = opts.get("theme", "Dark")
    # Inject saved custom colors so Custom slot is always up to date
    saved_custom = opts.get("custom_theme")
    if saved_custom:
        THEMES["Custom"].update(saved_custom)
    return THEMES.get(name, THEMES["Dark"])

_th = _resolve_theme(_load_opts())
BG       = _th["BG"]
FG       = _th["FG"]
CYAN     = _th["CYAN"]
GREEN    = _th["GREEN"]
GOLD     = _th["GOLD"]
BG_PANEL = _th["BG_PANEL"]
ACTIVE_BG = _th["ACTIVE_BG"]

def _apply_theme(name: str) -> None:
    """Update module-level color globals to the named theme."""
    global BG, FG, CYAN, GREEN, GOLD, BG_PANEL, ACTIVE_BG
    _th = THEMES.get(name, THEMES["Dark"])
    BG       = _th["BG"]
    FG       = _th["FG"]
    CYAN     = _th["CYAN"]
    GREEN    = _th["GREEN"]
    GOLD     = _th["GOLD"]
    BG_PANEL = _th["BG_PANEL"]
    ACTIVE_BG = _th["ACTIVE_BG"]


class _CustomThemeDialog(tk.Toplevel):
    """Color-picker dialog for the Custom theme."""

    _TOKENS = [
        ("BG",        "Background"),
        ("BG_PANEL",  "Panel / Input BG"),
        ("FG",        "Foreground text"),
        ("CYAN",      "Accent (Browse / hover)"),
        ("GREEN",     "Warp button"),
        ("GOLD",      "Unwarp button"),
        ("ACTIVE_BG", "Active / hover BG"),
    ]

    def __init__(self, parent: tk.Tk):
        super().__init__(parent)
        self._parent = parent
        self.title("Custom Theme")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.grab_set()
        self.wm_attributes("-topmost", True)
        if os.path.exists(_ICO_FILE):
            try:
                self.iconbitmap(_ICO_FILE)
            except Exception:
                pass

        # Working copy — start from current Custom values
        self._colors: dict = dict(THEMES["Custom"])
        self._swatches: dict = {}  # token → Label widget

        tk.Label(self, text="Custom Theme", font=("Segoe UI", 11, "bold"),
                 bg=BG, fg=FG).pack(pady=(14, 8))

        grid = tk.Frame(self, bg=BG)
        grid.pack(padx=20, pady=(0, 10))

        for row_idx, (token, label) in enumerate(self._TOKENS):
            tk.Label(grid, text=label, font=("Segoe UI", 9),
                     bg=BG, fg=FG, anchor="w", width=22).grid(
                row=row_idx, column=0, sticky="w", pady=3)

            swatch = tk.Label(grid, width=6, relief=tk.FLAT,
                              bg=self._colors.get(token, "#888888"))
            swatch.grid(row=row_idx, column=1, padx=(6, 4))
            self._swatches[token] = swatch

            hex_var = tk.StringVar(value=self._colors.get(token, "#888888"))
            hex_entry = tk.Entry(grid, textvariable=hex_var, width=9,
                                 font=("Consolas", 9),
                                 bg=BG_PANEL, fg=FG, insertbackground=FG,
                                 relief=tk.FLAT)
            hex_entry.grid(row=row_idx, column=2, padx=(0, 4))
            hex_entry.bind("<FocusOut>",
                           lambda e, t=token, v=hex_var: self._on_hex_entry(t, v))
            hex_entry.bind("<Return>",
                           lambda e, t=token, v=hex_var: self._on_hex_entry(t, v))

            tk.Button(grid, text="Pick", font=("Segoe UI", 8),
                      bg=BG_PANEL, fg=FG, activebackground=ACTIVE_BG,
                      relief=tk.FLAT, cursor="hand2",
                      command=lambda t=token, v=hex_var: self._pick(t, v)
                      ).grid(row=row_idx, column=3)

        # Buttons row
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=(4, 14))
        tk.Button(btn_row, text="Reset to Dark", font=("Segoe UI", 9),
                  bg=BG_PANEL, fg=FG, activebackground=ACTIVE_BG,
                  relief=tk.FLAT, cursor="hand2",
                  command=self._reset).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(btn_row, text="Apply", width=10, font=("Segoe UI", 9, "bold"),
                  bg=GREEN, fg="#ffffff", activebackground=GREEN,
                  relief=tk.FLAT, cursor="hand2",
                  command=self._apply).pack(side=tk.LEFT)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(
            f"+{(sw - self.winfo_width()) // 2}+{(sh - self.winfo_height()) // 2}")

    def _on_hex_entry(self, token: str, var: tk.StringVar):
        val = var.get().strip()
        if not val.startswith("#"):
            val = "#" + val
        if len(val) == 7:
            try:
                int(val[1:], 16)
                self._colors[token] = val
                self._swatches[token].configure(bg=val)
                var.set(val)
            except ValueError:
                pass

    def _pick(self, token: str, var: tk.StringVar):
        from tkinter import colorchooser
        initial = self._colors.get(token, "#888888")
        result = colorchooser.askcolor(color=initial, title=f"Pick color — {token}",
                                       parent=self)
        if result and result[1]:
            hex_color = result[1]
            self._colors[token] = hex_color
            self._swatches[token].configure(bg=hex_color)
            var.set(hex_color)

    def _reset(self):
        dark = THEMES["Dark"]
        self._colors = dict(dark)
        for token, swatch in self._swatches.items():
            swatch.configure(bg=dark[token])

    def _apply(self):
        THEMES["Custom"].update(self._colors)
        # Persist custom colors
        self._parent.opts["custom_theme"] = dict(self._colors)
        self._parent.opts["theme"] = "Custom"
        _save_opts(self._parent.opts)
        self._parent._var_theme.set("Custom")
        _apply_theme("Custom")
        self._parent.configure(bg=BG)
        self._parent._destroy_ui()
        self._parent._build_ui()
        self._parent._build_menu()
        self.destroy()


class _AboutDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title(f"About {_APP_NAME}")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.wm_attributes("-topmost", True)
        self.grab_set()
        if os.path.exists(_ICO_FILE):
            try:
                self.iconbitmap(_ICO_FILE)
            except Exception:
                pass

        tk.Label(self, text=f"{_APP_NAME}  v{_APP_VERSION}",
                 font=("Segoe UI", 13, "bold"), bg=BG, fg=FG
                 ).pack(pady=(20, 2))

        tk.Label(self, text="File Stealth Tool",
                 font=("Segoe UI", 9), bg=BG, fg="#aaaaaa"
                 ).pack(pady=(0, 4))

        tk.Label(self, text=_APP_COPYRIGHT,
                 font=("Segoe UI", 9), bg=BG, fg="#888888"
                 ).pack(pady=(0, 14))

        tk.Button(self, text="Close", width=10,
                  font=("Segoe UI", 9, "bold"),
                  bg=BG_PANEL, fg=FG, activebackground=ACTIVE_BG,
                  relief=tk.FLAT, cursor="hand2",
                  command=self.destroy).pack(pady=(0, 14))

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(
            f"+{(sw - self.winfo_width()) // 2}+{(sh - self.winfo_height()) // 2}")


def _auto_update_association():
    """If the user previously associated .ks with KvhWarp, silently update
    the registry command to the current exe path (handles exe being moved)."""
    import winreg
    reg_key = r"Software\Classes\KvhWarpFile\shell\open\command"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_key) as k:
            current_cmd, _ = winreg.QueryValueEx(k, "")
    except FileNotFoundError:
        return  # Not associated yet — nothing to update
    except Exception:
        return

    exe = sys.executable
    expected_cmd = f'"{exe}" "%1"'
    if current_cmd == expected_cmd:
        return  # Already up to date

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, reg_key) as k:
            winreg.SetValue(k, "", winreg.REG_SZ, expected_cmd)
    except Exception:
        pass  # Fail silently — non-critical


class _SingleDecryptDialog(tk.Tk):
    """Minimal window shown when a .ks file is double-clicked."""
    def __init__(self, ks_path: Path):
        super().__init__()
        _auto_update_association()
        self._ks_path = ks_path
        self._pw_visible = False
        self._opts = _load_opts()
        self.title(f"{_APP_NAME} — Decrypt File")
        self.configure(bg=BG)
        self.resizable(False, False)
        if os.path.exists(_ICO_FILE):
            try:
                self.iconbitmap(_ICO_FILE)
            except Exception:
                pass

        outer = tk.Frame(self, bg=BG)
        outer.pack(fill=tk.BOTH, expand=True, padx=20, pady=16)

        tk.Label(outer, text="Decrypt File", font=("Segoe UI", 12, "bold"),
                 bg=BG, fg=FG).pack(anchor=tk.W, pady=(0, 8))

        tk.Label(outer, text=ks_path.name, font=("Consolas", 9),
                 bg=BG, fg=CYAN, wraplength=360, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 12))

        row_pw = tk.Frame(outer, bg=BG)
        row_pw.pack(fill=tk.X, pady=(0, 10))
        tk.Label(row_pw, text="Password:", font=("Segoe UI", 9),
                 bg=BG, fg=FG).pack(side=tk.LEFT)
        self._var_pw = tk.StringVar()
        self._ent_pw = tk.Entry(row_pw, textvariable=self._var_pw,
                                show="*", font=("Segoe UI", 10),
                                bg=BG_PANEL, fg=FG, insertbackground=FG,
                                relief=tk.FLAT, width=24)
        self._ent_pw.pack(side=tk.LEFT, padx=(8, 4))
        self._ent_pw.focus_set()
        tk.Button(row_pw, text="\U0001f441", font=("Segoe UI", 9),
                  bg=BG_PANEL, fg=FG, activebackground=BG_PANEL,
                  relief=tk.FLAT, cursor="hand2",
                  command=self._toggle_pw).pack(side=tk.LEFT)

        # -- After decrypt option --
        row_open = tk.Frame(outer, bg=BG)
        row_open.pack(fill=tk.X, pady=(0, 6))
        tk.Label(row_open, text="After decrypt:", font=("Segoe UI", 8),
                 bg=BG, fg="#888888").pack(side=tk.LEFT, padx=(0, 6))
        self._var_after = tk.StringVar(value=self._opts.get("after_decrypt", "auto"))
        for val, label in (("auto", "Auto Open"), ("confirm", "Open after Confirm"), ("folder", "Open Folder")):
            tk.Radiobutton(row_open, text=label, variable=self._var_after, value=val,
                           font=("Segoe UI", 8), bg=BG, fg=FG,
                           activebackground=BG, activeforeground=CYAN,
                           selectcolor=BG, relief=tk.FLAT,
                           command=self._save_after_opt).pack(side=tk.LEFT, padx=(0, 10))

        # -- Keep Encrypted checkbox --
        row_keep = tk.Frame(outer, bg=BG)
        row_keep.pack(fill=tk.X, pady=(0, 12))
        self._var_keep = tk.BooleanVar(value=self._opts.get("keep_encrypted", False))
        tk.Checkbutton(row_keep, text="Keep Encrypted  (decrypt to temp, auto-clean on close)",
                       variable=self._var_keep,
                       font=("Segoe UI", 8), bg=BG, fg="#aaaaaa",
                       activebackground=BG, activeforeground=CYAN,
                       selectcolor=BG, relief=tk.FLAT,
                       command=self._save_keep_opt).pack(side=tk.LEFT)

        row_btn = tk.Frame(outer, bg=BG)
        row_btn.pack(fill=tk.X)
        self._btn_decrypt = tk.Button(row_btn, text="\u25c0  Decrypt",
                                      width=14, font=("Segoe UI", 10, "bold"),
                                      bg=GOLD, fg="#1e1e1e",
                                      activebackground="#f5d060",
                                      relief=tk.FLAT, cursor="hand2",
                                      command=self._do_decrypt)
        self._btn_decrypt.pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(row_btn, text="Cancel", width=10,
                  font=("Segoe UI", 10), bg=BG_PANEL, fg=FG,
                  activebackground=ACTIVE_BG, relief=tk.FLAT,
                  cursor="hand2", command=self.destroy).pack(side=tk.LEFT)
        self._btn_open = tk.Button(row_btn, text="Open File", width=10,
                                   font=("Segoe UI", 10), bg=BG_PANEL, fg=CYAN,
                                   activebackground=CYAN, activeforeground="#1e1e1e",
                                   relief=tk.FLAT, cursor="hand2",
                                   command=self._open_restored)
        # hidden until needed for "confirm" mode

        self._lbl_status = tk.Label(outer, text="", font=("Segoe UI", 9),
                                    bg=BG, fg="#aaaaaa", wraplength=360,
                                    justify=tk.LEFT)
        self._lbl_status.pack(anchor=tk.W, pady=(10, 0))

        self._restored_path: Optional[Path] = None
        self._temp_path: Optional[Path] = None

        self.bind("<Return>", lambda e: self._do_decrypt())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{(self.winfo_screenwidth()-w)//2}+{(self.winfo_screenheight()-h)//2}")

    def _toggle_pw(self):
        self._pw_visible = not self._pw_visible
        self._ent_pw.configure(show="" if self._pw_visible else "*")

    def _on_close(self):
        self._cleanup_temp()
        self.destroy()

    def _cleanup_temp(self):
        if self._temp_path and self._temp_path.exists():
            try:
                self._temp_path.unlink()
            except Exception:
                pass
        self._temp_path = None

    def _save_after_opt(self):
        self._opts["after_decrypt"] = self._var_after.get()
        _save_opts(self._opts)

    def _save_keep_opt(self):
        self._opts["keep_encrypted"] = self._var_keep.get()
        _save_opts(self._opts)

    def _open_restored(self):
        if self._restored_path and self._restored_path.exists():
            os.startfile(str(self._restored_path))

    def _do_decrypt(self):
        pw = self._var_pw.get()
        keep = self._var_keep.get()
        self._btn_decrypt.configure(state=tk.DISABLED)
        self._lbl_status.configure(text="Decrypting…", fg="#aaaaaa")
        self.update()

        def _run():
            if keep:
                import tempfile
                temp_dir = Path(tempfile.gettempdir()) / "KvhWarp"
                temp_dir.mkdir(exist_ok=True)
                tmp_ks = temp_dir / self._ks_path.name
                shutil.copy2(str(self._ks_path), str(tmp_ks))
                result = unwarp_auto(tmp_ks, pw)
                self.after(0, self._on_done, result, keep, temp_dir)
            else:
                result = unwarp_auto(self._ks_path, pw)
                self.after(0, self._on_done, result, keep, self._ks_path.parent)

        import threading
        threading.Thread(target=_run, daemon=True).start()

    def _on_done(self, result: str, keep: bool, restore_dir: Path):
        self._btn_decrypt.configure(state=tk.NORMAL)
        if result.startswith("OK"):
            # Parse restored filename from "OK: x.ks -> photo.PNG (...)"
            try:
                restored_name = result.split(" -> ")[1].split(" (")[0]
                self._restored_path = restore_dir / restored_name
                if keep:
                    self._temp_path = self._restored_path
            except Exception:
                self._restored_path = None

            status = result + ("  [temp]") if keep else result
            self._lbl_status.configure(text=status, fg=GREEN)

            after = self._var_after.get()
            if after == "auto":
                if self._restored_path and self._restored_path.exists():
                    os.startfile(str(self._restored_path))
                self.after(1500, self._on_close)
            elif after == "confirm":
                self._btn_open.pack(side=tk.LEFT, padx=(16, 0))
                # Dialog stays open; user clicks Open File or closes manually
            elif after == "folder":
                os.startfile(str(restore_dir))
                self.after(1500, self._on_close)
        else:
            self._lbl_status.configure(text=result, fg="#e05050")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        _auto_update_association()
        self.title(f"{_APP_NAME}  v{_APP_VERSION}")
        self.configure(bg=BG)
        self.resizable(False, False)
        if os.path.exists(_ICO_FILE):
            try:
                self.iconbitmap(_ICO_FILE)
            except Exception:
                pass
        self.update_idletasks()
        width, height = 700, 530
        x = (self.winfo_screenwidth() // 2) - (width // 2)
        y = (self.winfo_screenheight() // 2) - (height // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

        self.opts = _load_opts()
        self._pw_visible = False

        # Persistent tk variables (survive UI rebuilds)
        self._var_folder  = tk.StringVar(value=self.opts.get("last_folder", ""))
        self._var_pw      = tk.StringVar()
        self._var_scrypt  = tk.BooleanVar(value=True)
        self._var_inplace = tk.BooleanVar(value=True)
        self._var_enc     = tk.IntVar(value=self.opts.get("encrypt_size", ENCRYPT_SIZE))
        self._var_tail    = tk.BooleanVar(value=self.opts.get("encrypt_tail", False))
        self._var_enc_all     = tk.BooleanVar(value=self.opts.get("encrypt_all", False))
        self._var_theme       = tk.StringVar(value=self.opts.get("theme", "Dark"))
        # Range CTR vars
        self._var_range      = tk.BooleanVar(value=self.opts.get("encrypt_range", False))
        self._var_range_mode = tk.StringVar(value=self.opts.get("range_mode", "auto"))
        self._var_range_pct  = tk.IntVar(value=self.opts.get("range_percent", 25))
        self._var_range_b    = tk.IntVar(value=self.opts.get("range_b", 1))
        self._var_range_c    = tk.IntVar(value=self.opts.get("range_c", 4))
        self._var_range_unit = tk.StringVar(value=self.opts.get("range_unit", "KB"))
        # Adaptive compression vars (copy mode only)
        self._var_compress    = tk.BooleanVar(value=self.opts.get("compress_copy", False))
        self._var_compress_mb = tk.IntVar(value=self.opts.get("compress_max_mb", 500))
        self._compress_skip_exts = _skip_exts_from_opts(self.opts)

        self._build_menu()
        self._build_ui()

    def _build_ui(self):
        """Build (or rebuild) all content widgets using current theme globals."""
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        self._outer = outer

        # ── Folder row ──
        row_folder = tk.Frame(outer, bg=BG)
        row_folder.pack(fill=tk.X, pady=(0, 12))
        tk.Label(row_folder, text="Folder:", font=("Segoe UI", 10), bg=BG, fg=FG).pack(side=tk.LEFT)
        self._ent_folder = tk.Entry(row_folder, textvariable=self._var_folder, font=("Segoe UI", 10), bg=BG_PANEL, fg=FG, insertbackground=FG, relief=tk.FLAT)
        self._ent_folder.pack(side=tk.LEFT, padx=(8, 4), fill=tk.X, expand=True)
        tk.Button(row_folder, text="Browse", font=("Segoe UI", 9), bg=BG_PANEL, fg=FG, activebackground=CYAN, relief=tk.FLAT, command=self._browse_folder).pack(side=tk.LEFT)

        # ── Password row ──
        row_pw = tk.Frame(outer, bg=BG)
        row_pw.pack(fill=tk.X, pady=(0, 12))
        tk.Label(row_pw, text="Password:", font=("Segoe UI", 10), bg=BG, fg=FG).pack(side=tk.LEFT)
        self._ent_pw = tk.Entry(row_pw, textvariable=self._var_pw, font=("Segoe UI", 10), show="*", bg=BG_PANEL, fg=FG, insertbackground=FG, relief=tk.FLAT)
        self._ent_pw.pack(side=tk.LEFT, padx=(8, 4), fill=tk.X, expand=True)
        self._pw_visible = False
        self._btn_eye = tk.Button(row_pw, text="👁", width=3, font=("Segoe UI", 9), bg=BG_PANEL, fg=FG, activebackground=ACTIVE_BG, relief=tk.FLAT, cursor="hand2", command=self._toggle_pw)
        self._btn_eye.pack(side=tk.LEFT)

        # ── scrypt/in-place options ──
        row_kdf = tk.Frame(outer, bg=BG)
        row_kdf.pack(fill=tk.X, pady=(0, 12))
        tk.Checkbutton(row_kdf, text="Use scrypt (strong KDF, ~130 ms)", variable=self._var_scrypt, font=("Segoe UI", 9), bg=BG, fg=FG, activebackground=BG, activeforeground=CYAN, selectcolor=BG_PANEL).pack(side=tk.LEFT, padx=(0, 16))
        self._chk_inplace = tk.Checkbutton(row_kdf, text="In-place (fast, no copy)", variable=self._var_inplace, font=("Segoe UI", 9), bg=BG, fg=FG, activebackground=BG, activeforeground=CYAN, selectcolor=BG_PANEL, command=self._on_inplace_toggle)
        self._chk_inplace.pack(side=tk.LEFT)

        # ── Adaptive compress row (copy mode only) ──
        row_compress = tk.Frame(outer, bg=BG)
        row_compress.pack(fill=tk.X, pady=(0, 8))
        self._chk_compress = tk.Checkbutton(
            row_compress, text="Adaptive Compress (Zstd, entropy-based, copy mode only)",
            variable=self._var_compress,
            font=("Segoe UI", 9), bg=BG, fg=FG,
            activebackground=BG, activeforeground=CYAN,
            selectcolor=BG_PANEL,
            command=self._on_compress_toggle,
        )
        self._chk_compress.pack(side=tk.LEFT)
        self._frm_compress_limit = tk.Frame(row_compress, bg=BG)
        self._frm_compress_limit.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(self._frm_compress_limit, text="Max:", font=("Segoe UI", 8),
                 bg=BG, fg="#888888").pack(side=tk.LEFT)
        self._spn_compress_mb = tk.Spinbox(
            self._frm_compress_limit, textvariable=self._var_compress_mb,
            values=(100, 200, 500, 1000, 2000, 0),
            font=("Segoe UI", 9), width=5,
            bg=BG_PANEL, fg=FG,
            buttonbackground=BG_PANEL, insertbackground=FG, relief=tk.FLAT,
        )
        self._spn_compress_mb.pack(side=tk.LEFT, padx=(2, 2))
        tk.Label(self._frm_compress_limit, text="MB (0=∞)", font=("Segoe UI", 8),
                 bg=BG, fg="#888888").pack(side=tk.LEFT)
        # Set initial state
        self._on_inplace_toggle()

        # ── Encrypt size row ──
        row_enc = tk.Frame(outer, bg=BG)
        row_enc.pack(fill=tk.X, pady=(0, 12))
        tk.Label(row_enc, text="Encrypt bytes:", font=("Segoe UI", 9), bg=BG, fg=FG).pack(side=tk.LEFT)
        enc_opts = (1024, 4096, 65536, 1048576)
        self._enc_spin = tk.Spinbox(row_enc, textvariable=self._var_enc, values=enc_opts,
                              font=("Segoe UI", 9), width=10,
                              bg=BG_PANEL, fg=FG,
                              buttonbackground=BG_PANEL,
                              disabledforeground=FG,
                              readonlybackground=BG_PANEL,
                              insertbackground=FG, relief=tk.FLAT,
                              state="readonly")
        self._enc_spin.pack(side=tk.LEFT, padx=(8, 8))
        tk.Label(row_enc, text="(1KB / 4KB / 64KB / 1MB)", font=("Segoe UI", 8), bg=BG, fg="#888888").pack(side=tk.LEFT)
        self._chk_tail = tk.Checkbutton(row_enc, text="+ Tail", variable=self._var_tail,
                       font=("Segoe UI", 9), bg=BG, fg=FG,
                       activebackground=BG, activeforeground=CYAN,
                       selectcolor=BG_PANEL)
        self._chk_tail.pack(side=tk.LEFT, padx=(16, 0))
        tk.Checkbutton(row_enc, text="Encrypt All", variable=self._var_enc_all,
                       font=("Segoe UI", 9), bg=BG, fg=FG,
                       activebackground=BG, activeforeground=CYAN,
                       selectcolor=BG_PANEL,
                       command=self._on_enc_all_toggle).pack(side=tk.LEFT, padx=(16, 0))
        if self._var_enc_all.get():
            self._enc_spin.configure(state="disabled")
            self._chk_tail.configure(state="disabled")

        # ── Range CTR row ──
        row_range = tk.Frame(outer, bg=BG)
        row_range.pack(fill=tk.X, pady=(0, 8))
        tk.Checkbutton(row_range, text="Range CTR", variable=self._var_range,
                       font=("Segoe UI", 9), bg=BG, fg=FG,
                       activebackground=BG, activeforeground=CYAN,
                       selectcolor=BG_PANEL,
                       command=self._on_range_toggle).pack(side=tk.LEFT)
        self._frm_range_opts = tk.Frame(row_range, bg=BG)
        self._frm_range_opts.pack(side=tk.LEFT, padx=(6, 0))
        # Auto radio + percent
        self._rb_range_auto = tk.Radiobutton(self._frm_range_opts, text="Auto",
                       variable=self._var_range_mode, value="auto",
                       font=("Segoe UI", 9), bg=BG, fg=FG,
                       activebackground=BG, activeforeground=CYAN,
                       selectcolor=BG_PANEL, command=self._on_range_mode_change)
        self._rb_range_auto.pack(side=tk.LEFT)
        self._spn_range_pct = tk.Spinbox(self._frm_range_opts, textvariable=self._var_range_pct,
                       from_=1, to=99, width=4,
                       font=("Segoe UI", 9), bg=BG_PANEL, fg=FG,
                       buttonbackground=BG_PANEL, insertbackground=FG, relief=tk.FLAT)
        self._spn_range_pct.pack(side=tk.LEFT, padx=(2, 2))
        tk.Label(self._frm_range_opts, text="%", font=("Segoe UI", 9), bg=BG, fg=FG
                 ).pack(side=tk.LEFT, padx=(0, 10))
        # Manual radio + B / C / unit
        self._rb_range_manual = tk.Radiobutton(self._frm_range_opts, text="Manual  B:",
                       variable=self._var_range_mode, value="manual",
                       font=("Segoe UI", 9), bg=BG, fg=FG,
                       activebackground=BG, activeforeground=CYAN,
                       selectcolor=BG_PANEL, command=self._on_range_mode_change)
        self._rb_range_manual.pack(side=tk.LEFT)
        self._spn_range_b = tk.Spinbox(self._frm_range_opts, textvariable=self._var_range_b,
                       from_=1, to=1024, width=4,
                       font=("Segoe UI", 9), bg=BG_PANEL, fg=FG,
                       buttonbackground=BG_PANEL, insertbackground=FG, relief=tk.FLAT)
        self._spn_range_b.pack(side=tk.LEFT, padx=(2, 4))
        tk.Label(self._frm_range_opts, text="C:", font=("Segoe UI", 9), bg=BG, fg=FG
                 ).pack(side=tk.LEFT)
        self._spn_range_c = tk.Spinbox(self._frm_range_opts, textvariable=self._var_range_c,
                       from_=1, to=1024, width=4,
                       font=("Segoe UI", 9), bg=BG_PANEL, fg=FG,
                       buttonbackground=BG_PANEL, insertbackground=FG, relief=tk.FLAT)
        self._spn_range_c.pack(side=tk.LEFT, padx=(2, 4))
        self._om_range_unit = tk.OptionMenu(self._frm_range_opts, self._var_range_unit,
                                            "B", "KB", "MB")
        self._om_range_unit.configure(font=("Segoe UI", 9), bg=BG_PANEL, fg=FG,
                                      activebackground=ACTIVE_BG, relief=tk.FLAT,
                                      bd=0, highlightthickness=0, width=3)
        self._om_range_unit["menu"].configure(bg=BG_PANEL, fg=FG,
                                               activebackground=ACTIVE_BG)
        self._om_range_unit.pack(side=tk.LEFT)
        # Set initial enabled/disabled state
        self._on_range_toggle()

        # ── Log ──
        self._log = tk.Text(outer, height=14, width=72, font=("Consolas", 9), bg=BG_PANEL, fg=FG, relief=tk.FLAT, state=tk.DISABLED, wrap=tk.WORD)
        self._log.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        # ── Buttons ──
        row_btn = tk.Frame(outer, bg=BG)
        row_btn.pack(fill=tk.X, pady=(0, 8))
        self._btn_warp = tk.Button(row_btn, text="\u25b6  Warp (Encrypt)", width=20, font=("Segoe UI", 10, "bold"), bg=GREEN, fg="#ffffff", activebackground=GREEN, relief=tk.FLAT, cursor="hand2", command=self._do_warp)
        self._btn_warp.pack(side=tk.LEFT, padx=(0, 8))
        self._btn_unwarp = tk.Button(row_btn, text="\u25c0  Unwarp (Decrypt)", width=20, font=("Segoe UI", 10, "bold"), bg=GOLD, fg="#1e1e1e", activebackground=GOLD, relief=tk.FLAT, cursor="hand2", command=self._do_unwarp)
        self._btn_unwarp.pack(side=tk.LEFT)
        self._btn_clearlog = tk.Button(row_btn, text="[ ] Clear Log", width=12, font=("Segoe UI", 10), bg=BG_PANEL, fg=FG, activebackground=CYAN, relief=tk.FLAT, cursor="hand2", command=self._clear_log)
        self._btn_clearlog.pack(side=tk.LEFT, padx=(16, 0))

    def _build_menu(self):
        menubar = tk.Menu(self, bg=BG_PANEL, fg=FG,
                          activebackground=ACTIVE_BG, activeforeground=CYAN,
                          relief=tk.FLAT, bd=0)
        self.config(menu=menubar)

        # Theme menu
        theme_menu = tk.Menu(menubar, tearoff=0, bg=BG_PANEL, fg=FG,
                             activebackground=ACTIVE_BG, activeforeground=CYAN)
        menubar.add_cascade(label="Theme", menu=theme_menu)
        for name in THEMES:
            theme_menu.add_radiobutton(
                label=name, variable=self._var_theme, value=name,
                command=self._on_theme_change,
            )
        theme_menu.add_separator()
        theme_menu.add_command(label="Edit Custom Theme\u2026",
                               command=self._open_custom_theme_dialog)

        help_menu = tk.Menu(menubar, tearoff=0, bg=BG_PANEL, fg=FG,
                            activebackground=ACTIVE_BG, activeforeground=CYAN)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Associate .ks files with KvhWarp\u2026",
                              command=self._associate_ks)
        help_menu.add_separator()
        help_menu.add_command(label=f"About {_APP_NAME}\u2026",
                              command=lambda: _AboutDialog(self))

    def _destroy_ui(self):
        """Destroy all content widgets (everything except the menu bar)."""
        if hasattr(self, "_outer") and self._outer.winfo_exists():
            self._outer.destroy()

    def _on_theme_change(self):
        name = self._var_theme.get()
        if name == "Custom":
            self._open_custom_theme_dialog()
            return
        self.opts["theme"] = name
        _save_opts(self.opts)
        _apply_theme(name)
        self.configure(bg=BG)
        self._destroy_ui()
        self._build_ui()
        self._build_menu()

    def _open_custom_theme_dialog(self):
        # Sync Custom slot with saved colors before opening
        saved = self.opts.get("custom_theme")
        if saved:
            THEMES["Custom"].update(saved)
        _CustomThemeDialog(self)

    def _associate_ks(self):
        """Register .ks extension to open with this executable in the Windows registry."""
        import winreg, subprocess
        exe = sys.executable if getattr(sys, "frozen", False) else str(Path(sys.executable))
        try:
            # .ks → ProgID
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.ks") as k:
                winreg.SetValue(k, "", winreg.REG_SZ, "KvhWarpFile")
            # ProgID description
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\KvhWarpFile") as k:
                winreg.SetValue(k, "", winreg.REG_SZ, "KvhWarp Encrypted File")
            # open command
            cmd = f'"{exe}" "%1"'
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                  r"Software\Classes\KvhWarpFile\shell\open\command") as k:
                winreg.SetValue(k, "", winreg.REG_SZ, cmd)
            # Notify shell
            try:
                subprocess.run(["ie4uinit.exe", "-show"], check=False)
            except Exception:
                pass
            messagebox.showinfo("KvhWarp",
                ".ks files are now associated with KvhWarp.\n"
                "Double-click any .ks file to decrypt it.", parent=self)
        except Exception as e:
            messagebox.showerror("KvhWarp",
                f"Failed to register .ks association:\n{e}", parent=self)

    def _clear_log(self):
        self._log.configure(state=tk.NORMAL)
        self._log.delete(1.0, tk.END)
        self._log.configure(state=tk.DISABLED)

    def _toggle_pw(self):
        self._pw_visible = not self._pw_visible
        self._ent_pw.configure(show="" if self._pw_visible else "*")
        self._btn_eye.configure(text="🔒" if self._pw_visible else "👁")

    def _browse_folder(self):
        d = filedialog.askdirectory(initialdir=self._var_folder.get() or None)
        if d:
            self._var_folder.set(d)
            self.opts["last_folder"] = d
            _save_opts(self.opts)

    def _on_inplace_toggle(self):
        """Gray out compress options when in-place mode is selected."""
        inplace = self._var_inplace.get()
        compress_state = tk.DISABLED if inplace else tk.NORMAL
        self._chk_compress.configure(state=compress_state)
        # Also update sub-row based on both inplace and compress checkbox
        self._on_compress_toggle()

    def _on_compress_toggle(self):
        """Show/hide the size-limit sub-row; save option."""
        inplace = self._var_inplace.get()
        compress_on = self._var_compress.get() and not inplace
        limit_state = tk.NORMAL if compress_on else tk.DISABLED
        self._spn_compress_mb.configure(state=limit_state)
        self.opts["compress_copy"] = self._var_compress.get()
        self.opts["compress_max_mb"] = self._var_compress_mb.get()
        _save_opts(self.opts)

    def _on_range_toggle(self):
        """Enable/disable all Range CTR option widgets based on checkbox state."""
        enabled = self._var_range.get()
        radio_state = tk.NORMAL if enabled else tk.DISABLED
        self._rb_range_auto.configure(state=radio_state)
        self._rb_range_manual.configure(state=radio_state)
        if enabled:
            self._on_range_mode_change()
        else:
            for w in (self._spn_range_pct, self._spn_range_b,
                      self._spn_range_c, self._om_range_unit):
                w.configure(state=tk.DISABLED)

    def _on_range_mode_change(self):
        """Enable the auto or manual sub-controls based on the selected mode."""
        mode = self._var_range_mode.get()
        auto_st   = tk.NORMAL if mode == "auto"   else tk.DISABLED
        manual_st = tk.NORMAL if mode == "manual" else tk.DISABLED
        self._spn_range_pct.configure(state=auto_st)
        self._spn_range_b.configure(state=manual_st)
        self._spn_range_c.configure(state=manual_st)
        self._om_range_unit.configure(state=manual_st)

    def _on_enc_all_toggle(self):
        """Enable/disable spinbox and tail checkbox based on Encrypt All state."""
        if self._var_enc_all.get():
            self._enc_spin.configure(state="disabled")
            self._chk_tail.configure(state="disabled")
        else:
            self._enc_spin.configure(state="readonly")
            self._chk_tail.configure(state="normal")
        self.opts["encrypt_all"] = self._var_enc_all.get()
        _save_opts(self.opts)

    def _log_msg(self, msg: str):
        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, msg + "\n")
        self._log.see(tk.END)
        self._log.configure(state=tk.DISABLED)

    def _validate(self) -> Tuple[Optional[Path], Optional[str]]:
        folder = self._var_folder.get().strip()
        pw = self._var_pw.get()
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning("KvhWarp", "Select a valid folder.", parent=self)
            return None, None
        # Empty password is allowed: files are encrypted with a key derived from
        # an empty string (no-password / stealth-only mode).
        return Path(folder), pw

    def _do_warp(self) -> None:
        folder, pw = self._validate()
        if not folder:
            return
        kdf = KDF_SCRYPT if self._var_scrypt.get() else KDF_SHA256
        kdf_label = "scrypt" if kdf == KDF_SCRYPT else "SHA-256"
        use_inplace = self._var_inplace.get()
        mode_label = "in-place" if use_inplace else "copy"
        warp_fn = warp_file_inplace if use_inplace else warp_file
        enc_size = self._var_enc.get()
        use_tail = self._var_tail.get()
        encrypt_all = self._var_enc_all.get()
        self.opts["encrypt_size"] = enc_size
        self.opts["encrypt_tail"] = use_tail
        self.opts["encrypt_all"] = encrypt_all
        _save_opts(self.opts)
        head_label = "ALL" if encrypt_all else f"{enc_size}B"
        files = [f for f in folder.rglob("*") if f.is_file() and f.suffix.lower() != KS_EXT and (f.stat().st_size > 0 if encrypt_all else f.stat().st_size >= MIN_FILE_SIZE)]
        if not files:
            self._log_msg("No eligible files found.")
            return
        count = len(files)
        if not messagebox.askyesno("KvhWarp", f"Warp {count} file(s) in:\n{folder} (incl. subfolders)\n\nContinue?", parent=self):
            return
        self._btn_warp.configure(state=tk.DISABLED)
        self._btn_unwarp.configure(state=tk.DISABLED)
        self._log_msg(f"── Warp: {count} file(s)  [KDF: {kdf_label}, mode: {mode_label}, head: {head_label}{', tail' if use_tail and not encrypt_all else ''}]  ──")
        t0 = time.perf_counter()

        # Pre-resolve CTR options once for the whole batch
        _opt = self.opts.get
        do_middle       = _opt("encrypt_middle_inplace" if use_inplace else "encrypt_middle_copy",
                               not use_inplace)
        do_middle_size  = _opt("encrypt_middle_size", ENCRYPT_MIDDLE_SIZE)
        do_range        = self._var_range.get()
        r_mode          = self._var_range_mode.get()
        r_percent       = self._var_range_pct.get()
        r_b_val         = self._var_range_b.get()
        r_c_val         = self._var_range_c.get()
        r_unit          = self._var_range_unit.get()
        r_start_bytes   = int(_opt("range_start_mb", 0.0) * 1024 * 1024)
        r_end_bytes     = int(_opt("range_end_mb",   0.0) * 1024 * 1024)
        # Save current range settings to opts
        self.opts.update({
            "encrypt_range": do_range, "range_mode": r_mode,
            "range_percent": r_percent, "range_b": r_b_val,
            "range_c": r_c_val, "range_unit": r_unit,
        })
        _save_opts(self.opts)
        # Manual B/C resolved here; auto mode is resolved per-file inside warp_fn
        r_b, r_c        = _range_resolve_bc(r_b_val, r_c_val, r_unit, 0)

        # Compress options (copy mode only)
        do_compress       = self._var_compress.get() and not use_inplace
        compress_max_bytes = self._var_compress_mb.get() * 1_048_576
        if do_compress:
            self.opts["compress_copy"]   = True
            self.opts["compress_max_mb"] = self._var_compress_mb.get()
            _save_opts(self.opts)

        def _run():
            for f in files:
                try:
                    file_enc_size = f.stat().st_size if encrypt_all else enc_size
                    file_use_tail = False if encrypt_all else use_tail
                    eff_pw = pw
                    if use_inplace:
                        result = warp_fn(
                            f, eff_pw,
                            kdf=kdf,
                            base_folder=folder,
                            encrypt_size=file_enc_size,
                            encrypt_tail=file_use_tail,
                            encrypt_middle=do_middle,
                            encrypt_middle_size=do_middle_size,
                            encrypt_range=do_range,
                            range_start=r_start_bytes,
                            range_end=r_end_bytes,
                            range_b_bytes=r_b,
                            range_c_bytes=r_c,
                            range_mode=r_mode,
                            range_percent=r_percent,
                        )
                    else:
                        result = warp_fn(
                            f, eff_pw,
                            kdf=kdf,
                            base_folder=folder,
                            encrypt_size=file_enc_size,
                            encrypt_tail=file_use_tail,
                            encrypt_middle=do_middle,
                            encrypt_middle_size=do_middle_size,
                            encrypt_range=do_range,
                            range_start=r_start_bytes,
                            range_end=r_end_bytes,
                            range_b_bytes=r_b,
                            range_c_bytes=r_c,
                            range_mode=r_mode,
                            range_percent=r_percent,
                            compress=do_compress,
                            compress_max_bytes=compress_max_bytes,
                            compress_skip_exts=self._compress_skip_exts,
                        )
                except (PermissionError, FileNotFoundError, OSError):
                    result = f"ERR: {f.name} - File access error"
                except ValueError:
                    result = f"ERR: {f.name} - Invalid parameters"
                except Exception:
                    result = f"ERR: {f.name} - Unexpected error"
                self.after(0, self._log_msg, result)
            for msg in rename_subfolders(folder, pw):
                self.after(0, self._log_msg, msg)
            elapsed = time.perf_counter() - t0
            self.after(0, self._log_msg, f"── Done in {elapsed:.2f}s ──")
            self.after(0, lambda: self._btn_warp.configure(state=tk.NORMAL))
            self.after(0, lambda: self._btn_unwarp.configure(state=tk.NORMAL))
        import threading
        threading.Thread(target=_run, daemon=True).start()

    def _do_unwarp(self):
        folder, pw = self._validate()
        if not folder:
            return
        files = [f for f in folder.rglob("*.ks") if f.is_file()]
        if not files:
            self._log_msg("No .ks files found.")
            return
        count = len(files)
        if not messagebox.askyesno("KvhWarp", f"Unwarp {count} .ks file(s) in:\n{folder} (incl. subfolders)\n\nContinue?", parent=self):
            return
        self._btn_warp.configure(state=tk.DISABLED)
        self._btn_unwarp.configure(state=tk.DISABLED)
        self._log_msg(f"── Unwarp: {count} file(s) ──")
        t0 = time.perf_counter()
        def _run():
            for f in files:
                try:
                    result = unwarp_auto(f, pw, base_folder=folder)
                except Exception as e:
                    result = f"ERR: {f.name} - {e}"
                self.after(0, self._log_msg, result)
            # Restore original subfolder names  
            for msg in restore_subfolders(folder, pw):
                self.after(0, self._log_msg, msg)
            for msg in cleanup_empty_dirs(folder):
                self.after(0, self._log_msg, msg)
            elapsed = time.perf_counter() - t0
            self.after(0, self._log_msg, f"── Done in {elapsed:.2f}s ──")
            self.after(0, lambda: self._btn_warp.configure(state=tk.NORMAL))
            self.after(0, lambda: self._btn_unwarp.configure(state=tk.NORMAL))
        import threading
        threading.Thread(target=_run, daemon=True).start()




if __name__ == "__main__":
    # Tell Windows to use the window icon in the taskbar (not the Python interpreter icon)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"KVH.{_APP_NAME}.{_APP_VERSION}"
        )
    except Exception:
        pass

    # Entry point: single-file decrypt (double-click) or full UI
    if (len(sys.argv) == 2
            and sys.argv[1].lower().endswith(KS_EXT)
            and Path(sys.argv[1]).is_file()):
        _SingleDecryptDialog(Path(sys.argv[1])).mainloop()
    else:
        App().mainloop()

