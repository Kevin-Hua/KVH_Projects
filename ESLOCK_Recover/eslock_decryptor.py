#!/usr/bin/env python3
"""
ESLockDecryptor — Forensic tool for recovering ES File Explorer encrypted files (.eslock)

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
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Optional

try:
    from Crypto.Cipher import AES
except ImportError:
    print("Error: pycryptodome is required. Install with: pip install pycryptodome")
    sys.exit(1)

VERSION = "1.0"
AES_IV = bytes(range(16))  # [0, 1, 2, ..., 15]


# ─── Models ───────────────────────────────────────────────────────────────────

@dataclass
class EslockFooter:
    footer_offset: int
    is_parsed_successfully: bool
    raw_data: bytes = b""
    is_partial_encryption: Optional[bool] = None
    encrypted_block_size: Optional[int] = None
    original_name_length: Optional[int] = None
    encrypted_original_name: Optional[bytes] = None
    stored_crc: Optional[int] = None
    calculated_crc: Optional[int] = None
    key: Optional[bytes] = None
    footer_length: Optional[int] = None

    @property
    def is_crc_valid(self) -> bool:
        return self.stored_crc is not None and self.stored_crc == self.calculated_crc


# ─── Configuration ────────────────────────────────────────────────────────────

class RawDecryptMode(Enum):
    AUTO = auto()
    FULL = auto()
    PARTIAL = auto()


@dataclass
class RawDecryptConfig:
    mode: RawDecryptMode = RawDecryptMode.AUTO
    encrypted_block_size: Optional[int] = None


@dataclass
class DecryptionConfig:
    original_file_length: int
    key: bytes
    is_partial_encryption: bool
    encrypted_block_size: Optional[int]
    is_file_truncated: bool

    @classmethod
    def create_full_encrypt(
        cls,
        original_file_length: int,
        key: bytes,
        is_file_truncated: bool = False,
    ) -> "DecryptionConfig":
        if len(key) != 16:
            raise ValueError("Invalid key length.")
        return cls(original_file_length, key, False, None, is_file_truncated)

    @classmethod
    def create_partial_encrypt(
        cls,
        original_file_length: int,
        key: bytes,
        encrypted_block_size: int = 1024,
        is_file_truncated: bool = False,
    ) -> "DecryptionConfig":
        if len(key) != 16:
            raise ValueError("Invalid key length.")
        if encrypted_block_size <= 0 or encrypted_block_size > original_file_length:
            raise ValueError("Invalid encrypted_block_size.")
        return cls(original_file_length, key, True, encrypted_block_size, is_file_truncated)


@dataclass
class ProcessingConfig:
    input_path: Path
    output_path: Optional[Path]
    verbose: bool
    overwrite: bool
    read_only: bool
    ignore_crc: bool
    password: Optional[str]
    key: Optional[bytes]
    heuristic: bool
    raw_decrypt_config: Optional[RawDecryptConfig]


# ─── Cryptography ─────────────────────────────────────────────────────────────

def derive_key_from_password(password: str) -> bytes:
    """Derive a 16-byte AES key from a password using MD5."""
    return hashlib.md5(password.encode("utf-8")).digest()[:16]


def _make_aes(key: bytes):
    """Create a fresh AES-CFB-128 cipher instance (IV = [0..15])."""
    return AES.new(key, AES.MODE_CFB, iv=AES_IV, segment_size=128)


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


def decrypt_stream(input_stream, output_stream, config: DecryptionConfig) -> None:
    """Decrypt an .eslock file stream to an output stream."""
    orig_len = config.original_file_length

    if config.is_partial_encryption:
        block_size = config.encrypted_block_size or 1024
        last_block_size = 0 if config.is_file_truncated else block_size
        middle_length = orig_len - (block_size + last_block_size)

        if block_size + last_block_size > orig_len:
            raise ValueError("Encrypted blocks are larger than the file length.")

        # First block (encrypted)
        first_block = input_stream.read(block_size)
        output_stream.write(_make_aes(config.key).decrypt(first_block))

        # Middle section (unencrypted, pass through)
        if middle_length > 0:
            _copy_bytes(input_stream, output_stream, middle_length)

        # Last block (encrypted)
        if last_block_size > 0:
            last_block = input_stream.read(last_block_size)
            output_stream.write(_make_aes(config.key).decrypt(last_block))

    else:
        # Full encryption — stream cipher, no reset between chunks
        cipher = _make_aes(config.key)
        buf_size = 65536
        remaining = orig_len
        while remaining > 0:
            chunk = input_stream.read(min(buf_size, remaining))
            if not chunk:
                break
            output_stream.write(cipher.decrypt(chunk))
            remaining -= len(chunk)


def decrypt_file_name(encrypted_name: bytes, key: bytes) -> str:
    """Decrypt the original filename stored in the footer."""
    name_length = len(encrypted_name)
    # Round up to nearest 16-byte boundary
    normalized_length = ((name_length - 1 >> 4) + 1) << 4
    buf = bytearray(normalized_length)
    buf[:name_length] = encrypted_name
    decrypted = _make_aes(key).decrypt(bytes(buf))
    return decrypted[:name_length].decode("utf-8")


# ─── Footer Utilities ─────────────────────────────────────────────────────────

def _read_file_tail(file_path: str, length: int) -> bytes:
    with open(file_path, "rb") as f:
        f.seek(-length, 2)
        return f.read(length)


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


# ─── Standard Footer Reader ───────────────────────────────────────────────────

def read_footer_standard(file_path: str) -> EslockFooter:
    """
    Fast footer reader for well-formed .eslock files.
    Reads the last 1024 bytes and parses the footer structure.
    """
    MAX_FOOTER = 1024
    MIN_FOOTER = 32

    file_length = os.path.getsize(file_path)
    if file_length < MIN_FOOTER:
        raise ValueError("File is too small to be a valid .eslock file.")

    tail_size = min(file_length, MAX_FOOTER)
    buffer = _read_file_tail(file_path, tail_size)

    (footer_length,) = struct.unpack_from(">i", buffer, len(buffer) - 4)
    if footer_length <= 0 or footer_length > MAX_FOOTER or footer_length >= file_length:
        raise ValueError(
            "Incorrect footer length. Use '--heuristic' to try to find a valid footer."
        )

    footer = buffer[-footer_length:]

    # Footer layout (from end):
    #   [^4:]     footer_length  (int32 BE)
    #   [^8:^4]   stored_crc     (uint32 BE) — ReadUInt64BE[^12:^4] cast to uint32 = [^8:^4]
    #   [^12:^8]  zeros padding  (4 bytes)
    #   [^13:^12] key postfix magic (1 byte)
    #   [^29:^13] key            (16 bytes)
    #   [^30:^29] key prefix magic 0x10 (1 byte)
    (stored_crc,) = struct.unpack_from(">I", footer, len(footer) - 8)
    calculated_crc = _crc32(footer[:-12])
    key = footer[-29:-13]

    pos = 0
    is_partial = footer[pos] != 0xFF
    pos += 1

    encrypted_block_size = None
    if is_partial:
        (encrypted_block_size,) = struct.unpack_from(">i", footer, pos)
        pos += 4

    name_length_byte = footer[pos]
    pos += 1

    original_name_length = None
    encrypted_original_name = None
    if name_length_byte != 255:
        original_name_length = name_length_byte
        if pos + original_name_length > len(footer):
            raise ValueError(
                "Encrypted name length is out of range. Try '--heuristic'."
            )
        encrypted_original_name = footer[pos : pos + original_name_length]
        pos += original_name_length

    return EslockFooter(
        footer_offset=file_length - footer_length,
        is_parsed_successfully=True,
        raw_data=bytes(footer),
        is_partial_encryption=is_partial,
        encrypted_block_size=encrypted_block_size,
        original_name_length=original_name_length,
        encrypted_original_name=encrypted_original_name,
        stored_crc=stored_crc,
        calculated_crc=calculated_crc,
        key=bytes(key),
        footer_length=footer_length,
    )


# ─── Heuristic Footer Reader ──────────────────────────────────────────────────

_SIGNATURES: list[bytes] = [
    bytes([0x04, 0x00, 0x00, 0x04, 0x00]),  # partial encryption — most cases
    bytes([0x04, 0x00, 0x02, 0x08, 0x00]),  # partial encryption — mp3 files
]


def _sequential_parse_footer(
    buffer: bytes, total_file_length: int, offset: int
) -> EslockFooter:
    """Attempt to parse a footer structure starting at `offset` within `buffer`."""
    pos = offset
    is_partial: Optional[bool] = None
    encrypted_block_size: Optional[int] = None
    original_name_length: Optional[int] = None
    encrypted_original_name: Optional[bytes] = None
    stored_crc: Optional[int] = None
    calculated_crc: Optional[int] = None
    key: Optional[bytes] = None
    footer_length: Optional[int] = None

    is_structure_valid = True
    is_truncated = False

    try:
        is_partial = buffer[pos] != 0xFF
        pos += 1

        if is_partial:
            (encrypted_block_size,) = struct.unpack_from(">i", buffer, pos)
            pos += 4

        name_length_byte = buffer[pos]
        pos += 1
        if name_length_byte != 255:
            original_name_length = name_length_byte
            encrypted_original_name = buffer[pos : pos + original_name_length]
            pos += original_name_length

        is_structure_valid = buffer[pos] == 0x10   # key prefix magic
        pos += 1
        key = buffer[pos : pos + 16]
        pos += 16
        is_structure_valid = is_structure_valid and (buffer[pos] in (0x00, 0x02))  # key postfix magic
        pos += 1

        calculated_crc = _crc32(buffer[offset:pos])

        is_structure_valid = is_structure_valid and all(
            b == 0x00 for b in buffer[pos : pos + 4]
        )
        pos += 4

        (stored_crc,) = struct.unpack_from(">I", buffer, pos)
        pos += 4
        (footer_length,) = struct.unpack_from(">i", buffer, pos)
        pos += 4

        is_structure_valid = is_structure_valid and ((pos - footer_length) == offset)

    except (IndexError, struct.error):
        is_truncated = True

    end = len(buffer) if is_truncated else pos
    raw_data = buffer[offset:end]

    return EslockFooter(
        footer_offset=total_file_length - len(buffer) + offset,
        is_parsed_successfully=is_structure_valid and not is_truncated,
        raw_data=raw_data,
        is_partial_encryption=is_partial,
        encrypted_block_size=encrypted_block_size,
        original_name_length=original_name_length,
        encrypted_original_name=encrypted_original_name if encrypted_original_name else None,
        stored_crc=stored_crc,
        calculated_crc=calculated_crc,
        key=bytes(key) if key else None,
        footer_length=footer_length,
    )


def _find_signature(buffer: bytes, signature: bytes) -> list[int]:
    sig_len = len(signature)
    positions = []
    for i in range(len(buffer) - sig_len, -1, -1):
        if buffer[i : i + sig_len] == signature:
            positions.append(i)
    return positions


def _find_candidates_by_signature(
    buffer: bytes, total_file_length: int
) -> list[EslockFooter]:
    candidates: list[EslockFooter] = []
    for sig in _SIGNATURES:
        for offset in _find_signature(buffer, sig):
            candidates.append(_sequential_parse_footer(buffer, total_file_length, offset))
    return candidates


def _find_candidates_by_structure(
    buffer: bytes, total_file_length: int
) -> list[EslockFooter]:
    MAX_FOOTER = 1024
    MIN_FOOTER = 32
    candidates: list[EslockFooter] = []

    for i in range(len(buffer) - 22, -1, -1):
        if buffer[i] != 0x10:
            continue  # key prefix magic
        if buffer[i + 17] not in (0x00, 0x02):
            continue  # key postfix magic
        if all(b == 0x00 for b in buffer[i + 1 : i + 17]):
            continue  # key cannot be all zeros
        if any(b != 0x00 for b in buffer[i + 18 : i + 22]):
            continue  # padding before CRC must be zero

        # Try with full-encryption flag (0xFF 0xFF = two bytes before key prefix)
        if i >= 2 and all(b == 0xFF for b in buffer[i - 2 : i]):
            candidate = _sequential_parse_footer(buffer, total_file_length, i - 2)
            if candidate.is_crc_valid or (
                candidate.raw_data
                and candidate.footer_length is not None
                and len(candidate.raw_data) == candidate.footer_length
            ):
                candidates.append(candidate)
                continue

        # Try using declared footer_length from the tail
        if i + 30 <= len(buffer):
            (declared_length,) = struct.unpack_from(">i", buffer, i + 26)
            if declared_length < MIN_FOOTER or declared_length > MAX_FOOTER:
                continue
            candidate = _sequential_parse_footer(
                buffer, total_file_length, i + 30 - declared_length
            )
            if candidate.is_crc_valid:
                candidates.append(candidate)

    return candidates


def read_footer_heuristic(file_path: str) -> EslockFooter:
    """
    Resilient footer reader for corrupted or truncated .eslock files.
    Searches the last 128 KB using signature and structure heuristics.
    """
    SEARCH_WINDOW = 128 * 1024
    file_length = os.path.getsize(file_path)
    buf_size = min(file_length, SEARCH_WINDOW)
    buffer = _read_file_tail(file_path, buf_size)

    candidates = _find_candidates_by_signature(buffer, file_length)
    candidates.extend(_find_candidates_by_structure(buffer, file_length))

    if not candidates:
        raise ValueError(
            "Footer not found. Extract key from a valid file and try '--raw-decrypt'."
        )

    candidates.sort(
        key=lambda c: (c.is_crc_valid, c.is_parsed_successfully, c.footer_offset),
        reverse=True,
    )
    return candidates[0]


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
        self.files_decrypted = 0
        self.files_skipped = 0
        self.errors = 0
        self.warnings = 0

    def increment_files_processed(self) -> None:
        with self._lock:
            self.files_processed += 1

    def increment_files_decrypted(self) -> None:
        with self._lock:
            self.files_decrypted += 1
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

class EslockProcessor:
    def __init__(self, config: ProcessingConfig) -> None:
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

    def _read_footer(self, file_path: Path) -> EslockFooter:
        if self.config.heuristic:
            return read_footer_heuristic(str(file_path))
        return read_footer_standard(str(file_path))

    def _get_key(self, footer: Optional[EslockFooter], logger: BufferedLogger) -> bytes:
        if self.config.key is not None:
            logger.info(f"Using provided key: {self.config.key.hex().upper()}")
            return self.config.key
        if self.config.password is not None:
            logger.info(f"Using provided password: {self.config.password}")
            return derive_key_from_password(self.config.password)
        if footer is not None and footer.key and len(footer.key) == 16:
            return footer.key
        raise RuntimeError("Key for decryption not found or corrupted.")

    def _get_decryption_config(
        self,
        file_path: Path,
        footer: Optional[EslockFooter],
        logger: BufferedLogger,
    ) -> DecryptionConfig:
        PARTIAL_DEFAULT = True
        BLOCK_DEFAULT = 1024

        file_length = file_path.stat().st_size
        key = self._get_key(footer, logger)
        raw = self.config.raw_decrypt_config

        is_partial_provided: Optional[bool] = None
        if raw is not None:
            if raw.mode == RawDecryptMode.PARTIAL:
                is_partial_provided = True
            elif raw.mode == RawDecryptMode.FULL:
                is_partial_provided = False

        if raw is not None:
            is_partial = (
                is_partial_provided
                if is_partial_provided is not None
                else (footer.is_partial_encryption if footer else PARTIAL_DEFAULT)
            )
            orig_len = footer.footer_offset if footer else file_length
            if is_partial:
                block_size = (
                    raw.encrypted_block_size
                    or (footer.encrypted_block_size if footer else None)
                    or BLOCK_DEFAULT
                )
                return DecryptionConfig.create_partial_encrypt(
                    orig_len, key, block_size, footer is None
                )
            else:
                return DecryptionConfig.create_full_encrypt(orig_len, key, footer is None)

        if footer is not None:
            is_partial = (
                footer.is_partial_encryption
                if footer.is_partial_encryption is not None
                else PARTIAL_DEFAULT
            )
            if is_partial:
                block_size = footer.encrypted_block_size or BLOCK_DEFAULT
                return DecryptionConfig.create_partial_encrypt(
                    footer.footer_offset, key, block_size, False
                )
            else:
                return DecryptionConfig.create_full_encrypt(
                    footer.footer_offset, key, False
                )

        raise ValueError("Not enough data to decrypt.")

    def _decrypt_file_to_disk(
        self,
        input_path: Path,
        output_path: Path,
        config: DecryptionConfig,
        logger: BufferedLogger,
    ) -> None:
        logger.info(f"Output path: {output_path}")
        with open(input_path, "rb") as fin:
            try:
                mode = "wb" if self.config.overwrite else "xb"
                with open(output_path, mode) as fout:
                    decrypt_stream(fin, fout, config)
            except FileExistsError:
                raise RuntimeError(
                    f"File '{output_path.name}' already exists in the output directory. "
                    "Use '--overwrite' to replace it."
                )
            except OSError as exc:
                raise RuntimeError(f"Failed to access output file: {exc}")

    @staticmethod
    def _log_metadata(
        file_size: int, footer: EslockFooter, logger: BufferedLogger
    ) -> None:
        unknown = "unknown"
        crc_status = "[MATCH]" if footer.is_crc_valid else "[MISMATCH]"
        stored = f"{footer.stored_crc:08X}" if footer.stored_crc is not None else unknown
        calc = f"{footer.calculated_crc:08X}" if footer.calculated_crc is not None else unknown
        fl = str(footer.footer_length) if footer.footer_length is not None else unknown

        if footer.is_partial_encryption is None:
            enc_type = unknown
        elif footer.is_partial_encryption:
            sz = (
                str(footer.encrypted_block_size)
                if footer.encrypted_block_size is not None
                else unknown
            )
            enc_type = f"Partial (encrypted first/last {sz} bytes)"
        else:
            enc_type = "Full"

        key_str = footer.key.hex().upper() if footer.key else unknown

        logger.info(f"  File size: {file_size} bytes")
        logger.info(f"  Footer offset: {footer.footer_offset} bytes")
        logger.info("Metadata:")
        logger.info(f"  Footer length: {fl} bytes")
        logger.info(f"  CRC check: {crc_status}")
        logger.info(f"    Stored CRC: {stored}")
        logger.info(f"    Calculated CRC: {calc}")
        logger.info(f"  Encryption: {enc_type}")
        logger.info(f"  Key: {key_str}")

    # ── File processing ───────────────────────────────────────────────────────

    def _process_file(
        self, input_file: Path, output_directory: Optional[Path]
    ) -> None:
        logger = BufferedLogger()
        logger.info(f"\nProcessing file: {input_file}")

        try:
            if self.config.read_only:
                footer = self._read_footer(input_file)
                self._log_metadata(input_file.stat().st_size, footer, logger)
                if not footer.is_crc_valid:
                    logger.warning("CRC check failed. Metadata may be corrupted.")
                    self.stats.increment_warnings()
                self.stats.increment_files_processed()
                return

            if output_directory is None:
                raise RuntimeError("Output directory must be specified.")

            output_directory.mkdir(parents=True, exist_ok=True)

            # Raw-decrypt mode (skip footer reading)
            if self.config.raw_decrypt_config is not None and not self.config.heuristic:
                dec_config = self._get_decryption_config(input_file, None, logger)
                output_path = output_directory / input_file.stem
                self._decrypt_file_to_disk(input_file, output_path, dec_config, logger)
                logger.success(f"File decrypted: {input_file.name}")
                self.stats.increment_files_decrypted()
                return

            footer = self._read_footer(input_file)
            dec_config = self._get_decryption_config(input_file, footer, logger)

            original_name: Optional[str] = None
            if (
                footer.encrypted_original_name
                and footer.original_name_length is not None
            ):
                try:
                    original_name = decrypt_file_name(
                        footer.encrypted_original_name, dec_config.key
                    )
                except Exception:
                    original_name = None

            if self.config.verbose:
                self._log_metadata(input_file.stat().st_size, footer, logger)
                if original_name:
                    logger.info(f"  Original file name: {original_name}")

            if not footer.is_crc_valid:
                if not self.config.ignore_crc:
                    raise RuntimeError(
                        "CRC check failed. Skipping file. Use '--ignore-crc' to bypass."
                    )
                logger.warning("CRC check failed. Metadata may be corrupted.")

            output_name = original_name or input_file.stem
            output_path = output_directory / output_name
            self._decrypt_file_to_disk(input_file, output_path, dec_config, logger)
            logger.success(f"File decrypted: {input_file.name}")
            self.stats.increment_files_decrypted()

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

        eslock_files = list(input_dir.rglob("*.eslock"))
        if not eslock_files:
            print("  No .eslock files found.")
            return

        print(f"  Found {len(eslock_files)} file(s).")

        if not self.config.read_only and output_directory is not None:
            output_directory.mkdir(parents=True, exist_ok=True)
            print(f"\nCreated output directory: {output_directory}")

        def process_one(eslock_file: Path) -> None:
            if not self.config.read_only and output_directory is not None:
                rel = eslock_file.relative_to(input_dir)
                target_dir = output_directory / rel.parent
                target_dir.mkdir(parents=True, exist_ok=True)
                self._process_file(eslock_file, target_dir)
            else:
                self._process_file(eslock_file, None)

        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(process_one, f) for f in eslock_files]
            for future in as_completed(futures):
                future.result()  # propagate unexpected exceptions

    # ── Output helpers ────────────────────────────────────────────────────────

    def _print_stats(self) -> None:
        s = self.stats
        print("\n" + "=" * 80)
        print("Processing complete.")
        print(f"  Files processed:  {s.files_processed}")
        print(f"  Files decrypted:  {s.files_decrypted}")
        print(f"  Files skipped:    {s.files_skipped}")
        print(f"  Warnings:         {s.warnings}")
        print(f"  Errors:           {s.errors}")
        print("=" * 80)


def _print_info() -> None:
    print("=" * 80)
    print("                                 ESLockDecryptor")
    print("=" * 80)
    print("     Forensic tool for recovering ES File Explorer encrypted files (.eslock)")
    print("                             ! FOR LEGAL USE ONLY !")
    print(f"              Version {VERSION} | (C) 2025-2026 | MIT License")
    print("=" * 80)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _parse_raw_decrypt_arg(value: Optional[str]) -> RawDecryptConfig:
    """Parse --raw-decrypt value: auto | full | partial[:size]"""
    if value is None:
        return RawDecryptConfig(RawDecryptMode.AUTO)

    parts = value.split(":", 1)
    mode_map = {
        "auto": RawDecryptMode.AUTO,
        "full": RawDecryptMode.FULL,
        "partial": RawDecryptMode.PARTIAL,
    }
    mode_str = parts[0].strip().lower()
    if mode_str not in mode_map:
        raise argparse.ArgumentTypeError(
            "Invalid value for '--raw-decrypt'. Allowed: auto, full, partial[:size]."
        )
    mode = mode_map[mode_str]

    if mode != RawDecryptMode.PARTIAL and len(parts) > 1:
        raise argparse.ArgumentTypeError(
            "Size can only be specified when mode is 'partial'."
        )

    size: Optional[int] = None
    if mode == RawDecryptMode.PARTIAL and len(parts) == 2:
        try:
            size = int(parts[1].strip())
            if size <= 0:
                raise ValueError()
        except ValueError:
            raise argparse.ArgumentTypeError(
                "Invalid size for '--raw-decrypt partial'. Size must be a positive integer."
            )

    return RawDecryptConfig(mode, size)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="eslock_decryptor",
        description=(
            "ESLockDecryptor — Forensic tool for recovering "
            "ES File Explorer encrypted files (.eslock)"
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
            "Destination directory. "
            "If omitted, a timestamped folder is created alongside the input."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable detailed logging.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing decrypted files.")
    parser.add_argument(
        "--read-only",
        action="store_true",
        dest="read_only",
        help="Only read and print metadata (no decryption).",
    )
    parser.add_argument(
        "--ignore-crc",
        action="store_true",
        dest="ignore_crc",
        help="Process even if the footer CRC check fails.",
    )
    parser.add_argument(
        "-p", "--password",
        default=None,
        help="Use provided password for decryption (ignore embedded key).",
    )
    parser.add_argument(
        "-k", "--key",
        default=None,
        help="Use provided key (32-char hex) for decryption (ignore embedded key).",
    )
    parser.add_argument(
        "--heuristic",
        action="store_true",
        help="Enable heuristic footer detection (for corrupted files).",
    )
    parser.add_argument(
        "--raw-decrypt",
        dest="raw_decrypt",
        nargs="?",
        const="auto",
        default=None,
        metavar="auto|full|partial[:size]",
        help="Enable raw decryption, ignoring metadata.",
    )

    args = parser.parse_args()

    # ── Validation ─────────────────────────────────────────────────────────

    if args.key is not None:
        if len(args.key) != 32 or not re.fullmatch(r"[0-9a-fA-F]+", args.key):
            parser.error("Key must be a 32-character hexadecimal string.")

    if args.read_only and args.output:
        parser.error(
            "'--read-only' cannot be used with an output path. "
            "Redirect stdout to save the log to a file instead."
        )
    if args.read_only and args.overwrite:
        parser.error("'--read-only' and '--overwrite' cannot be used together.")
    if args.read_only and args.raw_decrypt is not None:
        parser.error("'--read-only' and '--raw-decrypt' cannot be used together.")
    if args.key and args.password:
        parser.error("'--key' and '--password' cannot be used together.")
    if args.raw_decrypt is not None and not (args.key or args.password):
        parser.error("'--raw-decrypt' requires either '--key' or '--password'.")

    raw_config: Optional[RawDecryptConfig] = None
    if args.raw_decrypt is not None:
        try:
            raw_config = _parse_raw_decrypt_arg(args.raw_decrypt)
        except argparse.ArgumentTypeError as exc:
            parser.error(str(exc))

    # ── Resolve paths ───────────────────────────────────────────────────────

    exe_dir = Path(sys.argv[0]).resolve().parent
    input_path = Path(args.input).resolve() if args.input else exe_dir

    if not input_path.exists():
        parser.error(f"Input path does not exist: {input_path}")

    output_path: Optional[Path] = None
    if not args.read_only:
        if args.output:
            output_path = Path(args.output).resolve()
        else:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            base_dir = input_path.parent if input_path.is_file() else input_path.parent
            output_path = base_dir / f"decrypted-{timestamp}"

    key_bytes = bytes.fromhex(args.key) if args.key else None
    ignore_crc = bool(args.key or args.password or args.ignore_crc)

    config = ProcessingConfig(
        input_path=input_path,
        output_path=output_path,
        verbose=args.verbose,
        overwrite=args.overwrite,
        read_only=args.read_only,
        ignore_crc=ignore_crc,
        password=args.password,
        key=key_bytes,
        heuristic=args.heuristic,
        raw_decrypt_config=raw_config,
    )

    EslockProcessor(config).execute()
    return 0


if __name__ == "__main__":
    sys.exit(main())
