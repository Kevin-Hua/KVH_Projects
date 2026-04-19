#!/usr/bin/env python3
"""
KvhLocker — GUI tool for encrypting and decrypting .kvhlock files
Requires: pip install pycryptodome
"""

import hashlib
import hmac as _hmac
import io
import json
import os
import struct
import sys
import threading
import time
import traceback
import uuid
import lzma
import zlib
from datetime import datetime
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes
    from Crypto.Util.Padding import pad, unpad
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pycryptodome"])
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes
    from Crypto.Util.Padding import pad, unpad

VERSION   = "1.0"
COPYRIGHT = "\u00a9 2026 Rewolf_KVH"
AES_IV = bytes(range(16))
DEFAULT_BLOCK_SIZE = 1024
_PARTIAL_FLAG = 0x04

# ─── Crypto core (shared) ─────────────────────────────────────────────────────

def derive_key_from_password(password: str) -> bytes:
    return hashlib.md5(password.encode("utf-8")).digest()[:16]

def _make_aes(key: bytes):
    return AES.new(key, AES.MODE_CFB, iv=AES_IV, segment_size=128)

def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF

def _copy_bytes(src, dst, count: int) -> None:
    buf_size = 81920
    copied = 0
    while copied < count:
        chunk = src.read(min(buf_size, count - copied))
        if not chunk:
            break
        dst.write(chunk)
        copied += len(chunk)

# ─── Encryption ───────────────────────────────────────────────────────────────

def _encrypt_file_name(name: str, key: bytes) -> bytes:
    name_bytes = name.encode("utf-8")
    name_length = len(name_bytes)
    normalized_length = ((name_length - 1 >> 4) + 1) << 4
    buf = bytearray(normalized_length)
    buf[:name_length] = name_bytes
    return _make_aes(key).encrypt(bytes(buf))[:name_length]

def build_footer(key: bytes, is_partial: bool, encrypted_block_size: int,
                 original_name: Optional[str]) -> bytes:
    body = bytearray()
    if is_partial:
        body.append(_PARTIAL_FLAG)
        body += struct.pack(">i", encrypted_block_size)
    else:
        body.append(0xFF)
    if original_name:
        enc_name = _encrypt_file_name(original_name, key)
        name_length = len(enc_name)
        if name_length > 254:
            body.append(0xFF)
        else:
            body.append(name_length)
            body += enc_name
    else:
        body.append(0xFF)
    body.append(0x10)
    body += key
    body.append(0x00)
    crc = _crc32(bytes(body))
    body += b"\x00\x00\x00\x00"   # reserved — always zero (mobile-compatible)
    body += struct.pack(">I", crc)
    footer_length = len(body) + 4
    body += struct.pack(">i", footer_length)
    return bytes(body)

def encrypt_stream(input_stream, output_stream, key: bytes, is_partial: bool,
                   block_size: int, file_length: int) -> bool:
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

def encrypt_file(input_path: Path, output_path: Path, key: bytes,
                 is_partial: bool, block_size: int, store_name: bool,
                 overwrite: bool, preserve_date: bool = True) -> str:
    st = input_path.stat()
    file_length = st.st_size
    if file_length == 0:
        raise RuntimeError("File is empty.")
    original_name = input_path.name if store_name else None
    with open(input_path, "rb") as fin:
        mode = "wb" if overwrite else "xb"
        try:
            with open(output_path, mode) as fout:
                used_partial = encrypt_stream(fin, fout, key, is_partial, block_size, file_length)
                footer = build_footer(key, used_partial, block_size, original_name)
                fout.write(footer)
        except FileExistsError:
            raise RuntimeError(f"Output file already exists: {output_path.name}")
    if preserve_date:
        os.utime(output_path, (st.st_atime, st.st_mtime))
    enc_type = f"Partial ({block_size}B)" if used_partial else "Full"
    return enc_type

# ─── Decryption ───────────────────────────────────────────────────────────────

def read_footer_standard(file_path: Path):
    MAX_FOOTER = 1024
    MIN_FOOTER = 32
    file_length = file_path.stat().st_size
    if file_length < MIN_FOOTER:
        raise ValueError("File too small to be a valid .eslock file.")
    tail_size = min(file_length, MAX_FOOTER)
    with open(file_path, "rb") as f:
        f.seek(-tail_size, 2)
        buffer = f.read(tail_size)
    (footer_length,) = struct.unpack_from(">i", buffer, len(buffer) - 4)
    if footer_length <= 0 or footer_length > MAX_FOOTER or footer_length >= file_length:
        raise ValueError("Invalid footer length. Try heuristic mode.")
    footer = buffer[-footer_length:]
    (stored_crc,) = struct.unpack_from(">I", footer, len(footer) - 8)
    calculated_crc = _crc32(footer[:-12])
    key = bytes(footer[-29:-13])
    pos = 0
    is_partial = footer[pos] != 0xFF
    pos += 1
    encrypted_block_size = None
    if is_partial:
        (encrypted_block_size,) = struct.unpack_from(">i", footer, pos)
        pos += 4
    name_length_byte = footer[pos]
    pos += 1
    original_name = None
    if name_length_byte != 255:
        enc_name = footer[pos: pos + name_length_byte]
        pos += name_length_byte
        try:
            name_length = name_length_byte
            normalized_length = ((name_length - 1 >> 4) + 1) << 4
            buf = bytearray(normalized_length)
            buf[:name_length] = enc_name
            decrypted = _make_aes(key).decrypt(bytes(buf))
            original_name = decrypted[:name_length].decode("utf-8")
        except Exception:
            original_name = None
    # optional mtime extension: stored in the 4-byte slot at footer[-12:-8]
    # always zero — reserved for future use, kept zero for mobile compatibility
    return {
        "footer_offset": file_length - footer_length,
        "key": key,
        "is_partial": is_partial,
        "block_size": encrypted_block_size or DEFAULT_BLOCK_SIZE,
        "original_name": original_name,
        "stored_crc": stored_crc,
        "calculated_crc": calculated_crc,
        "crc_valid": stored_crc == calculated_crc,
        "footer_length": footer_length,
        "mtime": None,
    }

def decrypt_stream(input_stream, output_stream, is_partial: bool, block_size: int,
                   orig_len: int, key: bytes) -> None:
    if is_partial:
        first_block = input_stream.read(block_size)
        output_stream.write(_make_aes(key).decrypt(first_block))
        middle_length = orig_len - 2 * block_size
        if middle_length > 0:
            _copy_bytes(input_stream, output_stream, middle_length)
        last_block = input_stream.read(block_size)
        output_stream.write(_make_aes(key).decrypt(last_block))
    else:
        cipher = _make_aes(key)
        buf_size = 65536
        remaining = orig_len
        while remaining > 0:
            chunk = input_stream.read(min(buf_size, remaining))
            if not chunk:
                break
            output_stream.write(cipher.decrypt(chunk))
            remaining -= len(chunk)

def decrypt_file(input_path: Path, output_path: Path, key_override: Optional[bytes],
                 ignore_crc: bool, overwrite: bool):
    info = read_footer_standard(input_path)
    if not info["crc_valid"] and not ignore_crc:
        raise RuntimeError("CRC check failed. Enable 'Ignore CRC' to bypass.")
    key = key_override if key_override else info["key"]
    orig_len = info["footer_offset"]
    # mtime is embedded in the footer (robust); fall back to .eslock file's mtime if absent
    eslock_st = input_path.stat()
    embedded_mtime = info.get("mtime")
    restore_mtime = embedded_mtime if embedded_mtime is not None else eslock_st.st_mtime
    with open(input_path, "rb") as fin:
        mode = "wb" if overwrite else "xb"
        try:
            with open(output_path, mode) as fout:
                decrypt_stream(fin, fout, info["is_partial"], info["block_size"], orig_len, key)
        except FileExistsError:
            raise RuntimeError(f"Output file already exists: {output_path.name}")
    try:
        os.utime(output_path, (restore_mtime, restore_mtime))
    except OSError:
        pass
    return info

def verify_eslock(file_path: Path, key_override: Optional[bytes] = None) -> dict:
    """Verify an .eslock file by decrypting to memory and checking integrity."""
    info = read_footer_standard(file_path)
    key = key_override if key_override else info["key"]
    orig_len = info["footer_offset"]
    buf = io.BytesIO()
    with open(file_path, "rb") as fin:
        decrypt_stream(fin, buf, info["is_partial"], info["block_size"], orig_len, key)
    decrypted_size = buf.tell()
    info["decrypted_size"] = decrypted_size
    info["verify_ok"] = decrypted_size == orig_len
    return info


def kvh_verify_file(file_path: Path, password: str) -> dict:
    """Verify a KVH file by streaming the body and checking the HMAC tag.
    Returns dict with keys: hmac_ok, partial, block_size, compress, algo, original_size, filename.
    Raises ValueError on wrong password or corrupt header."""
    file_size = file_path.stat().st_size
    magic_mask, salt_mask, hdr_mask = _kvh_pw_masks(password)
    with open(file_path, "rb") as f:
        enc_magic = f.read(4)
        if bytes(a ^ b for a, b in zip(enc_magic, magic_mask)) != _kvh_MAGIC:
            raise ValueError("Wrong password or not a valid KVH file.")
        enc_salt_offset = f.read(1)[0]
        salt_split      = (enc_salt_offset ^ salt_mask) % (_kvh_SALT_SIZE - 1)
        salt_prefix     = f.read(salt_split)
        enc_hdr_offset  = f.read(1)[0]
        header_split    = enc_hdr_offset ^ hdr_mask
        header_prefix   = f.read(header_split)
        salt_suffix     = f.read(_kvh_SALT_SIZE - salt_split)
        master_salt     = salt_prefix + salt_suffix
        file_iv         = f.read(_kvh_IV_SIZE)
        header_iv       = f.read(_kvh_IV_SIZE)
        hlen            = struct.unpack("<I", f.read(_kvh_HLEN_SIZE))[0]
        header_suffix   = f.read(hlen - header_split)
        header_enc      = header_prefix + header_suffix

        master_key = kvh_derive_master_key(password, master_salt)
        file_key   = _kvh_file_key(master_key, file_iv)
        hmac_key   = _kvh_hmac_key(file_key)
        try:
            hdr_cipher   = AES.new(master_key, AES.MODE_CBC, header_iv)
            header_plain = unpad(hdr_cipher.decrypt(header_enc), AES.block_size)
        except Exception as exc:
            raise ValueError(f"Header decryption failed — wrong password? ({exc})")
        header = json.loads(header_plain.decode("utf-8"))

        body_start = f.tell()
        body_size  = file_size - body_start - _kvh_HMAC_SIZE
        if body_size < 0:
            raise ValueError("File truncated or not a valid KVH file.")

        mac = _hmac.new(hmac_key, digestmod=hashlib.sha256)
        remaining = body_size
        while remaining > 0:
            chunk = f.read(min(_kvh_CHUNK, remaining))
            if not chunk: break
            mac.update(chunk)
            remaining -= len(chunk)
        stored_mac  = f.read(_kvh_HMAC_SIZE)
        hmac_ok     = _hmac.compare_digest(mac.digest(), stored_mac)

    return {
        "hmac_ok":       hmac_ok,
        "partial":       header.get("partial", False),
        "block_size":    header.get("block_size", 0),
        "compress":      header.get("compress_middle", False),
        "compress_algo": header.get("compress_algo", "zlib"),
        "original_size": header.get("original_size", 0),
        "filename":      header.get("filename", ""),
    }


# ─── KVH Crypto Core ─────────────────────────────────────────────────────────

_kvh_DEFAULT_PW = ",1D'&xTl1A=.X28]]hGQ{VWZ$'2r-@vJ;PyH4vb-,{9Z,4FJa;"

_kvh_MAGIC        = b"_KVH"
_kvh_SALT_SIZE    = 16
_kvh_IV_SIZE      = 16
_kvh_KEY_SIZE     = 16
_kvh_HLEN_SIZE    = 4
_kvh_HMAC_SIZE    = 32
_kvh_CHUNK        = 65_536
_kvh_PBKDF2_ITER  = 600_000
_kvh_MIN_PW_LEN   = 32
_kvh_DEFAULT_BLOCK = 1_048_576   # 1 MB
_kvh_NOCOMPRESS_EXTS = {
    # Video
    ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".m2ts",
    # Audio
    ".mp3", ".aac", ".ogg", ".flac", ".m4a", ".wma", ".opus",
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif",
    # Archives
    ".zip", ".7z", ".rar", ".gz", ".bz2", ".xz", ".zst", ".lz4",
    # Office (already zip-based)
    ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
    # Other pre-compressed
    ".epub", ".apk", ".ipa",
}


def _kvh_extend_password(pw: str) -> str:
    if len(pw) >= _kvh_MIN_PW_LEN:
        return pw
    extended, counter = pw, 0
    while len(extended) < _kvh_MIN_PW_LEN:
        extended += _hmac.new(
            pw.encode("utf-8"), counter.to_bytes(4, "big"), hashlib.sha256
        ).hexdigest()
        counter += 1
    return extended[:_kvh_MIN_PW_LEN]


def kvh_derive_master_key(password: str, master_salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        _kvh_extend_password(password).encode("utf-8"),
        master_salt,
        iterations=_kvh_PBKDF2_ITER,
        dklen=_kvh_KEY_SIZE,
    )


def _kvh_file_key(master_key: bytes, file_iv: bytes) -> bytes:
    return hashlib.sha256(master_key + file_iv).digest()[:_kvh_KEY_SIZE]


def _kvh_hmac_key(file_key: bytes) -> bytes:
    return hashlib.sha256(file_key + b"\x01").digest()


def _kvh_pw_masks(password: str) -> tuple:
    h = hashlib.sha256(password.encode("utf-8")).digest()
    return h[0:4], h[4], h[5]


# ─── KVH Middle Compression Helpers ─────────────────────────────────────────
# compress_mode: None/"full"/"partial"/"block"
# compress_n   : block / partial N value
# compress_algo: "zlib" | "lzma" | "lzma2"

_LZMA_FMT = {"lzma": lzma.FORMAT_ALONE, "lzma2": lzma.FORMAT_XZ}

def _compress_chunk(data: bytes, algo: str) -> bytes:
    if algo in _LZMA_FMT:
        return lzma.compress(data, format=_LZMA_FMT[algo])
    return zlib.compress(data, level=1)

def _decompress_chunk(data: bytes, algo: str) -> bytes:
    if algo in _LZMA_FMT:
        return lzma.decompress(data, format=_LZMA_FMT[algo])
    return zlib.decompress(data)

def _compressobj(algo: str):
    if algo in _LZMA_FMT:
        return lzma.LZMACompressor(format=_LZMA_FMT[algo])
    return zlib.compressobj(level=1)

def _decompressobj(algo: str):
    if algo in _LZMA_FMT:
        return lzma.LZMADecompressor(format=_LZMA_FMT[algo])
    return zlib.decompressobj()

def _kvh_mid_compress(data: bytes, mode: str, n: int, algo: str = "zlib") -> bytes:
    """Encode middle section according to compression mode."""
    if not data:
        return data
    if mode == "full":
        return _compress_chunk(data, algo)
    elif mode == "partial":
        # [prefix_size:4B][prefix][inner_comp_size:4B][inner_comp][suffix_size:4B][suffix]
        plen = min(n, len(data))
        slen = min(n, max(0, len(data) - plen))
        prefix = data[:plen]
        suffix = data[len(data) - slen:] if slen > 0 else b""
        inner  = data[plen: len(data) - slen if slen > 0 else len(data)]
        inner_comp = _compress_chunk(inner, algo) if inner else b""
        out = struct.pack("<I", plen) + prefix
        out += struct.pack("<I", len(inner_comp)) + inner_comp
        out += struct.pack("<I", slen) + suffix
        return out
    elif mode == "block":
        # [num_chunks:4B] then for each: [size:4B][data]
        # even chunks: compressed; odd chunks: raw
        chunks = [data[i:i+n] for i in range(0, len(data), n)]
        out = struct.pack("<I", len(chunks))
        for i, chunk in enumerate(chunks):
            enc = _compress_chunk(chunk, algo) if i % 2 == 0 else chunk
            out += struct.pack("<I", len(enc)) + enc
        return out
    return data


def _kvh_mid_decompress(data: bytes, mode: str, n: int, algo: str = "zlib") -> bytes:
    """Decode middle section according to compression mode."""
    if not data:
        return data
    if mode == "full":
        return _decompress_chunk(data, algo)
    elif mode == "partial":
        pos = 0
        plen = struct.unpack_from("<I", data, pos)[0]; pos += 4
        prefix = data[pos:pos + plen]; pos += plen
        ilen = struct.unpack_from("<I", data, pos)[0]; pos += 4
        inner = _decompress_chunk(data[pos:pos + ilen], algo) if ilen > 0 else b""; pos += ilen
        slen = struct.unpack_from("<I", data, pos)[0]; pos += 4
        suffix = data[pos:pos + slen]
        return prefix + inner + suffix
    elif mode == "block":
        pos = 0
        num = struct.unpack_from("<I", data, pos)[0]; pos += 4
        out = bytearray()
        for i in range(num):
            size = struct.unpack_from("<I", data, pos)[0]; pos += 4
            chunk = data[pos:pos + size]; pos += size
            out += _decompress_chunk(chunk, algo) if i % 2 == 0 else chunk
        return bytes(out)
    return data


def kvh_encrypt_file(
    input_path: Path,
    output_path: Path,
    master_key: bytes,
    master_salt: bytes,
    password: str,
    is_partial: bool = False,
    block_size: int = _kvh_DEFAULT_BLOCK,
    overwrite: bool = False,
    preserve_date: bool = True,
    compress_mode: str = None,
    compress_n: int = 65536,
    compress_algo: str = "zlib",
    skip_compress_exts: set = None,
    progress_cb=None,          # optional callable(bytes_done: int, total: int)
) -> str:
    file_size = input_path.stat().st_size
    if file_size == 0:
        raise RuntimeError("File is empty.")
    use_partial = is_partial and file_size >= 2 * block_size
    # Skip compression if extension is in the skip list
    _ext = input_path.suffix.lower()
    _skip = skip_compress_exts is not None and _ext in skip_compress_exts
    eff_compress = compress_mode if (compress_mode and use_partial and not _skip) else None
    _done = [0]  # mutable counter for nested helpers
    def _tick(n: int):
        if progress_cb:
            _done[0] += n
            progress_cb(_done[0], file_size)

    file_iv  = get_random_bytes(_kvh_IV_SIZE)
    file_key = _kvh_file_key(master_key, file_iv)
    hmac_key = _kvh_hmac_key(file_key)

    src_stat = input_path.stat()
    header_plain = json.dumps({
        "filename":      input_path.name,
        "original_size": file_size,
        "partial":       use_partial,
        "block_size":    block_size if use_partial else 0,
        "mtime":         src_stat.st_mtime if preserve_date else None,
        "atime":         src_stat.st_atime if preserve_date else None,
        "compress_middle": eff_compress or False,
        "compress_n":    compress_n if eff_compress and eff_compress != "full" else 0,
        "compress_algo":  compress_algo if eff_compress else "zlib",
    }, ensure_ascii=False).encode("utf-8")
    header_iv  = get_random_bytes(_kvh_IV_SIZE)
    hdr_cipher = AES.new(master_key, AES.MODE_CBC, header_iv)
    header_enc = hdr_cipher.encrypt(pad(header_plain, AES.block_size))

    magic_mask, salt_mask, hdr_mask = _kvh_pw_masks(password)
    enc_magic        = bytes(a ^ b for a, b in zip(_kvh_MAGIC, magic_mask))
    salt_split       = int.from_bytes(get_random_bytes(1), "big") % (_kvh_SALT_SIZE - 1)
    enc_salt_offset  = salt_split ^ salt_mask
    header_split     = int.from_bytes(get_random_bytes(1), "big") % len(header_enc)
    enc_hdr_offset   = header_split ^ hdr_mask

    open_mode = "wb" if overwrite else "xb"
    mac = _hmac.new(hmac_key, digestmod=hashlib.sha256)
    try:
        with open(input_path, "rb") as fin, open(output_path, open_mode) as fout:
            fout.write(enc_magic)
            fout.write(bytes([enc_salt_offset]))
            fout.write(master_salt[:salt_split])
            fout.write(bytes([enc_hdr_offset]))
            fout.write(header_enc[:header_split])
            fout.write(master_salt[salt_split:])
            fout.write(file_iv)
            fout.write(header_iv)
            fout.write(struct.pack("<I", len(header_enc)))
            fout.write(header_enc[header_split:])

            if use_partial:
                head = fin.read(block_size)
                enc_head = AES.new(file_key, AES.MODE_CTR, nonce=file_iv[:8]).encrypt(head)
                mac.update(enc_head); fout.write(enc_head)
                _tick(block_size)
                middle_size = file_size - 2 * block_size
                if eff_compress == "block":
                    # Streaming: never loads entire middle into RAM
                    num_chunks = (middle_size + compress_n - 1) // compress_n if middle_size > 0 else 0
                    hdr4 = struct.pack("<I", num_chunks)
                    mac.update(hdr4); fout.write(hdr4)
                    remaining = middle_size
                    for i in range(num_chunks):
                        raw = fin.read(min(compress_n, remaining))
                        remaining -= len(raw)
                        if i % 2 == 0:
                            if progress_cb: progress_cb(-1, file_size)  # signal: compressing
                            data = _compress_chunk(raw, compress_algo)
                        else:
                            data = raw
                        entry = struct.pack("<I", len(data)) + data
                        mac.update(entry); fout.write(entry)
                        _tick(len(raw))
                elif eff_compress == "full":
                    # Streaming compress
                    cobj = _compressobj(compress_algo)
                    remaining = middle_size
                    while remaining > 0:
                        raw = fin.read(min(_kvh_CHUNK, remaining))
                        if not raw: break
                        remaining -= len(raw)
                        data = cobj.compress(raw)
                        if data: mac.update(data); fout.write(data)
                        _tick(len(raw))
                    flush = cobj.flush()
                    if flush: mac.update(flush); fout.write(flush)
                elif eff_compress == "partial":
                    # Must buffer inner to get its compressed size; prefix/suffix are small
                    middle_plain = fin.read(middle_size)
                    mid_data = _kvh_mid_compress(middle_plain, "partial", compress_n, compress_algo)
                    mac.update(mid_data); fout.write(mid_data)
                    _tick(middle_size)
                else:
                    # No compression: stream through without buffering
                    remaining = middle_size
                    while remaining > 0:
                        raw = fin.read(min(_kvh_CHUNK, remaining))
                        if not raw: break
                        mac.update(raw); fout.write(raw)
                        remaining -= len(raw)
                        _tick(len(raw))
                tail = fin.read(block_size)
                enc_tail = AES.new(file_key, AES.MODE_CTR, nonce=file_iv[8:16]).encrypt(tail)
                mac.update(enc_tail); fout.write(enc_tail)
                _tick(block_size)
            else:
                body_cipher = AES.new(file_key, AES.MODE_CTR, nonce=file_iv[:8])
                remaining = file_size
                while remaining > 0:
                    chunk = fin.read(min(_kvh_CHUNK, remaining))
                    if not chunk: break
                    enc = body_cipher.encrypt(chunk)
                    mac.update(enc); fout.write(enc)
                    remaining -= len(chunk)
                    _tick(len(chunk))
            fout.write(mac.digest())
    except FileExistsError:
        raise RuntimeError(f"Output file already exists: {output_path.name}")
    except Exception:
        try: output_path.unlink(missing_ok=True)
        except OSError: pass
        raise
    # Restore original timestamps on the encrypted output file
    if preserve_date:
        os.utime(output_path, (src_stat.st_atime, src_stat.st_mtime))
    if use_partial:
        if eff_compress:
            return f"Partial ({block_size}B) + {eff_compress}/{compress_algo}"
        elif _skip:
            return f"Partial ({block_size}B) skip-compress ({_ext})"
        else:
            return f"Partial ({block_size}B)"
    return "Full"


def kvh_decrypt_file(
    input_path: Path,
    output_path: Path,
    password: str,
    overwrite: bool = False,
    precomputed_master_key: Optional[bytes] = None,
) -> dict:
    file_size = input_path.stat().st_size
    magic_mask, salt_mask, hdr_mask = _kvh_pw_masks(password)
    with open(input_path, "rb") as f:
        enc_magic = f.read(4)
        if bytes(a ^ b for a, b in zip(enc_magic, magic_mask)) != _kvh_MAGIC:
            raise ValueError("Wrong password or not a valid KVH file.")
        enc_salt_offset = f.read(1)[0]
        salt_split      = (enc_salt_offset ^ salt_mask) % (_kvh_SALT_SIZE - 1)
        salt_prefix     = f.read(salt_split)
        enc_hdr_offset  = f.read(1)[0]
        header_split    = enc_hdr_offset ^ hdr_mask
        header_prefix   = f.read(header_split)
        salt_suffix     = f.read(_kvh_SALT_SIZE - salt_split)
        master_salt     = salt_prefix + salt_suffix
        file_iv         = f.read(_kvh_IV_SIZE)
        header_iv       = f.read(_kvh_IV_SIZE)
        hlen            = struct.unpack("<I", f.read(_kvh_HLEN_SIZE))[0]
        header_suffix   = f.read(hlen - header_split)
        header_enc      = header_prefix + header_suffix

        master_key = precomputed_master_key or kvh_derive_master_key(password, master_salt)
        file_key   = _kvh_file_key(master_key, file_iv)
        hmac_key   = _kvh_hmac_key(file_key)
        try:
            hdr_cipher   = AES.new(master_key, AES.MODE_CBC, header_iv)
            header_plain = unpad(hdr_cipher.decrypt(header_enc), AES.block_size)
        except Exception as exc:
            raise ValueError(f"Header decryption failed — wrong password? ({exc})")
        header        = json.loads(header_plain.decode("utf-8"))
        original_name = header["filename"]
        original_size = header["original_size"]
        use_partial    = header.get("partial", False)
        block_size     = header.get("block_size", 0)
        orig_mtime     = header.get("mtime")
        orig_atime     = header.get("atime")
        compress_mid   = header.get("compress_middle", False)
        # Backward compat: old files stored True (bool) instead of mode string
        if compress_mid is True:
            compress_mid = "full"
        compress_n     = header.get("compress_n", 65536) or 65536
        compress_algo  = header.get("compress_algo", "zlib") or "zlib"

        body_start = f.tell()
        body_size  = file_size - body_start - _kvh_HMAC_SIZE
        if body_size < 0:
            raise ValueError("File truncated or not a valid KVH file.")

        open_mode    = "wb" if overwrite else "xb"
        mac          = _hmac.new(hmac_key, digestmod=hashlib.sha256)
        file_created = False
        try:
            with open(output_path, open_mode) as fout:
                file_created = True
                if use_partial and block_size > 0:
                    enc_head = f.read(block_size)
                    mac.update(enc_head)
                    fout.write(AES.new(file_key, AES.MODE_CTR, nonce=file_iv[:8]).decrypt(enc_head))
                    middle_size = body_size - 2 * block_size
                    if compress_mid == "block":
                        # Streaming decode
                        raw4 = f.read(4); mac.update(raw4)
                        num = struct.unpack_from("<I", raw4)[0]
                        for i in range(num):
                            sz4 = f.read(4); mac.update(sz4)
                            sz  = struct.unpack_from("<I", sz4)[0]
                            chunk = f.read(sz); mac.update(chunk)
                            fout.write(_decompress_chunk(chunk, compress_algo) if i % 2 == 0 else chunk)
                    elif compress_mid == "full":
                        # Streaming decompress
                        dobj = _decompressobj(compress_algo)
                        remaining = middle_size
                        while remaining > 0:
                            chunk = f.read(min(_kvh_CHUNK, remaining))
                            if not chunk: break
                            mac.update(chunk)
                            remaining -= len(chunk)
                            fout.write(dobj.decompress(chunk))
                        tail_data = dobj.flush() if hasattr(dobj, 'flush') else b""
                        if tail_data: fout.write(tail_data)
                    elif compress_mid == "partial":
                        middle_data = f.read(middle_size)
                        mac.update(middle_data)
                        fout.write(_kvh_mid_decompress(middle_data, "partial", compress_n, compress_algo))
                    else:
                        remaining = middle_size
                        while remaining > 0:
                            chunk = f.read(min(_kvh_CHUNK, remaining))
                            if not chunk: break
                            mac.update(chunk)
                            fout.write(chunk)
                            remaining -= len(chunk)
                    enc_tail = f.read(block_size)
                    mac.update(enc_tail)
                    fout.write(AES.new(file_key, AES.MODE_CTR, nonce=file_iv[8:16]).decrypt(enc_tail))
                else:
                    body_cipher = AES.new(file_key, AES.MODE_CTR, nonce=file_iv[:8])
                    remaining = body_size
                    while remaining > 0:
                        chunk = f.read(min(_kvh_CHUNK, remaining))
                        if not chunk: break
                        mac.update(chunk)
                        fout.write(body_cipher.decrypt(chunk))
                        remaining -= len(chunk)
                stored_mac = f.read(_kvh_HMAC_SIZE)
                if not _hmac.compare_digest(mac.digest(), stored_mac):
                    raise ValueError("HMAC verification failed — file corrupted or tampered.")
        except FileExistsError:
            raise RuntimeError(f"Output file already exists: {output_path.name}")
        except Exception:
            if file_created and output_path.exists():
                try: output_path.unlink()
                except OSError: pass
            raise
    # Restore original timestamps if stored in header
    if orig_mtime is not None:
        try:
            atime = orig_atime if orig_atime is not None else orig_mtime
            os.utime(output_path, (atime, orig_mtime))
        except OSError:
            pass
    return {
        "original_name": original_name,
        "original_size": original_size,
        "master_salt":   master_salt,
        "master_key":    master_key,
        "mtime":         orig_mtime,
    }


def kvh_peek_salt(file_path: Path, password: str) -> bytes:
    magic_mask, salt_mask, hdr_mask = _kvh_pw_masks(password)
    with open(file_path, "rb") as f:
        enc_magic = f.read(4)
        if bytes(a ^ b for a, b in zip(enc_magic, magic_mask)) != _kvh_MAGIC:
            raise ValueError("Wrong password or not a valid KVH file.")
        enc_salt_offset = f.read(1)[0]
        salt_split      = (enc_salt_offset ^ salt_mask) % (_kvh_SALT_SIZE - 1)
        salt_prefix     = f.read(salt_split)
        enc_hdr_offset  = f.read(1)[0]
        header_split    = enc_hdr_offset ^ hdr_mask
        f.read(header_split)
        salt_suffix     = f.read(_kvh_SALT_SIZE - salt_split)
    return salt_prefix + salt_suffix


# ─── Theme ────────────────────────────────────────────────────────────────────

BG       = "#0f1117"
SURFACE  = "#1a1d27"
SURFACE2 = "#22263a"
BORDER   = "#2e3350"
ACCENT   = "#4f8ef7"
ACCENT2  = "#a78bfa"
GREEN    = "#34d399"
YELLOW   = "#fbbf24"
RED      = "#f87171"
TEXT     = "#e2e8f0"
MUTED    = "#94a3b8"
FONT     = ("Segoe UI", 10)
FONT_B   = ("Segoe UI", 10, "bold")
FONT_S   = ("Segoe UI", 9)
MONO     = ("Cascadia Code", 9)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _set_frame_state(frame: ttk.Frame, state: str) -> None:
    """Recursively set state on all leaf widgets inside a frame, skipping labels."""
    for child in frame.winfo_children():
        # Labels don't support state and show a jarring system highlight when forced
        if not isinstance(child, (ttk.Label, tk.Label)):
            try:
                child.configure(state=state)
            except tk.TclError:
                pass
        if child.winfo_children():
            _set_frame_state(child, state)


def _fmt_size(n: int) -> str:
    """Format a byte count with auto unit: B / KB / MB / GB."""
    if n < 100 * 1024:
        return f"{n:,} B"
    if n < 100 * 1024 * 1024:
        return f"{n / 1024:,.1f} KB"
    if n < 10 * 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):,.1f} MB"
    return f"{n / (1024 * 1024 * 1024):,.2f} GB"


def _key_mismatch_msg(key_override: bytes, embedded_key: bytes) -> Optional[str]:
    """Return a warning string if key_override differs from the embedded key, else None."""
    if key_override != embedded_key:
        return (
            f"KEY MISMATCH — supplied key ({key_override.hex().upper()}) "
            f"differs from embedded key ({embedded_key.hex().upper()}). "
            "Wrong password / key — output will be garbage."
        )
    return None


# ─── Settings persistence ─────────────────────────────────────────────────────

_SETTINGS_FILE = Path(__file__).with_name("kvhlocker_settings.json")

_SETTINGS_DEFAULTS: dict = {
    # Encrypt
    "enc_format":      "eslock",
    "enc_input_mode":  "folder",
    "enc_input":       "",
    "enc_output":      "",
    "enc_password":    "",
    "enc_partial":     True,
    "enc_compress_mid": False,
    "enc_compress_mode": "full",
    "enc_compress_n":  "64",
    "enc_compress_n_unit": "KB",
    "enc_compress_algo": "zlib",
    "enc_compress_skip": True,
    "enc_compress_skip_exts": " ".join(sorted(_kvh_NOCOMPRESS_EXTS)),
    "enc_blocksize":   str(DEFAULT_BLOCK_SIZE),
    "enc_store_name":  True,
    "enc_preserve_date": True,
    "enc_aef_name":    "timestamp",
    "enc_overwrite":   False,
    # Decrypt
    "dec_format":      "eslock",
    "dec_input_mode":  "folder",
    "dec_input":       "",
    "dec_output":      "",
    "dec_key_mode":    "embedded",
    "dec_ignore_crc":  False,
    "dec_overwrite":   False,
    "dec_heuristic":   False,
    # Verify
    "ver_format":     "eslock",
    "ver_input_mode":  "folder",
    "ver_input":       "",
    "ver_key_mode":    "embedded",
}


def _load_settings() -> dict:
    try:
        if _SETTINGS_FILE.exists():
            data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
            return {**_SETTINGS_DEFAULTS, **data}
    except Exception:
        pass
    return dict(_SETTINGS_DEFAULTS)


def _save_settings(data: dict) -> None:
    try:
        _SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


# ─── GUI ──────────────────────────────────────────────────────────────────────

class ESLockIDE(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"KvhLocker  v{VERSION}")
        self.geometry("900x820")
        self.minsize(800, 700)
        self.configure(bg=BG)
        self._apply_style()
        self._settings = _load_settings()
        self._build_ui()
        self._restore_settings()
        self._log_welcome()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self._persist_settings()
        self.destroy()

    def _persist_settings(self):
        s = self._settings
        # Encrypt
        s["enc_format"]     = self._enc_format.get()
        s["enc_input_mode"] = self._enc_input_mode.get()
        s["enc_input"]      = self._enc_input.get()
        s["enc_output"]     = self._enc_output.get()
        s["enc_password"]   = self._enc_aef_password.get()
        s["enc_partial"]    = self._enc_partial.get()
        s["enc_compress_mid"] = self._enc_compress_mid.get()
        s["enc_compress_mode"] = self._enc_compress_mode.get()
        s["enc_compress_n"]  = self._enc_compress_n.get()
        s["enc_compress_n_unit"] = self._enc_compress_n_unit.get()
        s["enc_compress_algo"] = self._enc_compress_algo.get()
        s["enc_compress_skip"] = self._enc_compress_skip.get()
        s["enc_compress_skip_exts"] = self._enc_compress_skip_exts.get()
        s["enc_blocksize"]  = self._enc_blocksize.get()
        s["enc_store_name"] = self._enc_store_name.get()
        s["enc_preserve_date"] = self._enc_preserve_date.get()
        s["enc_aef_name"]    = self._enc_aef_name.get()
        s["enc_overwrite"]  = self._enc_overwrite.get()
        # Decrypt
        s["dec_input_mode"] = self._dec_input_mode.get()
        s["dec_input"]      = self._dec_input.get()
        s["dec_output"]     = self._dec_output.get()
        s["dec_key_mode"]   = self._dec_key_mode.get()
        s["dec_format"]      = self._dec_format.get()
        s["dec_ignore_crc"] = self._dec_ignore_crc.get()
        s["dec_overwrite"]  = self._dec_overwrite.get()
        s["dec_heuristic"]  = self._dec_heuristic.get()
        # Verify
        s["ver_format"]     = self._ver_format.get()
        s["ver_input_mode"] = self._ver_input_mode.get()
        s["ver_input"]      = self._ver_input.get()
        s["ver_key_mode"]   = self._ver_key_mode.get()
        _save_settings(s)

    def _restore_settings(self):
        s = self._settings
        # Encrypt
        self._enc_input_mode.set(s["enc_input_mode"]); self._enc_mode_changed()
        self._enc_input.set(s["enc_input"])
        self._enc_output.set(s["enc_output"])
        self._enc_aef_password.set(s["enc_password"])
        self._enc_partial.set(s["enc_partial"])
        self._enc_compress_mid.set(s["enc_compress_mid"])
        self._enc_compress_mode.set(s["enc_compress_mode"])
        self._enc_compress_n.set(s["enc_compress_n"])
        self._enc_compress_n_unit.set(s["enc_compress_n_unit"])
        self._enc_compress_algo.set(s["enc_compress_algo"])
        self._enc_compress_skip.set(s["enc_compress_skip"])
        self._enc_compress_skip_exts.set(s["enc_compress_skip_exts"])
        self._enc_blocksize.set(s["enc_blocksize"])
        self._enc_store_name.set(s["enc_store_name"])
        self._enc_preserve_date.set(s["enc_preserve_date"])
        self._enc_aef_name.set(s["enc_aef_name"])
        self._enc_overwrite.set(s["enc_overwrite"])
        self._enc_format.set(s["enc_format"]); self._enc_format_changed()  # replaces key_mode_changed
        # Decrypt
        self._dec_input_mode.set(s["dec_input_mode"]); self._dec_mode_changed()
        self._dec_input.set(s["dec_input"])
        self._dec_output.set(s["dec_output"])
        self._dec_key_mode.set(s["dec_key_mode"])
        self._dec_ignore_crc.set(s["dec_ignore_crc"])
        self._dec_overwrite.set(s["dec_overwrite"])
        self._dec_heuristic.set(s["dec_heuristic"])
        self._dec_format.set(s["dec_format"]); self._dec_format_changed()  # replaces key_mode_changed
        # Verify
        self._ver_input_mode.set(s["ver_input_mode"]); self._ver_mode_changed()
        self._ver_input.set(s["ver_input"])
        self._ver_key_mode.set(s["ver_key_mode"]); self._ver_key_mode_changed()
        self._ver_format.set(s["ver_format"]); self._ver_format_changed()

    # ── Style ─────────────────────────────────────────────────────────────────

    def _apply_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".",            background=BG,      foreground=TEXT,    font=FONT)
        style.configure("TFrame",       background=BG)
        style.configure("Surface.TFrame", background=SURFACE)
        style.configure("TLabel",       background=BG,      foreground=TEXT,    font=FONT)
        style.map("TLabel",
                  background=[("disabled", BG), ("!disabled", BG)],
                  foreground=[("disabled", BORDER), ("!disabled", TEXT)])
        style.configure("Muted.TLabel", background=BG,      foreground=MUTED,   font=FONT_S)
        style.configure("Surface.TLabel", background=SURFACE, foreground=TEXT,  font=FONT)
        style.configure("Header.TLabel",  background=BG,    foreground=ACCENT,  font=("Segoe UI", 12, "bold"))
        style.configure("TNotebook",    background=BG,      borderwidth=0)
        style.configure("TNotebook.Tab", background=SURFACE2, foreground=MUTED,
                        padding=[14, 6], font=FONT_B)
        style.map("TNotebook.Tab",
                  background=[("selected", SURFACE)],
                  foreground=[("selected", ACCENT)])
        style.configure("TEntry",       fieldbackground=SURFACE2, foreground=TEXT,
                        insertcolor=TEXT, bordercolor=BORDER, relief="flat",
                        padding=5, font=FONT)
        style.map("TEntry",
                  fieldbackground=[("disabled", SURFACE2), ("!disabled", SURFACE2)],
                  foreground=[("disabled", BORDER), ("!disabled", TEXT)],
                  bordercolor=[("disabled", BORDER), ("!disabled", BORDER)])
        style.configure("TCheckbutton", background=BG, foreground=TEXT, font=FONT)
        style.map("TCheckbutton",
                  background=[("active", BG), ("disabled", BG)],
                  foreground=[("disabled", BORDER), ("!disabled", TEXT)])
        style.configure("TRadiobutton", background=BG, foreground=TEXT, font=FONT)
        style.map("TRadiobutton",       background=[("active", BG)])
        style.configure("TCombobox",    fieldbackground=SURFACE2, foreground=TEXT,
                        selectbackground=SURFACE2, selectforeground=TEXT,
                        insertcolor=TEXT, font=FONT)
        style.map("TCombobox",
                  fieldbackground=[("readonly", SURFACE2), ("disabled", BG)],
                  foreground=[("readonly", TEXT), ("disabled", MUTED), ("!disabled", TEXT)],
                  selectbackground=[("readonly", SURFACE2)],
                  selectforeground=[("readonly", TEXT)])
        self.option_add("*TCombobox*Listbox.background",  SURFACE2)
        self.option_add("*TCombobox*Listbox.foreground",  TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#fff")
        style.configure("TScrollbar",   background=SURFACE2, troughcolor=BG,
                        arrowcolor=MUTED, bordercolor=BG)
        style.configure("Accent.TButton",
                        background=ACCENT, foreground="#fff",
                        font=FONT_B, relief="flat", padding=[14, 6])
        style.map("Accent.TButton",
                  background=[("active", "#3a7ae0"), ("disabled", SURFACE2)],
                  foreground=[("!disabled", "#fff"), ("disabled", MUTED)])
        style.configure("TButton",
                        background=SURFACE2, foreground=TEXT,
                        font=FONT, relief="flat", padding=[10, 5], bordercolor=BORDER)
        style.map("TButton",
                  background=[("active", BORDER)],
                  foreground=[("!disabled", TEXT), ("disabled", MUTED)])
        style.configure("Red.TButton",
                        background="#7f1d1d", foreground=RED,
                        font=FONT_B, relief="flat", padding=[14, 6])
        style.map("Red.TButton",
                  background=[("active", "#991b1b")],
                  foreground=[("!disabled", RED)])
        style.configure("Green.TButton",
                        background="#14532d", foreground=GREEN,
                        font=FONT_B, relief="flat", padding=[14, 6])
        style.map("Green.TButton",
                  background=[("active", "#166534")],
                  foreground=[("!disabled", GREEN)])
        style.configure("TSeparator",   background=BORDER)
        style.configure("TProgressbar", troughcolor=SURFACE2,
                        background=ACCENT, bordercolor=BG, lightcolor=ACCENT, darkcolor=ACCENT)

    # ── Top bar ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        top = ttk.Frame(self, style="Surface.TFrame")
        top.pack(fill="x", padx=0, pady=0)
        ttk.Label(top, text="KvhLocker", style="Header.TLabel",
                  background=SURFACE).pack(side="left", padx=16, pady=10)
        ttk.Label(top, text=f"v{VERSION}  ·  AES-CFB-128  ·  ES File Explorer format",
                  style="Muted.TLabel", background=SURFACE).pack(side="left", pady=10)

        ttk.Separator(self, orient="horizontal").pack(fill="x")

        # Notebook — fixed portion; log area below gets the rest
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=False, padx=0, pady=0)

        self._enc_tab = ttk.Frame(nb)
        self._dec_tab = ttk.Frame(nb)
        self._verify_tab = ttk.Frame(nb)
        nb.add(self._enc_tab,    text="  🔒  Encrypt  ")
        nb.add(self._dec_tab,    text="  🔓  Decrypt  ")
        nb.add(self._verify_tab, text="  ✅  Verify  ")

        self._build_encrypt_tab()
        self._build_decrypt_tab()
        self._build_verify_tab()

        # Log area (shared) — packed with expand=True so it always reserves space
        ttk.Separator(self, orient="horizontal").pack(fill="x")
        log_frame = ttk.Frame(self)
        log_frame.pack(fill="both", expand=True, padx=0, pady=0)
        log_header = ttk.Frame(log_frame, style="Surface.TFrame")
        log_header.pack(fill="x")
        ttk.Label(log_header, text="Output Log", style="Surface.TLabel",
                  font=FONT_B, background=SURFACE).pack(side="left", padx=12, pady=4)
        ttk.Button(log_header, text="Clear", style="TButton",
                   command=self._clear_log).pack(side="right", padx=8, pady=3)
        ttk.Button(log_header, text="Save Log…", style="TButton",
                   command=self._save_log).pack(side="right", padx=(0, 0), pady=3)

        txt_frame = ttk.Frame(log_frame)
        txt_frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self._log_widget = tk.Text(txt_frame, height=10, bg=SURFACE, fg=TEXT,
                            font=MONO, relief="flat", wrap="word",
                            insertbackground=TEXT, state="disabled",
                            selectbackground=SURFACE2)
        sb = ttk.Scrollbar(txt_frame, command=self._log_widget.yview)
        self._log_widget.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._log_widget.pack(side="left", fill="both", expand=True)
        self._log_widget.tag_config("green",  foreground=GREEN)
        self._log_widget.tag_config("red",    foreground=RED)
        self._log_widget.tag_config("yellow", foreground=YELLOW)
        self._log_widget.tag_config("accent", foreground=ACCENT)
        self._log_widget.tag_config("muted",  foreground=MUTED)

    # ── Shared widgets ────────────────────────────────────────────────────────

    def _section(self, parent, title: str):
        f = ttk.Frame(parent)
        f.pack(fill="x", padx=14, pady=(12, 0))
        ttk.Label(f, text=title, foreground=ACCENT2, font=FONT_B).pack(anchor="w")
        ttk.Separator(f, orient="horizontal").pack(fill="x", pady=(2, 6))
        return f

    def _row(self, parent):
        r = ttk.Frame(parent)
        r.pack(fill="x", pady=2)
        return r

    def _path_row(self, parent, label: str, var: tk.StringVar,
                  pick_file=False, pick_dir=False, save=False, filetypes=None):
        r = self._row(parent)
        ttk.Label(r, text=label, width=16, anchor="e").pack(side="left", padx=(0, 6))
        e = ttk.Entry(r, textvariable=var)
        e.pack(side="left", fill="x", expand=True, padx=(0, 4))

        def _browse():
            if pick_file:
                ft = filetypes or [("All files", "*.*")]
                p = filedialog.askopenfilename(filetypes=ft)
            elif save:
                ft = filetypes or [("All files", "*.*")]
                p = filedialog.asksaveasfilename(filetypes=ft)
            else:
                p = filedialog.askdirectory()
            if p:
                var.set(p)

        ttk.Button(r, text="Browse…", command=_browse).pack(side="left")

    def _pass_row(self, parent, label: str, var: tk.StringVar):
        r = self._row(parent)
        ttk.Label(r, text=label, width=16, anchor="e").pack(side="left", padx=(0, 6))
        e = ttk.Entry(r, textvariable=var, show="●")
        e.pack(side="left", fill="x", expand=True)
        self._show_pass_toggle(r, e)

    def _show_pass_toggle(self, parent, entry: ttk.Entry):
        show = tk.BooleanVar(value=False)
        def toggle():
            entry.config(show="" if show.get() else "●")
        ttk.Checkbutton(parent, text="Show", variable=show,
                        command=toggle).pack(side="left", padx=6)

    # ── Encrypt tab ───────────────────────────────────────────────────────────

    def _build_encrypt_tab(self):
        tab = self._enc_tab

        # ── Format selection
        fmt_sec = self._section(tab, "Format")
        self._enc_format = tk.StringVar(value="eslock")
        fmt_row = self._row(fmt_sec)
        ttk.Label(fmt_row, text="Algorithm:", width=16, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Radiobutton(fmt_row, text="ESLock  (.eslock)  — AES-CFB-128 + MD5",
                        variable=self._enc_format, value="eslock",
                        command=self._enc_format_changed).pack(side="left")
        ttk.Radiobutton(fmt_row, text="KVH  (.kvhlock)  — AES-CTR + PBKDF2 + HMAC",
                        variable=self._enc_format, value="kvh",
                        command=self._enc_format_changed).pack(side="left", padx=16)

        # KVH filename format sub-row moved to Options section
        self._enc_aef_name = tk.StringVar(value="timestamp")

        # Shared password row (always visible; ESLock: empty = random per file)
        self._enc_aef_password = tk.StringVar()
        pass_row = self._row(fmt_sec)
        self._enc_aef_pass_frame = pass_row
        self._pass_row(pass_row, "Password:", self._enc_aef_password)
        self._enc_hint_var = tk.StringVar(value="Leave blank to use a random key per file")
        self._enc_hint_label = ttk.Label(fmt_sec, textvariable=self._enc_hint_var,
                                         style="Muted.TLabel", padding=(0, 0, 0, 4))
        self._enc_hint_label.pack(anchor="w", padx=(140, 0))

        # ── Input / output
        sec = self._section(tab, "Input / Output")
        self._enc_input_mode = tk.StringVar(value="folder")
        m_row = self._row(sec)
        ttk.Label(m_row, text="Input mode:", width=16, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Radiobutton(m_row, text="Folder", variable=self._enc_input_mode,
                        value="folder", command=self._enc_mode_changed).pack(side="left")
        ttk.Radiobutton(m_row, text="Single file", variable=self._enc_input_mode,
                        value="file", command=self._enc_mode_changed).pack(side="left", padx=8)

        self._enc_input = tk.StringVar()
        self._enc_input_row_holder = ttk.Frame(sec)
        self._enc_input_row_holder.pack(fill="x")
        self._enc_input_label = tk.StringVar(value="Input folder:")
        self._enc_path_frame = self._row(self._enc_input_row_holder)
        ttk.Label(self._enc_path_frame, textvariable=self._enc_input_label,
                  width=16, anchor="e").pack(side="left", padx=(0, 6))
        self._enc_path_entry = ttk.Entry(self._enc_path_frame, textvariable=self._enc_input)
        self._enc_path_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._enc_browse_btn = ttk.Button(self._enc_path_frame, text="Browse…",
                                          command=self._enc_browse_input)
        self._enc_browse_btn.pack(side="left")

        self._enc_output = tk.StringVar()
        self._path_row(sec, "Output folder:", self._enc_output, pick_dir=True)

        # ── Options
        sec3 = self._section(tab, "Options")
        opt_row = self._row(sec3)
        self._enc_partial = tk.BooleanVar(value=True)
        self._enc_compress_mid = tk.BooleanVar(value=False)
        self._enc_compress_mode = tk.StringVar(value="full")
        self._enc_compress_n = tk.StringVar(value="64")
        self._enc_compress_n_unit = tk.StringVar(value="KB")
        self._enc_compress_algo = tk.StringVar(value="zlib")
        ttk.Checkbutton(opt_row, text="Partial encryption  (first + last N bytes)",
                        variable=self._enc_partial,
                        command=self._enc_partial_changed).pack(side="left")
        self._enc_compress_mid_cb = ttk.Checkbutton(
            opt_row, text="Compress middle  (KVH)",
            variable=self._enc_compress_mid,
            command=self._enc_compress_mid_changed)
        # hidden by default; shown by _enc_partial_changed when KVH+partial

        # Sub-row for compress mode — outer frame always packed (holds position), inner row toggled
        self._enc_compress_mode_frame = ttk.Frame(sec3)
        self._enc_compress_mode_frame.pack(fill="x")     # always in layout; zero height when inner hidden
        self._enc_compress_mode_inner = ttk.Frame(self._enc_compress_mode_frame)  # not packed yet
        ttk.Label(self._enc_compress_mode_inner, text="Mode:", width=6, anchor="e").pack(side="left", padx=(4, 4))
        for _lbl, _val in (("Full", "full"), ("Partial", "partial"), ("Block", "block")):
            ttk.Radiobutton(self._enc_compress_mode_inner, text=_lbl, variable=self._enc_compress_mode,
                            value=_val, command=self._enc_compress_mid_changed).pack(side="left")
        self._enc_compress_n_label = ttk.Label(self._enc_compress_mode_inner, text="  N:", anchor="e")
        self._enc_compress_n_label.pack(side="left", padx=(12, 4))
        self._enc_compress_n_spin = tk.Spinbox(
            self._enc_compress_mode_inner, from_=1, to=256, increment=1,
            textvariable=self._enc_compress_n, width=7,
            bg=SURFACE2, fg=TEXT, insertbackground=TEXT,
            relief="flat", highlightthickness=1, highlightbackground=BORDER)
        self._enc_compress_n_spin.pack(side="left")
        self._enc_compress_n_unit_cb = ttk.Combobox(
            self._enc_compress_mode_inner, textvariable=self._enc_compress_n_unit,
            values=["B", "KB", "MB"], state="readonly", width=5)
        self._enc_compress_n_unit_cb.pack(side="left", padx=(4, 0))
        ttk.Label(self._enc_compress_mode_inner, text="  Algo:", anchor="e").pack(side="left", padx=(12, 4))
        ttk.Combobox(
            self._enc_compress_mode_inner, textvariable=self._enc_compress_algo,
            values=["zlib", "lzma", "lzma2"], state="readonly", width=8
        ).pack(side="left")
        # Auto-clamp N when algo switches
        self._enc_compress_algo.trace_add("write", lambda *_: self._enc_compress_algo_changed())

        # Skip-compress extensions sub-row (inside same inner frame)
        self._enc_compress_skip = tk.BooleanVar(value=True)
        self._enc_compress_skip_exts = tk.StringVar(
            value=" ".join(sorted(_kvh_NOCOMPRESS_EXTS)))
        skip_row = ttk.Frame(self._enc_compress_mode_inner)
        skip_row.pack(side="left", padx=(16, 0))
        ttk.Checkbutton(skip_row, text="Skip ext:",
                        variable=self._enc_compress_skip).pack(side="left")
        self._enc_compress_skip_entry = ttk.Entry(
            skip_row, textvariable=self._enc_compress_skip_exts, width=38)
        self._enc_compress_skip_entry.pack(side="left", padx=(4, 4))
        ttk.Button(skip_row, text="↺",
                   command=lambda: self._enc_compress_skip_exts.set(
                       " ".join(sorted(_kvh_NOCOMPRESS_EXTS)))
                   ).pack(side="left")

        bs_row = self._row(sec3)
        ttk.Label(bs_row, text="Block size (N):", width=16, anchor="e").pack(side="left", padx=(0, 6))
        self._enc_blocksize = tk.StringVar(value=str(DEFAULT_BLOCK_SIZE))
        bs_spin = tk.Spinbox(bs_row, from_=64, to=65536, increment=256,
                             textvariable=self._enc_blocksize, width=10,
                             bg=SURFACE2, fg=TEXT, insertbackground=TEXT,
                             relief="flat", font=FONT, buttonbackground=SURFACE2)
        bs_spin.pack(side="left")
        ttk.Label(bs_row, text="bytes", style="Muted.TLabel").pack(side="left", padx=6)

        opt2 = self._row(sec3)
        self._enc_store_name    = tk.BooleanVar(value=True)
        self._enc_preserve_date = tk.BooleanVar(value=True)
        self._enc_overwrite     = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt2, text="Filename encryption",
                        variable=self._enc_store_name).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(opt2, text="Preserve original file date",
                        variable=self._enc_preserve_date).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(opt2, text="Overwrite existing files",
                        variable=self._enc_overwrite).pack(side="left")

        self._enc_aef_name_frame = self._row(sec3)
        ttk.Label(self._enc_aef_name_frame, text="Output filename:", width=16, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Radiobutton(self._enc_aef_name_frame, text="Random UUID",
                        variable=self._enc_aef_name, value="uuid").pack(side="left")
        ttk.Radiobutton(self._enc_aef_name_frame, text="Timestamp",
                        variable=self._enc_aef_name, value="timestamp").pack(side="left", padx=16)
        # always visible — applies to both ESLock and KVH

        # ── Progress + Run
        self._enc_progress = ttk.Progressbar(tab, mode="determinate")
        self._enc_progress.pack(fill="x", padx=14, pady=(14, 4))
        self._enc_status = tk.StringVar(value="Ready")
        ttk.Label(tab, textvariable=self._enc_status, style="Muted.TLabel").pack(anchor="w", padx=16)

        btn_row = ttk.Frame(tab)
        btn_row.pack(fill="x", padx=14, pady=(8, 14))
        self._enc_run_btn = ttk.Button(btn_row, text="  Encrypt  ", style="Accent.TButton",
                                       command=self._run_encrypt)
        self._enc_run_btn.pack(side="left")

    def _enc_browse_input(self):
        mode = self._enc_input_mode.get()
        if mode == "folder":
            p = filedialog.askdirectory()
        else:
            p = filedialog.askopenfilename()
        if p:
            self._enc_input.set(p)

    def _enc_mode_changed(self):
        mode = self._enc_input_mode.get()
        self._enc_input_label.set("Input folder:" if mode == "folder" else "Input file:")

    def _enc_format_changed(self):
        fmt = self._enc_format.get()
        self._enc_partial_changed()   # update compress-mid visibility
        if fmt == "kvh":
            self._enc_hint_var.set("")
        else:
            self._enc_hint_var.set("Leave blank to use a random key per file")

    def _enc_key_mode_changed(self):
        mode = self._enc_key_mode.get()
        _set_frame_state(self._enc_pass_frame, "normal" if mode == "password" else "disabled")
        _set_frame_state(self._enc_hex_frame,  "normal" if mode == "hex"      else "disabled")

    def _enc_partial_changed(self):
        is_kvh     = self._enc_format.get() == "kvh"
        is_partial = self._enc_partial.get()
        if is_kvh and is_partial:
            self._enc_compress_mid_cb.pack(side="left", padx=(20, 0))
        else:
            self._enc_compress_mid.set(False)
            self._enc_compress_mid_cb.pack_forget()
            self._enc_compress_mode_inner.pack_forget()
        self._enc_compress_mid_changed()  # always sync sub-row visibility

    def _enc_compress_mid_changed(self):
        """Show/hide compress-mode inner row and N spinbox."""
        if self._enc_compress_mid.get():
            self._enc_compress_mode_inner.pack(fill="x", pady=2)
        else:
            self._enc_compress_mode_inner.pack_forget()
        mode = self._enc_compress_mode.get()
        if mode in ("partial", "block"):
            self._enc_compress_n_label.pack(side="left", padx=(12, 4))
            self._enc_compress_n_spin.pack(side="left")
            self._enc_compress_n_unit_cb.pack(side="left", padx=(4, 0))
        else:
            self._enc_compress_n_label.pack_forget()
            self._enc_compress_n_spin.pack_forget()
            self._enc_compress_n_unit_cb.pack_forget()

    def _enc_compress_algo_changed(self):
        """No hard cap by algo — just reset obviously huge values when switching to lzma."""
        algo = self._enc_compress_algo.get()
        _unit_mult = {"B": 1, "KB": 1024, "MB": 1024 * 1024}
        try:
            n_bytes = int(self._enc_compress_n.get()) * _unit_mult.get(self._enc_compress_n_unit.get(), 1024)
        except ValueError:
            return
        # Only reset if clearly unreasonable (> 256 MB) regardless of algo
        if n_bytes > 256 * 1024 * 1024:
            self._enc_compress_n.set("64")
            self._enc_compress_n_unit.set("MB" if algo == "zlib" else "KB")

    def _run_encrypt(self):
        src = self._enc_input.get().strip()
        dst = self._enc_output.get().strip()
        if not src:
            messagebox.showerror("Error", "Please select an input path.")
            return
        src_path = Path(src)
        if not src_path.exists():
            messagebox.showerror("Error", f"Input path not found:\n{src}")
            return
        if not dst:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            base = src_path.parent if src_path.is_file() else src_path
            dst_path = base / f"encrypted-{ts}"
        else:
            dst_path = Path(dst)

        fmt = self._enc_format.get()

        # ── KVH path ────────────────────────────────────────────────────────
        if fmt == "kvh":
            pw = self._enc_aef_password.get() or _kvh_DEFAULT_PW
            try:
                block_size = int(self._enc_blocksize.get())
                if block_size <= 0:
                    raise ValueError()
            except ValueError:
                messagebox.showerror("Error", "Block size must be a positive integer.")
                return
            is_partial = self._enc_partial.get()
            overwrite  = self._enc_overwrite.get()
            preserve_date = self._enc_preserve_date.get()
            compress_mid  = self._enc_compress_mid.get()
            compress_mode = self._enc_compress_mode.get() if compress_mid else None
            compress_algo = self._enc_compress_algo.get() if compress_mid else "zlib"
            skip_compress_exts = None
            if compress_mid and self._enc_compress_skip.get():
                skip_compress_exts = {
                    e.lower() if e.startswith(".") else f".{e.lower()}"
                    for e in self._enc_compress_skip_exts.get().split()
                    if e
                }
            try:
                _unit_mult = {"B": 1, "KB": 1024, "MB": 1024 * 1024}
                compress_n = int(self._enc_compress_n.get()) * _unit_mult.get(self._enc_compress_n_unit.get(), 1024)
                if compress_n <= 0:
                    raise ValueError()
            except ValueError:
                compress_n = 65536
            # Cap at 256 MB for both zlib and lzma — lzma will be slow but functional
            _max_n = 256 * 1024 * 1024
            if compress_n > _max_n:
                messagebox.showwarning(
                    "N too large",
                    f"Compress N ({compress_n // (1024*1024)} MB) exceeds 256 MB.\n"
                    f"Clamped to 256 MB."
                )
                compress_n = _max_n
            aef_name  = self._enc_aef_name.get()
            ext_excl   = {".kvhlock"}
            files = [src_path] if src_path.is_file() else [
                f for f in src_path.rglob("*") if f.is_file() and f.suffix.lower() not in ext_excl
            ]
            if not files:
                messagebox.showinfo("No files", "No suitable files found to encrypt.")
                return
            self._enc_run_btn.configure(state="disabled")
            self._enc_progress["maximum"] = len(files)
            self._enc_progress["value"] = 0
            self._log_section("ENCRYPT  [KVH]")
            master_salt = get_random_bytes(_kvh_SALT_SIZE)
            master_key  = kvh_derive_master_key(pw, master_salt)
            self._log(f"[INFO] Deriving KVH master key…  salt={master_salt.hex().upper()}", "accent")

            def kvh_worker():
                ok = err = 0
                failures: list[tuple[str, str]] = []
                for i, f in enumerate(files):
                    if src_path.is_file():
                        rel_dir = dst_path
                    else:
                        rel = f.relative_to(src_path)
                        rel_dir = dst_path / rel.parent
                    rel_dir.mkdir(parents=True, exist_ok=True)
                    if aef_name == "timestamp":
                        out_name = datetime.now().strftime("%Y%m%d%H%M%S%f")[:17] + ".kvhlock"
                    else:
                        out_name = str(uuid.uuid4()) + ".kvhlock"
                    out_path = rel_dir / out_name
                    try:
                        def _progress(done, total, fname=f.name, idx=i, algo=compress_algo, n_bytes=compress_n):
                            if done < 0:
                                # Sentinel: about to compress a chunk (may take a while)
                                self.after(0, lambda n=fname, ii=idx, a=algo: self._enc_status.set(
                                    f"[{ii+1}/{len(files)}] {n}  compressing ({a})…"
                                ))
                            else:
                                pct = int(done * 100 / total) if total else 100
                                self.after(0, lambda p=pct, n=fname, ii=idx: self._enc_status.set(
                                    f"[{ii+1}/{len(files)}] {n}  {p}%"
                                ))
                        enc_type = kvh_encrypt_file(
                            f, out_path, master_key, master_salt, pw,
                            is_partial, block_size, overwrite, preserve_date,
                            compress_mode, compress_n, compress_algo,
                            skip_compress_exts,
                            progress_cb=_progress)
                        self._log(f"[OK]  {f.name}  →  {out_path.name}  ({enc_type})", "green")
                        ok += 1
                    except Exception as exc:
                        tb = traceback.format_exc().strip()
                        reason = str(exc) or type(exc).__name__
                        self._log(f"[ERR] {f.name}\n      Reason : {reason}\n      Detail : {tb}", "red")
                        failures.append((f.name, reason))
                        err += 1
                    self.after(0, lambda v=i+1: self._enc_progress.configure(value=v))
                    self._enc_status.set(f"Processing {i+1}/{len(files)}…")
                self._enc_status.set(f"Done — {ok} encrypted, {err} errors")
                self._log(f"\nEncrypt [KVH] complete: {ok} OK, {err} errors  →  {dst_path}", "accent")
                if failures:
                    self._log(f"\nFailed files ({len(failures)}):", "yellow")
                    for name, reason in failures:
                        self._log(f"  • {name}\n    {reason}", "yellow")
                self.after(0, lambda: self._enc_run_btn.configure(state="normal"))

            threading.Thread(target=kvh_worker, daemon=True).start()
            return

        # ── ESLock path ───────────────────────────────────────────────────────
        # Key: password → derived key; empty → random per file
        pw = self._enc_aef_password.get()
        fixed_key: Optional[bytes] = derive_key_from_password(pw) if pw else None

        try:
            block_size = int(self._enc_blocksize.get())
            if block_size <= 0:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Error", "Block size must be a positive integer.")
            return

        is_partial = self._enc_partial.get()
        store_name = self._enc_store_name.get()
        preserve_date = self._enc_preserve_date.get()
        out_name_mode = self._enc_aef_name.get()
        overwrite  = self._enc_overwrite.get()

        files = [src_path] if src_path.is_file() else [
            f for f in src_path.rglob("*") if f.is_file() and f.suffix.lower() != ".eslock"
        ]
        if not files:
            messagebox.showinfo("No files", "No suitable files found to encrypt.")
            return

        self._enc_run_btn.configure(state="disabled")
        self._enc_progress["maximum"] = len(files)
        self._enc_progress["value"] = 0
        self._log_section("ENCRYPT")

        def worker():
            ok = err = 0
            failures: list[tuple[str, str]] = []
            for i, f in enumerate(files):
                if src_path.is_file():
                    rel_dir = dst_path
                else:
                    rel = f.relative_to(src_path)
                    rel_dir = dst_path / rel.parent
                rel_dir.mkdir(parents=True, exist_ok=True)
                if out_name_mode == "uuid":
                    out_name = str(uuid.uuid4()) + ".eslock"
                else:
                    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")[:17]
                    out_name = ts + ".eslock"
                out_path = rel_dir / out_name
                key = fixed_key if fixed_key else os.urandom(16)
                try:
                    enc_type = encrypt_file(f, out_path, key, is_partial,
                                            block_size, store_name, overwrite, preserve_date)
                    self._log(f"[OK]  {f.name}  →  {out_path.name}  ({enc_type}  key={key.hex().upper()})", "green")
                    ok += 1
                except Exception as exc:
                    tb = traceback.format_exc().strip()
                    reason = str(exc) or type(exc).__name__
                    self._log(f"[ERR] {f.name}\n      Reason : {reason}\n      Detail : {tb}", "red")
                    failures.append((f.name, reason))
                    err += 1
                self.after(0, lambda v=i+1: self._enc_progress.configure(value=v))
                self._enc_status.set(f"Processing {i+1}/{len(files)}…")

            self._enc_status.set(f"Done — {ok} encrypted, {err} errors")
            self._log(f"\nEncrypt complete: {ok} OK, {err} errors  →  {dst_path}", "accent")
            if failures:
                self._log(f"\nFailed files ({len(failures)}):", "yellow")
                for name, reason in failures:
                    self._log(f"  • {name}\n    {reason}", "yellow")
            self.after(0, lambda: self._enc_run_btn.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    # ── Decrypt tab ───────────────────────────────────────────────────────────

    def _build_decrypt_tab(self):
        tab = self._dec_tab

        # ── Format selection
        fmt_sec = self._section(tab, "Format")
        self._dec_format = tk.StringVar(value="eslock")
        fmt_row = self._row(fmt_sec)
        ttk.Label(fmt_row, text="Algorithm:", width=16, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Radiobutton(fmt_row, text="ESLock  (.eslock)", variable=self._dec_format,
                        value="eslock", command=self._dec_format_changed).pack(side="left")
        ttk.Radiobutton(fmt_row, text="KVH  (.kvhlock)", variable=self._dec_format,
                        value="kvh", command=self._dec_format_changed).pack(side="left", padx=16)

        sec = self._section(tab, "Input / Output")
        self._dec_input_mode = tk.StringVar(value="folder")
        m_row = self._row(sec)
        ttk.Label(m_row, text="Input mode:", width=16, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Radiobutton(m_row, text="Folder", variable=self._dec_input_mode,
                        value="folder", command=self._dec_mode_changed).pack(side="left")
        ttk.Radiobutton(m_row, text="Single file", variable=self._dec_input_mode,
                        value="file", command=self._dec_mode_changed).pack(side="left", padx=8)

        self._dec_input = tk.StringVar()
        self._dec_input_label = tk.StringVar(value="Input folder:")
        self._dec_path_frame = self._row(sec)
        ttk.Label(self._dec_path_frame, textvariable=self._dec_input_label,
                  width=16, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Entry(self._dec_path_frame, textvariable=self._dec_input).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(self._dec_path_frame, text="Browse…",
                   command=self._dec_browse_input).pack(side="left")

        self._dec_output = tk.StringVar()
        self._path_row(sec, "Output folder:", self._dec_output, pick_dir=True)

        sec2 = self._section(tab, "Key Override  (leave blank to use embedded key)")
        self._dec_key_mode = tk.StringVar(value="embedded")
        km = self._row(sec2)
        self._dec_key_radio_frame = km
        ttk.Label(km, text="Key source:", width=16, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Radiobutton(km, text="Embedded (auto)", variable=self._dec_key_mode,
                        value="embedded", command=self._dec_key_mode_changed).pack(side="left")
        ttk.Radiobutton(km, text="Password", variable=self._dec_key_mode,
                        value="password", command=self._dec_key_mode_changed).pack(side="left", padx=8)

        self._dec_password = tk.StringVar()
        self._dec_pass_frame = ttk.Frame(sec2)
        self._dec_pass_frame.pack(fill="x")
        self._pass_row(self._dec_pass_frame, "Password:", self._dec_password)

        self._dec_key_mode_changed()

        sec3 = self._section(tab, "Options")
        opt = self._row(sec3)
        self._dec_ignore_crc = tk.BooleanVar(value=False)
        self._dec_overwrite  = tk.BooleanVar(value=False)
        self._dec_heuristic  = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Ignore CRC errors", variable=self._dec_ignore_crc).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(opt, text="Overwrite existing files", variable=self._dec_overwrite).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(opt, text="Heuristic footer detection", variable=self._dec_heuristic).pack(side="left")

        self._dec_progress = ttk.Progressbar(tab, mode="determinate")
        self._dec_progress.pack(fill="x", padx=14, pady=(14, 4))
        self._dec_status = tk.StringVar(value="Ready")
        ttk.Label(tab, textvariable=self._dec_status, style="Muted.TLabel").pack(anchor="w", padx=16)

        btn_row = ttk.Frame(tab)
        btn_row.pack(fill="x", padx=14, pady=(8, 14))
        self._dec_run_btn = ttk.Button(btn_row, text="  Decrypt  ", style="Green.TButton",
                                       command=self._run_decrypt)
        self._dec_run_btn.pack(side="left")

    def _dec_browse_input(self):
        mode = self._dec_input_mode.get()
        fmt  = self._dec_format.get()
        if mode == "folder":
            p = filedialog.askdirectory()
        else:
            if fmt == "kvh":
                filetypes = [("KVH files", "*.kvhlock"), ("All", "*.*")]
            else:
                filetypes = [("ESLock files", "*.eslock"), ("All", "*.*")]
            p = filedialog.askopenfilename(filetypes=filetypes)
        if p:
            self._dec_input.set(p)

    def _dec_mode_changed(self):
        mode = self._dec_input_mode.get()
        self._dec_input_label.set("Input folder:" if mode == "folder" else "Input file:")

    def _dec_format_changed(self):
        fmt = self._dec_format.get()
        if fmt == "kvh":
            # KVH uses password only
            self._dec_key_mode.set("password")
            for child in self._dec_key_radio_frame.winfo_children():
                if isinstance(child, ttk.Radiobutton):
                    try: val = child["value"]
                    except Exception: val = ""
                    child.configure(state="disabled" if val == "embedded" else "normal")
            _set_frame_state(self._dec_pass_frame, "normal")
        else:
            for child in self._dec_key_radio_frame.winfo_children():
                if isinstance(child, ttk.Radiobutton):
                    child.configure(state="normal")
            self._dec_key_mode_changed()

    def _dec_key_mode_changed(self):
        mode = self._dec_key_mode.get()
        _set_frame_state(self._dec_pass_frame, "normal" if mode == "password" else "disabled")

    def _resolve_dec_key(self) -> Optional[bytes]:
        mode = self._dec_key_mode.get()
        if mode == "password":
            pw = self._dec_password.get()
            if not pw:
                messagebox.showerror("Error", "Password is empty.")
                return None
            return derive_key_from_password(pw)
        return None  # embedded

    def _run_decrypt(self):
        src = self._dec_input.get().strip()
        dst = self._dec_output.get().strip()
        if not src:
            messagebox.showerror("Error", "Please select an input path.")
            return
        src_path = Path(src)
        if not src_path.exists():
            messagebox.showerror("Error", f"Input path not found:\n{src}")
            return

        if not dst:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            base = src_path.parent if src_path.is_file() else src_path.parent
            dst_path = base / f"decrypted-{ts}"
        else:
            dst_path = Path(dst)

        fmt = self._dec_format.get()

        # ── KVH path ────────────────────────────────────────────────────────
        if fmt == "kvh":
            pw = self._dec_password.get() or _kvh_DEFAULT_PW
            overwrite = self._dec_overwrite.get()
            files = ([src_path] if src_path.is_file() else
                     list(src_path.rglob("*.kvhlock")))
            if not files:
                messagebox.showinfo("No files", "No *.kvhlock files found.")
                return
            self._dec_run_btn.configure(state="disabled")
            self._dec_progress["maximum"] = len(files)
            self._dec_progress["value"] = 0
            self._log_section("DECRYPT  [KVH]")
            # Pre-derive master key from first file's salt — reuse if salt matches
            _mk_cache: dict[bytes, bytes] = {}

            def kvh_worker():
                ok = err = 0
                failures: list[tuple[str, str]] = []
                for i, f in enumerate(files):
                    if src_path.is_file():
                        rel_dir = dst_path
                    else:
                        rel = f.relative_to(src_path)
                        rel_dir = dst_path / rel.parent
                    rel_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        salt = kvh_peek_salt(f, pw)
                        if salt not in _mk_cache:
                            self._log(f"[INFO] Deriving KVH key for salt {salt.hex().upper()[:16]}…", "accent")
                            _mk_cache[salt] = kvh_derive_master_key(pw, salt)
                        mk = _mk_cache[salt]
                        tmp_path = rel_dir / (f.stem + ".~aef_dec_tmp")
                        info = kvh_decrypt_file(f, tmp_path, pw, overwrite=True,
                                                 precomputed_master_key=mk)
                        final_path = rel_dir / info["original_name"]
                        if not overwrite and final_path.exists():
                            tmp_path.unlink(missing_ok=True)
                            raise RuntimeError(f"Output file already exists: {final_path.name}")
                        tmp_path.rename(final_path)
                        self._log(
                            f"[OK]  {f.name}  →  {final_path.name}  "
                            f"(HMAC OK  {info['original_size']} bytes)", "green")
                        ok += 1
                    except Exception as exc:
                        tb = traceback.format_exc().strip()
                        reason = str(exc) or type(exc).__name__
                        self._log(f"[ERR] {f.name}\n      Reason : {reason}\n      Detail : {tb}", "red")
                        failures.append((f.name, reason))
                        err += 1
                    self.after(0, lambda v=i+1: self._dec_progress.configure(value=v))
                    self._dec_status.set(f"Processing {i+1}/{len(files)}…")
                self._dec_status.set(f"Done — {ok} decrypted, {err} errors")
                self._log(f"\nDecrypt [KVH] complete: {ok} OK, {err} errors  →  {dst_path}", "accent")
                if failures:
                    self._log(f"\nFailed files ({len(failures)}):", "yellow")
                    for name, reason in failures:
                        self._log(f"  • {name}\n    {reason}", "yellow")
                self.after(0, lambda: self._dec_run_btn.configure(state="normal"))

            threading.Thread(target=kvh_worker, daemon=True).start()
            return

        # ── ESLock path ───────────────────────────────────────────────────────
        key_override = self._resolve_dec_key()
        if self._dec_key_mode.get() != "embedded" and key_override is None:
            return  # error already shown

        ignore_crc = self._dec_ignore_crc.get()
        overwrite  = self._dec_overwrite.get()

        files = ([src_path] if src_path.is_file() else
                 list(src_path.rglob("*.eslock")))
        if not files:
            messagebox.showinfo("No files", "No .eslock files found.")
            return

        self._dec_run_btn.configure(state="disabled")
        self._dec_progress["maximum"] = len(files)
        self._dec_progress["value"] = 0
        self._log_section("DECRYPT")

        def worker():
            ok = err = 0
            failures: list[tuple[str, str]] = []
            for i, f in enumerate(files):
                if src_path.is_file():
                    rel_dir = dst_path
                else:
                    rel = f.relative_to(src_path)
                    rel_dir = dst_path / rel.parent
                rel_dir.mkdir(parents=True, exist_ok=True)
                try:
                    info = read_footer_standard(f)
                    key = key_override if key_override else info["key"]
                    # ── Key mismatch check ──────────────────────────────────
                    mismatch = None
                    if key_override and info["key"]:
                        mismatch = _key_mismatch_msg(key_override, info["key"])
                    if mismatch:
                        reason = f"Wrong password/key — {mismatch}"
                        self._log(f"[WARN] {f.name}\n      Reason : {reason}", "yellow")
                        failures.append((f.name, reason))
                        err += 1
                        continue
                    # ───────────────────────────────────────────────────────
                    out_name = info["original_name"] or f.stem
                    out_path = rel_dir / out_name
                    decrypt_file(f, out_path, key_override, ignore_crc, overwrite)
                    crc_tag = "CRC OK" if info["crc_valid"] else "CRC WARN"
                    self._log(
                        f"[OK]  {f.name}  →  {out_path.name}  "
                        f"({crc_tag}  key={key.hex().upper()})", "green")
                    ok += 1
                except Exception as exc:
                    tb = traceback.format_exc().strip()
                    reason = str(exc) or type(exc).__name__
                    self._log(f"[ERR] {f.name}\n      Reason : {reason}\n      Detail : {tb}", "red")
                    failures.append((f.name, reason))
                    err += 1
                self.after(0, lambda v=i+1: self._dec_progress.configure(value=v))
                self._dec_status.set(f"Processing {i+1}/{len(files)}…")

            self._dec_status.set(f"Done — {ok} decrypted, {err} errors")
            self._log(f"\nDecrypt complete: {ok} OK, {err} errors  →  {dst_path}", "accent")
            if failures:
                self._log(f"\nFailed files ({len(failures)}):", "yellow")
                for name, reason in failures:
                    self._log(f"  • {name}\n    {reason}", "yellow")
            self.after(0, lambda: self._dec_run_btn.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    # ── Verify tab ────────────────────────────────────────────────────────────

    def _build_verify_tab(self):
        tab = self._verify_tab

        sec0 = self._section(tab, "Format")
        self._ver_format = tk.StringVar(value="eslock")
        fmt_row = self._row(sec0)
        ttk.Label(fmt_row, text="Algorithm:", width=16, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Radiobutton(fmt_row, text="ESLock  (.eslock)", variable=self._ver_format,
                        value="eslock", command=self._ver_format_changed).pack(side="left")
        ttk.Radiobutton(fmt_row, text="KVH  (.kvhlock)", variable=self._ver_format,
                        value="kvh", command=self._ver_format_changed).pack(side="left", padx=16)

        sec = self._section(tab, "Select Files to Verify")
        self._ver_input_mode = tk.StringVar(value="folder")
        m_row = self._row(sec)
        ttk.Label(m_row, text="Input mode:", width=16, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Radiobutton(m_row, text="Folder", variable=self._ver_input_mode,
                        value="folder", command=self._ver_mode_changed).pack(side="left")
        ttk.Radiobutton(m_row, text="Single file", variable=self._ver_input_mode,
                        value="file", command=self._ver_mode_changed).pack(side="left", padx=8)

        self._ver_input  = tk.StringVar()
        self._ver_input_label = tk.StringVar(value="Input folder:")
        vpr = self._row(sec)
        ttk.Label(vpr, textvariable=self._ver_input_label,
                  width=16, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Entry(vpr, textvariable=self._ver_input).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(vpr, text="Browse…",
                   command=self._ver_browse).pack(side="left")

        sec2 = self._section(tab, "Key Override  (leave blank to use embedded key)")
        self._ver_key_mode = tk.StringVar(value="embedded")
        km = self._row(sec2)
        self._ver_key_radio_frame = km
        ttk.Label(km, text="Key source:", width=16, anchor="e").pack(side="left", padx=(0, 6))
        ttk.Radiobutton(km, text="Embedded (auto)", variable=self._ver_key_mode,
                        value="embedded", command=self._ver_key_mode_changed).pack(side="left")
        ttk.Radiobutton(km, text="Password", variable=self._ver_key_mode,
                        value="password", command=self._ver_key_mode_changed).pack(side="left", padx=8)

        self._ver_password = tk.StringVar()
        self._ver_pass_frame = ttk.Frame(sec2)
        self._ver_pass_frame.pack(fill="x")
        self._pass_row(self._ver_pass_frame, "Password:", self._ver_password)

        self._ver_key_mode_changed()

        # Results table
        sec3 = self._section(tab, "Results")
        tv_frame = ttk.Frame(tab)
        tv_frame.pack(fill="both", expand=True, padx=14, pady=(4, 4))

        cols = ("file", "size", "enc", "crc", "key", "status")
        self._ver_tree = ttk.Treeview(tv_frame, columns=cols, show="headings", height=6)
        tv = self._ver_tree

        style = ttk.Style()
        style.configure("Treeview", background=SURFACE, foreground=TEXT,
                        fieldbackground=SURFACE, rowheight=22, font=FONT_S)
        style.configure("Treeview.Heading", background=SURFACE2,
                        foreground=ACCENT, font=FONT_B)
        style.map("Treeview", background=[("selected", SURFACE2)])

        tv.heading("file",   text="Filename")
        tv.heading("size",   text="Size")
        tv.heading("enc",    text="Encryption")
        tv.heading("crc",    text="CRC / Original name")
        tv.heading("key",    text="Key (hex) / Salt")
        tv.heading("status", text="Status")
        tv.column("file",   width=160, anchor="w")
        tv.column("size",   width=90,  anchor="e")
        tv.column("enc",    width=185, anchor="center")
        tv.column("crc",    width=210, anchor="w")
        tv.column("key",    width=105, anchor="e")
        tv.column("status", width=90,  anchor="center")

        tv.tag_configure("ok",   foreground=GREEN)
        tv.tag_configure("fail", foreground=RED)
        tv.tag_configure("warn", foreground=YELLOW)

        vsb = ttk.Scrollbar(tv_frame, orient="vertical",   command=tv.yview)
        hsb = ttk.Scrollbar(tv_frame, orient="horizontal", command=tv.xview)
        tv.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right",  fill="y")
        hsb.pack(side="bottom", fill="x")
        tv.pack(side="left", fill="both", expand=True)

        self._ver_progress = ttk.Progressbar(tab, mode="determinate")
        self._ver_progress.pack(fill="x", padx=14, pady=(4, 2))
        self._ver_status = tk.StringVar(value="Ready")
        ttk.Label(tab, textvariable=self._ver_status, style="Muted.TLabel").pack(anchor="w", padx=16)

        btn_row = ttk.Frame(tab)
        btn_row.pack(fill="x", padx=14, pady=(6, 14))
        self._ver_run_btn = ttk.Button(btn_row, text="  Verify  ", style="Accent.TButton",
                                       command=self._run_verify)
        self._ver_run_btn.pack(side="left")
        ttk.Button(btn_row, text="Clear results",
                   command=lambda: self._ver_tree.delete(*self._ver_tree.get_children())
                   ).pack(side="left", padx=8)

    def _ver_browse(self):
        mode = self._ver_input_mode.get()
        fmt  = self._ver_format.get()
        if mode == "folder":
            p = filedialog.askdirectory()
        else:
            if fmt == "kvh":
                p = filedialog.askopenfilename(filetypes=[("KVH files", "*.kvhlock"), ("All", "*.*")])
            else:
                p = filedialog.askopenfilename(filetypes=[("ESLock files", "*.eslock"), ("All", "*.*")])
        if p:
            self._ver_input.set(p)

    def _ver_mode_changed(self):
        mode = self._ver_input_mode.get()
        self._ver_input_label.set("Input folder:" if mode == "folder" else "Input file:")

    def _ver_key_mode_changed(self):
        mode = self._ver_key_mode.get()
        _set_frame_state(self._ver_pass_frame, "normal" if mode == "password" else "disabled")

    def _ver_format_changed(self):
        fmt = self._ver_format.get()
        if fmt == "kvh":
            self._ver_key_mode.set("password")
            for child in self._ver_key_radio_frame.winfo_children():
                if isinstance(child, ttk.Radiobutton):
                    try: val = child["value"]
                    except Exception: val = ""
                    child.configure(state="disabled" if val == "embedded" else "normal")
            _set_frame_state(self._ver_pass_frame, "normal")
            self._ver_tree.heading("crc",  text="Original filename")
            self._ver_tree.heading("key",  text="Original size")
        else:
            for child in self._ver_key_radio_frame.winfo_children():
                if isinstance(child, ttk.Radiobutton):
                    child.configure(state="normal")
            self._ver_key_mode_changed()
            self._ver_tree.heading("crc",  text="CRC")
            self._ver_tree.heading("key",  text="Key (hex)")

    def _resolve_ver_key(self) -> Optional[bytes]:
        mode = self._ver_key_mode.get()
        if mode == "password":
            pw = self._ver_password.get()
            if not pw:
                messagebox.showerror("Error", "Password is empty.")
                return None
            return derive_key_from_password(pw)
        return None

    def _run_verify(self):
        src = self._ver_input.get().strip()
        if not src:
            messagebox.showerror("Error", "Please select an input path.")
            return
        src_path = Path(src)
        if not src_path.exists():
            messagebox.showerror("Error", f"Path not found:\n{src}")
            return

        fmt = self._ver_format.get()

        if fmt == "kvh":
            pw = self._ver_password.get() or _kvh_DEFAULT_PW
            files = ([src_path] if src_path.is_file() else
                     list(src_path.rglob("*.kvhlock")))
            if not files:
                messagebox.showinfo("No files", "No *.kvhlock files found.")
                return
            self._ver_run_btn.configure(state="disabled")
            self._ver_progress["maximum"] = len(files)
            self._ver_progress["value"] = 0
            self._log_section("VERIFY  [KVH]")

            def kvh_worker():
                ok = fail = 0
                for i, f in enumerate(files):
                    try:
                        info = kvh_verify_file(f, pw)
                        enc = (f"Partial {info['block_size']}B" if info["partial"] else "Full")
                        if info["compress"]:
                            enc += f"+{info['compress']}/{info['compress_algo']}"
                        status = "HEALTHY" if info["hmac_ok"] else "CORRUPT"
                        tag    = "ok"   if info["hmac_ok"] else "fail"
                        size_str = _fmt_size(f.stat().st_size)
                        orig_str = _fmt_size(info['original_size'])
                        row = (f.name, size_str, enc, info["filename"] or "?", orig_str, status)
                        self.after(0, lambda r=row, t=tag:
                                   self._ver_tree.insert("", "end", values=r, tags=(t,)))
                        self._log(
                            f"{'[OK]  ' if info['hmac_ok'] else '[FAIL]'} "
                            f"{f.name}  HMAC={'OK' if info['hmac_ok'] else 'FAIL'}  "
                            f"orig={info['filename']}",
                            "green" if info["hmac_ok"] else "red")
                        if info["hmac_ok"]: ok += 1
                        else: fail += 1
                    except Exception as exc:
                        tb = traceback.format_exc().strip()
                        reason = str(exc) or type(exc).__name__
                        size_str = _fmt_size(f.stat().st_size) if f.exists() else "?"
                        row = (f.name, size_str, "?", "?", "?", "ERROR")
                        self.after(0, lambda r=row:
                                   self._ver_tree.insert("", "end", values=r, tags=("fail",)))
                        self._log(f"[ERR] {f.name}\n      Reason : {reason}\n      Detail : {tb}", "red")
                        fail += 1
                    self.after(0, lambda v=i+1: self._ver_progress.configure(value=v))
                    self._ver_status.set(f"Checking {i+1}/{len(files)}…")
                self._ver_status.set(f"Done — {ok} healthy, {fail} errors")
                self._log(f"\nVerify [KVH] complete: {ok} healthy, {fail} errors", "accent")
                self.after(0, lambda: self._ver_run_btn.configure(state="normal"))

            threading.Thread(target=kvh_worker, daemon=True).start()
            return

        # ── ESLock path ───────────────────────────────────────────────────────
        key_override = self._resolve_ver_key()
        if self._ver_key_mode.get() != "embedded" and key_override is None:
            return

        files = ([src_path] if src_path.is_file() else
                 list(src_path.rglob("*.eslock")))
        if not files:
            messagebox.showinfo("No files", "No .eslock files found.")
            return

        self._ver_run_btn.configure(state="disabled")
        self._ver_progress["maximum"] = len(files)
        self._ver_progress["value"] = 0
        self._log_section("VERIFY")

        def worker():
            ok = fail = warn = 0
            failures: list[tuple[str, str]] = []
            for i, f in enumerate(files):
                try:
                    info = verify_eslock(f, key_override)
                    key_str = info["key"].hex().upper() if info["key"] else "?"
                    enc = (f"Partial {info['block_size']}B" if info["is_partial"] else "Full")
                    crc_ok = "OK" if info["crc_valid"] else "FAIL"
                    # ── Key mismatch check ──────────────────────────────────
                    mismatch = None
                    if key_override and info["key"]:
                        mismatch = _key_mismatch_msg(key_override, info["key"])
                    if mismatch:
                        reason = f"Wrong password/key — supplied key differs from embedded key ({key_str})"
                        status = "WRONG KEY"
                        tag = "fail"
                        size_str = _fmt_size(f.stat().st_size)
                        self.after(0, lambda row=(f.name, size_str, enc, crc_ok, key_str, status), t=tag:
                                   self._ver_tree.insert("", "end", values=row, tags=(t,)))
                        self._log(f"[WARN] {f.name}\n      Reason : {reason}", "yellow")
                        failures.append((f.name, reason))
                        fail += 1
                    else:
                        status = "HEALTHY" if info["verify_ok"] else "CORRUPT"
                        tag = "ok" if (info["verify_ok"] and info["crc_valid"]) else \
                              "warn" if info["verify_ok"] else "fail"
                        size_str = _fmt_size(f.stat().st_size)
                        self.after(0, lambda row=(f.name, size_str, enc, crc_ok, key_str, status), t=tag:
                                   self._ver_tree.insert("", "end", values=row, tags=(t,)))
                        if tag == "ok":    ok += 1
                        elif tag == "warn": warn += 1
                        else:
                            fail += 1
                            failures.append((f.name, f"Corrupt — CRC={crc_ok}"))
                        self._log(
                            f"{'[OK]  ' if info['verify_ok'] else '[FAIL]'} "
                            f"{f.name}  CRC={'OK' if info['crc_valid'] else 'FAIL'}  "
                            f"key={info['key'].hex().upper()}",
                            "green" if info["verify_ok"] else "red")
                    # ───────────────────────────────────────────────────────
                except Exception as exc:
                    tb = traceback.format_exc().strip()
                    reason = str(exc) or type(exc).__name__
                    size_str = _fmt_size(f.stat().st_size) if f.exists() else "?"
                    row = (f.name, size_str, "?", "?", "?", "ERROR")
                    self.after(0, lambda r=row: self._ver_tree.insert("", "end", values=r, tags=("fail",)))
                    self._log(f"[ERR] {f.name}\n      Reason : {reason}\n      Detail : {tb}", "red")
                    failures.append((f.name, reason))
                    fail += 1
                self.after(0, lambda v=i+1: self._ver_progress.configure(value=v))
                self._ver_status.set(f"Checking {i+1}/{len(files)}…")

            self._ver_status.set(f"Done — {ok} healthy, {warn} warnings, {fail} errors")
            self._log(f"\nVerify complete: {ok} healthy, {warn} warnings, {fail} errors", "accent")
            if failures:
                self._log(f"\nFailed files ({len(failures)}):", "yellow")
                for name, reason in failures:
                    self._log(f"  • {name}\n    {reason}", "yellow")
            self.after(0, lambda: self._ver_run_btn.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _log(self, msg: str, tag: str = ""):
        def _write():
            self._log_widget.configure(state="normal")
            if tag:
                self._log_widget.insert("end", msg + "\n", tag)
            else:
                self._log_widget.insert("end", msg + "\n")
            self._log_widget.see("end")
            self._log_widget.configure(state="disabled")
        self.after(0, _write)

    def _log_section(self, title: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log(f"\n{'─'*30} {title}  [{ts}]  {'─'*30}", "muted")

    def _log_welcome(self):
        self._log(f"KvhLocker v{VERSION}  ·  AES-CFB-128  ·  Ready", "accent")
        self._log("Select a tab (Encrypt / Decrypt / Verify), configure options, and click Run.", "muted")

    def _save_log(self):
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = filedialog.asksaveasfilename(
            title="Save log",
            initialfile=f"eslock-log-{ts}.txt",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        content = self._log_widget.get("1.0", "end")
        try:
            Path(path).write_text(content, encoding="utf-8")
            self._log(f"Log saved → {path}", "accent")
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc))

    def _clear_log(self):
        self._log_widget.configure(state="normal")
        self._log_widget.delete("1.0", "end")
        self._log_widget.configure(state="disabled")


# ─── Entry ────────────────────────────────────────────────────────────────────

def _resource(name: str) -> Path:
    """Resolve a bundled resource (works both as script and PyInstaller exe)."""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / name
    return Path(__file__).resolve().parent.parent / name


def main():
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Rewolf.KvhLocker.1.0"
        )
    except Exception:
        pass
    app = ESLockIDE()
    ico = _resource("Rewolf.ico")
    if ico.exists():
        try:
            app.iconbitmap(str(ico))
        except Exception:
            pass
    app.mainloop()

if __name__ == "__main__":
    main()
