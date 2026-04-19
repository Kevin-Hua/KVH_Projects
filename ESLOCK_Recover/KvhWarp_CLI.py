#!/usr/bin/env python
"""KvhWarp_CLI.py — command-line interface for KvhWarp v2.

Usage:
    python KvhWarp_CLI.py warp        <folder>   [options]
    python KvhWarp_CLI.py unwarp      <folder>   [options]
    python KvhWarp_CLI.py unwarp-file <file.ks>  [options]

Requires: pip install pycryptodome
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from pathlib import Path

# ── Import from kvhwarp_core (same directory) ────────────────────────────────
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

from kvhwarp_core import (  # noqa: E402
    KDF_SHA256,
    KDF_SCRYPT,
    ENCRYPT_SIZE,
    ENCRYPT_MIDDLE_SIZE,
    KS_EXT,
    MIN_FILE_SIZE,
    warp_file,
    warp_file_inplace,
    unwarp_auto,
    rename_subfolders,
    restore_subfolders,
    cleanup_empty_dirs,
    _range_resolve_bc,
    _APP_NAME,
    _APP_VERSION,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_password(args_pw: str | None, prompt: str = "Password: ") -> str:
    if args_pw is not None:
        return args_pw
    return getpass.getpass(prompt)


# ── Sub-command handlers ──────────────────────────────────────────────────────

def cmd_warp(args: argparse.Namespace) -> int:
    folder = args.folder.resolve()
    if not folder.is_dir():
        print(f"Error: {folder} is not a valid directory.", file=sys.stderr)
        return 1

    pw  = _get_password(args.password)
    kdf = KDF_SHA256 if args.sha256 else KDF_SCRYPT
    warp_fn     = warp_file if args.copy else warp_file_inplace
    encrypt_all = args.encrypt_all
    enc_size    = args.encrypt_size
    use_tail    = args.tail
    do_middle   = not args.no_middle if args.no_middle else (args.middle if args.middle else (not args.copy))
    mid_size    = args.middle_size

    files = [
        f for f in folder.rglob("*")
        if f.is_file()
        and f.suffix.lower() != KS_EXT
        and (f.stat().st_size > 0 if encrypt_all else f.stat().st_size >= MIN_FILE_SIZE)
    ]
    if not files:
        print("No eligible files found.")
        return 0

    kdf_label  = "scrypt" if kdf == KDF_SCRYPT else "SHA-256"
    mode_label = "copy" if args.copy else "in-place"
    head_label = "ALL" if encrypt_all else f"{enc_size}B"
    print(f"Warp: {len(files)} file(s) in {folder}")
    print(f"  KDF: {kdf_label}, mode: {mode_label}, head: {head_label}"
          f"{', tail' if use_tail and not encrypt_all else ''}"
          f"{', middle' if do_middle else ''}")

    do_range  = args.range
    r_mode    = args.range_mode
    r_percent = int(args.range_percent)
    r_start   = int(args.range_start * 1024 * 1024)
    r_end     = int(args.range_end   * 1024 * 1024) if args.range_end else 0
    # Manual B/C pre-computed here; auto mode resolved per-file inside warp_fn
    r_b, r_c  = _range_resolve_bc(args.range_b, args.range_c, args.range_unit, 0)

    t0 = time.perf_counter()
    ok = err = 0
    for f in files:
        file_enc_size = f.stat().st_size if encrypt_all else enc_size
        file_use_tail = False if encrypt_all else use_tail
        result = warp_fn(
            f, pw,
            kdf=kdf,
            base_folder=folder,
            encrypt_size=file_enc_size,
            encrypt_tail=file_use_tail,
            encrypt_middle=do_middle,
            encrypt_middle_size=mid_size,
            encrypt_range=do_range,
            range_start=r_start,
            range_end=r_end,
            range_b_bytes=r_b,
            range_c_bytes=r_c,
            range_mode=r_mode,
            range_percent=r_percent,
        )
        print(f"  {result}")
        if result.startswith("OK"):
            ok += 1
        else:
            err += 1

    for msg in rename_subfolders(folder, pw):
        print(f"  {msg}")

    print(f"Done: {ok} OK, {err} errors in {time.perf_counter() - t0:.2f}s")
    return 0


def cmd_unwarp(args: argparse.Namespace) -> int:
    folder = args.folder.resolve()
    if not folder.is_dir():
        print(f"Error: {folder} is not a valid directory.", file=sys.stderr)
        return 1

    pw    = _get_password(args.password)
    files = [f for f in folder.rglob("*.ks") if f.is_file()]
    if not files:
        print("No .ks files found.")
        return 0

    print(f"Unwarp: {len(files)} .ks file(s) in {folder}")
    t0 = time.perf_counter()
    ok = err = 0
    for f in files:
        result = unwarp_auto(f, pw, base_folder=folder)
        print(f"  {result}")
        if result.startswith("OK"):
            ok += 1
        else:
            err += 1

    for msg in restore_subfolders(folder, pw):
        print(f"  {msg}")
    for msg in cleanup_empty_dirs(folder):
        print(f"  {msg}")

    print(f"Done: {ok} OK, {err} errors in {time.perf_counter() - t0:.2f}s")
    return 0


def cmd_unwarp_file(args: argparse.Namespace) -> int:
    fp = args.file.resolve()
    if not fp.is_file():
        print(f"Error: {fp} not found.", file=sys.stderr)
        return 1

    pw = _get_password(args.password)
    result = unwarp_auto(fp, pw)
    print(result)
    return 0 if result.startswith("OK") else 1


# ── Argument parser ───────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="KvhWarp_CLI",
        description=f"{_APP_NAME} — File Stealth Tool (CLI)",
    )
    sub = parser.add_subparsers(dest="command")

    # ── warp ──────────────────────────────────────────────────────────────────
    p_warp = sub.add_parser("warp", help="Encrypt files in a folder")
    p_warp.add_argument("folder", type=Path, help="Target folder")
    p_warp.add_argument("-p", "--password", default=None, metavar="PW",
                        help="Password (prompted if omitted)")
    p_warp.add_argument("--sha256", action="store_true",
                        help="Use SHA-256 KDF instead of scrypt")
    p_warp.add_argument("--copy", action="store_true",
                        help="Copy mode (default: in-place)")
    p_warp.add_argument("--encrypt-size", type=int, default=ENCRYPT_SIZE,
                        choices=[1024, 4096, 65536, 1_048_576],
                        metavar="{1024,4096,65536,1048576}",
                        help="Head bytes to encrypt (default: %(default)s)")
    p_warp.add_argument("--tail", action="store_true",
                        help="Also encrypt file tail")
    p_warp.add_argument("--all", action="store_true", dest="encrypt_all",
                        help="Encrypt entire file")

    # Middle CTR
    _mg = p_warp.add_mutually_exclusive_group()
    _mg.add_argument("--middle", action="store_true",
                     help="Enable center-region CTR (default ON for copy)")
    _mg.add_argument("--no-middle", action="store_true",
                     help="Disable center-region CTR")
    p_warp.add_argument("--middle-size", type=int, default=ENCRYPT_MIDDLE_SIZE,
                        metavar="BYTES",
                        help="Middle CTR region bytes (default: 1 MB)")

    # Range CTR
    p_warp.add_argument("--range", action="store_true",
                        help="Enable period-stride range CTR")
    p_warp.add_argument("--range-percent", type=float, default=25.0,
                        metavar="PCT",
                        help="Auto mode: encrypt PCT%% per period (default: 25)")
    p_warp.add_argument("--range-manual", action="store_true",
                        help="Manual B/C mode (use --range-b / --range-c)")
    p_warp.add_argument("--range-start", type=float, default=0.0, metavar="MB",
                        help="Range start in MB")
    p_warp.add_argument("--range-end",   type=float, default=0.0, metavar="MB",
                        help="Range end in MB (0 = file end)")
    p_warp.add_argument("--range-b", type=int, default=1, metavar="N",
                        help="Encrypt N units per period (manual)")
    p_warp.add_argument("--range-c", type=int, default=4, metavar="N",
                        help="Period N units (manual)")
    p_warp.add_argument("--range-unit", default="KB", choices=["B", "KB", "MB"],
                        help="Unit for --range-b/c (default: KB)")

    # ── unwarp ────────────────────────────────────────────────────────────────
    p_unwarp = sub.add_parser("unwarp", help="Decrypt .ks files in a folder")
    p_unwarp.add_argument("folder", type=Path, help="Target folder")
    p_unwarp.add_argument("-p", "--password", default=None, metavar="PW",
                          help="Password (prompted if omitted)")

    # ── unwarp-file ───────────────────────────────────────────────────────────
    p_file = sub.add_parser("unwarp-file", help="Decrypt a single .ks file")
    p_file.add_argument("file", type=Path, help="Path to .ks file")
    p_file.add_argument("-p", "--password", default=None, metavar="PW",
                        help="Password (prompted if omitted)")

    return parser


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    parser = _build_parser()
    args   = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # Inject range_mode convenience attribute for warp
    if args.command == "warp":
        args.range_mode = "manual" if getattr(args, "range_manual", False) else "auto"

    if args.command == "warp":
        return cmd_warp(args)
    if args.command == "unwarp":
        return cmd_unwarp(args)
    if args.command == "unwarp-file":
        return cmd_unwarp_file(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
