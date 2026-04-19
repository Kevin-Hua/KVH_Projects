#!/usr/bin/env python3
"""
ESLockEncryptor — Tool for encrypting files into ES File Explorer .eslock format

Requires: pip install pycryptodome
"""

import argparse
import hashlib
import os
import re
import struct
import sys
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from Crypto.Cipher import AES
except ImportError:
    print("Error: pycryptodome is required. Install with: pip install pycryptodome")
    sys.exit(1)

VERSION = "1.0"
AES_IV = bytes(range(16))       # [0, 1, 2, ..., 15] — matches decryptor
DEFAULT_BLOCK_SIZE = 1024
_PARTIAL_FLAG = 0x04             # standard partial-encryption marker


# ─── Cryptography ─────────────────────────────────────────────────────────────

def derive_key_from_password(password: str) -> bytes:
    """Derive a 16-byte AES key from a password using MD5 (mirrors decryptor)."""
    return hashlib.md5(password.encode("utf-8")).digest()[:16]


def _make_aes(key: bytes):
    """Create a fresh AES-CFB-128 cipher instance (IV = [0..15])."""
    return AES.new(key, AES.MODE_CFB, iv=AES_IV, segment_size=128)


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _copy_bytes(src, dst, count: int) -> None:
    """Copy exactly `count` bytes from src to dst using a buffer."""
    buf_size = 81920
    copied = 0
    while copied < count:
        chunk = src.read(min(buf_size, count - copied))
        if not chunk:
            break
        dst.write(chunk)
        copied += len(chunk)


# ─── Footer Builder ───────────────────────────────────────────────────────────

def _encrypt_file_name(name: str, key: bytes) -> bytes:
    """Encrypt the original filename for storage in the footer."""
    name_bytes = name.encode("utf-8")
    name_length = len(name_bytes)
    normalized_length = ((name_length - 1 >> 4) + 1) << 4
    buf = bytearray(normalized_length)
    buf[:name_length] = name_bytes
    return _make_aes(key).encrypt(bytes(buf))[:name_length]


def build_footer(
    key: bytes,
    is_partial: bool,
    encrypted_block_size: int,
    original_name: Optional[str],
) -> bytes:
    """
    Build the binary .eslock footer.

    Footer layout:
      [flag (1B)] [block_size if partial (4B BE)] [name_len (1B)] [enc_name?]
      [0x10] [key 16B] [0x00]  <- CRC covers everything up to here
      [00 00 00 00] [crc 4B BE] [footer_len 4B BE]
    """
    body = bytearray()

    # Encryption type flag
    if is_partial:
        body.append(_PARTIAL_FLAG)
        body += struct.pack(">i", encrypted_block_size)
    else:
        body.append(0xFF)  # full encryption marker

    # Original filename (optional)
    if original_name:
        enc_name = _encrypt_file_name(original_name, key)
        name_length = len(enc_name)
        if name_length > 254:
            body.append(0xFF)  # name too long — omit
        else:
            body.append(name_length)
            body += enc_name
    else:
        body.append(0xFF)  # no original name stored

    # Key section
    body.append(0x10)   # key prefix magic
    body += key          # 16 bytes
    body.append(0x00)   # key postfix magic (0x00 = standard)

    # CRC covers everything written so far
    crc = _crc32(bytes(body))

    body += b"\x00\x00\x00\x00"        # padding before CRC
    body += struct.pack(">I", crc)      # stored CRC (uint32 BE)
    footer_length = len(body) + 4       # +4 for the footer_length field itself
    body += struct.pack(">i", footer_length)

    return bytes(body)


# ─── Encryption Stream ────────────────────────────────────────────────────────

def encrypt_stream(
    input_stream,
    output_stream,
    key: bytes,
    is_partial: bool,
    block_size: int,
    file_length: int,
) -> bool:
    """
    Encrypt input_stream to output_stream.

    Partial mode: encrypt first `block_size` bytes, copy middle as-is,
                  encrypt last `block_size` bytes.
    Full mode:    stream-encrypt the entire file with a single cipher instance.

    Returns True if partial encryption was actually used, False if full was used.
    Falls back to full if the file is too small for partial (< 2 * block_size).
    """
    if is_partial and file_length >= 2 * block_size:
        middle_length = file_length - 2 * block_size

        first_block = input_stream.read(block_size)
        output_stream.write(_make_aes(key).encrypt(first_block))

        if middle_length > 0:
            _copy_bytes(input_stream, output_stream, middle_length)

        last_block = input_stream.read(block_size)
        output_stream.write(_make_aes(key).encrypt(last_block))
        return True

    else:
        # Full encryption — single cipher instance, no reset between chunks
        cipher = _make_aes(key)
        buf_size = 65536
        remaining = file_length
        while remaining > 0:
            chunk = input_stream.read(min(buf_size, remaining))
            if not chunk:
                break
            output_stream.write(cipher.encrypt(chunk))
            remaining -= len(chunk)
        return False


# ─── Configuration ────────────────────────────────────────────────────────────

@dataclass
class EncryptionConfig:
    input_path: Path
    output_path: Optional[Path]
    verbose: bool
    overwrite: bool
    is_partial: bool
    block_size: int
    password: Optional[str]
    key: Optional[bytes]
    store_name: bool


# ─── Buffered Thread-Safe Logger ──────────────────────────────────────────────

_console_lock = threading.Lock()


class BufferedLogger:
    """Buffers log messages and flushes them atomically to stdout."""

    RESET = "\033[0m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"

    def __init__(self) -> None:
        self._buffer: list[tuple[Optional[str], str]] = []

    def info(self, msg: str) -> None:
        self._buffer.append((None, msg))

    def success(self, msg: str) -> None:
        self._buffer.append(("green", f"[SUCCESS] {msg}"))

    def warning(self, msg: str) -> None:
        self._buffer.append(("yellow", f"[WARNING] {msg}"))

    def error(self, msg: str) -> None:
        self._buffer.append(("red", f"[ERROR] {msg}"))

    def flush(self) -> None:
        with _console_lock:
            for color, text in self._buffer:
                if color == "green":
                    print(f"{self.GREEN}{text}{self.RESET}")
                elif color == "yellow":
                    print(f"{self.YELLOW}{text}{self.RESET}")
                elif color == "red":
                    print(f"{self.RED}{text}{self.RESET}")
                else:
                    print(text)


# ─── Statistics ───────────────────────────────────────────────────────────────

class Statistics:
    """Thread-safe processing counters."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.files_processed = 0
        self.files_encrypted = 0
        self.files_skipped = 0
        self.errors = 0
        self.warnings = 0

    def increment_files_encrypted(self) -> None:
        with self._lock:
            self.files_encrypted += 1
            self.files_processed += 1

    def increment_files_skipped(self) -> None:
        with self._lock:
            self.files_skipped += 1
            self.files_processed += 1

    def increment_errors(self) -> None:
        with self._lock:
            self.errors += 1

    def increment_warnings(self) -> None:
        with self._lock:
            self.warnings += 1


# ─── Processor ────────────────────────────────────────────────────────────────

class EslockEncryptProcessor:
    def __init__(self, config: EncryptionConfig) -> None:
        self.config = config
        self.stats = Statistics()

    def execute(self) -> None:
        _print_info()
        p = self.config.input_path
        if p.is_file():
            self._process_file(p, self.config.output_path)
        elif p.is_dir():
            self._process_directory(p, self.config.output_path)
        self._print_stats()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _resolve_key(self, logger: BufferedLogger) -> bytes:
        if self.config.key is not None:
            logger.info(f"  Key (provided): {self.config.key.hex().upper()}")
            return self.config.key
        if self.config.password is not None:
            key = derive_key_from_password(self.config.password)
            logger.info(f"  Key (from password): {key.hex().upper()}")
            return key
        key = os.urandom(16)
        logger.info(f"  Key (random): {key.hex().upper()}")
        return key

    # ── File processing ───────────────────────────────────────────────────────

    def _process_file(
        self, input_file: Path, output_directory: Optional[Path]
    ) -> None:
        logger = BufferedLogger()
        logger.info(f"\nProcessing: {input_file.name}")

        try:
            if output_directory is None:
                raise RuntimeError("Output directory must be specified.")

            output_directory.mkdir(parents=True, exist_ok=True)

            file_length = input_file.stat().st_size
            if file_length == 0:
                raise RuntimeError("File is empty.")

            key = self._resolve_key(logger)
            output_path = output_directory / (input_file.name + ".eslock")
            original_name = input_file.name if self.config.store_name else None

            with open(input_file, "rb") as fin:
                try:
                    mode = "wb" if self.config.overwrite else "xb"
                    with open(output_path, mode) as fout:
                        used_partial = encrypt_stream(
                            fin,
                            fout,
                            key,
                            self.config.is_partial,
                            self.config.block_size,
                            file_length,
                        )
                        footer = build_footer(
                            key,
                            used_partial,
                            self.config.block_size,
                            original_name,
                        )
                        fout.write(footer)
                except FileExistsError:
                    raise RuntimeError(
                        f"Output file '{output_path.name}' already exists. "
                        "Use '--overwrite' to replace it."
                    )
                except OSError as exc:
                    raise RuntimeError(f"Failed to write output file: {exc}")

            if self.config.verbose:
                enc_type = (
                    f"Partial (first/last {self.config.block_size} bytes)"
                    if used_partial
                    else "Full"
                )
                logger.info(f"  File size: {file_length} bytes")
                logger.info(f"  Encryption: {enc_type}")
                if original_name:
                    logger.info(f"  Stored name: {original_name}")
                logger.info(f"  Output: {output_path}")

            if not used_partial and self.config.is_partial:
                logger.warning(
                    f"File too small for partial encryption "
                    f"(< {2 * self.config.block_size} bytes) — used full encryption."
                )
                self.stats.increment_warnings()

            logger.success(f"Encrypted: {input_file.name} -> {output_path.name}")
            self.stats.increment_files_encrypted()

        except Exception as exc:
            logger.error(str(exc))
            self.stats.increment_errors()
            self.stats.increment_files_skipped()
        finally:
            logger.flush()

    # ── Directory processing ──────────────────────────────────────────────────

    def _process_directory(
        self, input_dir: Path, output_directory: Optional[Path]
    ) -> None:
        print(f"\nProcessing directory: {input_dir}")

        source_files = [
            f for f in input_dir.rglob("*")
            if f.is_file() and f.suffix.lower() != ".eslock"
        ]

        if not source_files:
            print("  No files found.")
            return

        print(f"  Found {len(source_files)} file(s).")

        if output_directory is not None:
            output_directory.mkdir(parents=True, exist_ok=True)
            print(f"  Output directory: {output_directory}")

        def process_one(source_file: Path) -> None:
            if output_directory is not None:
                rel = source_file.relative_to(input_dir)
                target_dir = output_directory / rel.parent
                target_dir.mkdir(parents=True, exist_ok=True)
                self._process_file(source_file, target_dir)
            else:
                self._process_file(source_file, input_dir)

        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(process_one, f) for f in source_files]
            for future in as_completed(futures):
                future.result()

    # ── Stats output ──────────────────────────────────────────────────────────

    def _print_stats(self) -> None:
        s = self.stats
        print("\n" + "=" * 80)
        print("Processing complete.")
        print(f"  Files processed:  {s.files_processed}")
        print(f"  Files encrypted:  {s.files_encrypted}")
        print(f"  Files skipped:    {s.files_skipped}")
        print(f"  Warnings:         {s.warnings}")
        print(f"  Errors:           {s.errors}")
        print("=" * 80)


def _print_info() -> None:
    print("=" * 80)
    print("                                 ESLockEncryptor")
    print("=" * 80)
    print("       Tool for encrypting files into ES File Explorer .eslock format")
    print("                             ! FOR LEGAL USE ONLY !")
    print(f"              Version {VERSION} | (C) 2025-2026 | MIT License")
    print("=" * 80)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="eslock_encryptor",
        description=(
            "ESLockEncryptor — Encrypt files into ES File Explorer .eslock format"
        ),
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help="Path to input file or directory. Defaults to current directory if omitted.",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help=(
            "Destination directory for encrypted files. "
            "If omitted, a timestamped folder is created alongside the input."
        ),
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable detailed logging.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing .eslock files.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Use full-file encryption instead of partial (default: partial).",
    )
    parser.add_argument(
        "--block-size",
        dest="block_size",
        type=int,
        default=DEFAULT_BLOCK_SIZE,
        metavar="BYTES",
        help=(
            f"Number of bytes to encrypt at start and end in partial mode "
            f"(default: {DEFAULT_BLOCK_SIZE})."
        ),
    )
    parser.add_argument(
        "-p", "--password",
        default=None,
        help="Derive encryption key from password (same algorithm as ES File Explorer).",
    )
    parser.add_argument(
        "-k", "--key",
        default=None,
        help="Use a specific key (32-char hex). If omitted, a random key is generated per file.",
    )
    parser.add_argument(
        "--no-store-name",
        action="store_true",
        dest="no_store_name",
        help="Do not embed the original filename in the footer.",
    )

    args = parser.parse_args()

    # ── Validation ──────────────────────────────────────────────────────────

    if args.key is not None:
        if len(args.key) != 32 or not re.fullmatch(r"[0-9a-fA-F]+", args.key):
            parser.error("Key must be a 32-character hexadecimal string.")

    if args.key and args.password:
        parser.error("'--key' and '--password' cannot be used together.")

    if args.block_size <= 0:
        parser.error("Block size must be a positive integer.")

    # ── Resolve paths ────────────────────────────────────────────────────────

    exe_dir = Path(sys.argv[0]).resolve().parent
    input_path = Path(args.input).resolve() if args.input else exe_dir

    if not input_path.exists():
        parser.error(f"Input path does not exist: {input_path}")

    if args.output:
        output_path = Path(args.output).resolve()
    else:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base_dir = input_path.parent if input_path.is_file() else input_path
        output_path = base_dir / f"encrypted-{timestamp}"

    key_bytes = bytes.fromhex(args.key) if args.key else None

    config = EncryptionConfig(
        input_path=input_path,
        output_path=output_path,
        verbose=args.verbose,
        overwrite=args.overwrite,
        is_partial=not args.full,
        block_size=args.block_size,
        password=args.password,
        key=key_bytes,
        store_name=not args.no_store_name,
    )

    EslockEncryptProcessor(config).execute()
    return 0


if __name__ == "__main__":
    sys.exit(main())
