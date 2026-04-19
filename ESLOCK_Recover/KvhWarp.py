#!/usr/bin/env python
"""
KvhWarp.py — File Stealth Tool
Encrypt first 1 KB of files + hide filename/date, rename to timestamp.ks
Decrypt .ks files back to original name and date.

Requires: pip install pycryptodome
"""
"""
KvhWarp - Fast Stealth File Encryption Tool

SECURITY DOCUMENTATION
======================

CRYPTOGRAPHIC SECURITY:
- Uses AES-256-GCM for authenticated encryption (prevents tampering)
- Supports two key derivation functions:
  * SHA-256: Fast but vulnerable to dictionary attacks
  * scrypt: Memory-hard, resistant to GPU/ASIC attacks (RECOMMENDED)
- Each file gets unique salt when using scrypt for better security
- Random nonces prevent replay attacks

OPERATIONAL SECURITY:
- In-place mode: Only encrypts first 1KB for speed on large files
  * LIMITATION: Rest of file remains plaintext
  * Suitable for large videos where headers contain identifying info
  * NOT suitable for confidential documents requiring full encryption
- Copy mode: Full file encryption, original untouched
- Timestamp-based filenames provide stealth but not cryptographic security
- Original metadata (filename, size) stored encrypted within .ks files

THREAT MODEL & LIMITATIONS:
- Designed for STEALTH, not military-grade security
- Password length is critical - use strong passwords with scrypt KDF
- In-place mode assumes first 1KB contains identifying file information
- Tool does not protect against:
  * Memory dumps while running
  * Keylogger attacks
  * Physical access to unlocked device
  * Advanced forensic recovery techniques (file slack, swap files)
- No secure deletion of temporary data or password clearing from memory

RECOMMENDED USAGE:
- Use scrypt KDF for all encryption (stronger against brute force)
- Use in-place mode for large video files
- Use copy mode for important documents requiring full encryption
- Use strong passwords (20+ characters, mixed case, numbers, symbols)
- Regularly update to latest version for security fixes

DEPENDENCIES:
- pycryptodome: Industry-standard crypto library
- Python 3.8+: Latest Python version for security patches
"""

import hashlib
import json
import os
import random
import shutil
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Optional, List, Tuple, Union

try:
    from Crypto.Cipher import AES
except ImportError:
    print("Error: pycryptodome is required. Install with: pip install pycryptodome")
    sys.exit(1)


_APP_VERSION   = "1.0.3"
_APP_NAME      = "KvhWarp"
_APP_COPYRIGHT = "© 2026 KVH"

HERE = (Path(sys.executable).parent if getattr(sys, "frozen", False)
        else Path(__file__).parent)
if getattr(sys, "frozen", False):
    _ICO_FILE = str(Path(sys._MEIPASS) / "Rewolf.ico")
else:
    _ICO_FILE = str(HERE.parent / "Rewolf.ico")
OPTIONS_FILE = str(HERE / "kvhwarp_options.json")

_DEFAULTS: dict = {
    "last_folder": "",
    "encrypt_size": 1024,
    "encrypt_all": False,
    "password_enhancement": False,
    "enhancement_warning_shown": False,
    "after_decrypt": "auto",   # "auto" | "confirm" | "folder"
    "keep_encrypted": False,
    "theme": "Dark",
    # Middle CTR defaults (center-anchored)
    "encrypt_middle_copy":    True,
    "encrypt_middle_inplace": False,
    "encrypt_middle_size":    1_048_576,
    # Range CTR defaults
    "encrypt_range":   False,
    "range_mode":      "auto",   # "auto" | "manual"
    "range_percent":   25,
    "range_start_mb":  0.0,
    "range_end_mb":    0.0,
    "range_b":         1,
    "range_c":         4,
    "range_unit":      "KB",
    # Adaptive compression defaults (copy mode only)
    "compress_copy":       False,
    "compress_max_mb":     500,
    "compress_skip_exts": [
        ".7z", ".aac", ".apk", ".avi", ".avif", ".bmp", ".br",
        ".dmg", ".docx", ".epub", ".flac", ".flv", ".gif",
        ".gz", ".heic", ".heif", ".hevc", ".img", ".ipa",
        ".iso", ".jar", ".jpg", ".jpeg", ".lz", ".lz4", ".lzma",
        ".m4a", ".m4v", ".mkv", ".mov", ".mp3", ".mp4",
        ".odt", ".ods", ".odp", ".ogg", ".opus",
        ".png", ".pptx", ".rar",
        ".vmdk", ".webm", ".webp", ".wmv", ".wma",
        ".xlsx", ".xz", ".zip", ".zst", ".zz",
    ],
}

MIN_FILE_SIZE = 1024
ENCRYPT_SIZE = 1024
KS_EXT = ".ks"
COPY_CHUNK_SIZE = 64 * 1024  # 64KB for streaming large files
MAX_TIMESTAMP_ATTEMPTS = 100  # Prevent infinite loops in timestamp generation
MAX_META_LEN = 1_000_000  # Sanity limit for metadata length




# ?�?� Options ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
def _load_opts() -> dict[str, Union[str, int]]:
    """Load application options from JSON file.
    
    Returns:
        Dictionary containing application options with defaults applied.
        
    Example:
        >>> opts = _load_opts()
        >>> print(opts.get('last_folder', ''))
        
    Security: File is loaded from local directory, no remote access.
    """
    opts = dict(_DEFAULTS)
    try:
        with open(OPTIONS_FILE, encoding="utf-8") as f:
            opts.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        # Silently use defaults if options file is inaccessible
        pass
    return opts


def _save_opts(opts: dict[str, Union[str, int]]) -> None:
    """Save application options to JSON file.
    
    Args:
        opts: Dictionary of options to save.
        
    Example:
        >>> opts = {'last_folder': '/path/to/folder'}
        >>> _save_opts(opts)
        
    Security: Writes to local file only, no sensitive data stored.
    """
    try:
        with open(OPTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(opts, f, indent=2)
    except (PermissionError, OSError) as e:
        # Fail silently if unable to save options
        pass


# ?�?� Crypto helpers ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
KDF_SHA256 = 0
KDF_SCRYPT = 1

_SCRYPT_N = 2**16
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 128 * 1024 * 1024  # 128 MB


def _derive_key(password: str, kdf: int = KDF_SHA256, salt: bytes = b"") -> bytes:
    """Derive 32-byte AES-256 key from password.
    
    Args:
        password: User password for encryption/decryption.
        kdf: Key derivation function type (0=SHA-256, 1=scrypt).
        salt: Random salt bytes (required for scrypt, ignored for SHA-256).
        
    Returns:
        32-byte encryption key.
        
    Example:
        >>> salt = os.urandom(16)
        >>> key = _derive_key("mypassword", KDF_SCRYPT, salt)
        >>> len(key)
        32
        
    Security:
        - SHA-256: Fast but vulnerable to dictionary attacks
        - scrypt: Memory-hard, resistant to GPU/ASIC attacks
        - Always use scrypt with random salt for production
    """
    try:
        if kdf == KDF_SCRYPT:
            if len(salt) < 16:
                raise ValueError("scrypt requires at least 16 bytes of salt")
            return hashlib.scrypt(
                password.encode("utf-8"), salt=salt,
                n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
                dklen=32, maxmem=_SCRYPT_MAXMEM,
            )
        return hashlib.sha256(password.encode("utf-8")).digest()
    except Exception as e:
        raise ValueError(f"Key derivation failed: {e}")


def _encrypt_blob(key: bytes, plaintext: bytes) -> bytes:
    """Encrypt data using AES-GCM.
    
    Args:
        key: 32-byte AES-256 key.
        plaintext: Data to encrypt.
        
    Returns:
        Encrypted blob: nonce(12) + tag(16) + ciphertext.
        
    Example:
        >>> key = os.urandom(32)
        >>> data = b"Hello, World!"
        >>> encrypted = _encrypt_blob(key, data)
        >>> len(encrypted) == len(data) + 28  # 12 nonce + 16 tag
        True
        
    Security:
        - Uses AES-GCM for authenticated encryption
        - Random nonce prevents replay attacks
        - Authentication tag prevents tampering
    """
    if len(key) != 32:
        raise ValueError("Key must be exactly 32 bytes")
    cipher = AES.new(key, AES.MODE_GCM, nonce=os.urandom(12))
    ct, tag = cipher.encrypt_and_digest(plaintext)
    return cipher.nonce + tag + ct


def _decrypt_blob(key: bytes, blob: bytes) -> bytes:
    """Decrypt AES-GCM encrypted data.
    
    Args:
        key: 32-byte AES-256 key.
        blob: Encrypted data (nonce + tag + ciphertext).
        
    Returns:
        Decrypted plaintext.
        
    Raises:
        ValueError: If key is wrong or data was tampered with.
        
    Example:
        >>> key = os.urandom(32)
        >>> data = b"Hello, World!"
        >>> encrypted = _encrypt_blob(key, data)
        >>> decrypted = _decrypt_blob(key, encrypted)
        >>> data == decrypted
        True
        
    Security:
        - Verifies authentication tag before decrypting
        - Raises exception on any tampering or wrong key
    """
    if len(key) != 32:
        raise ValueError("Key must be exactly 32 bytes")
    if len(blob) < 28:
        raise ValueError("Blob too short for AES-GCM format")
    nonce, tag, ct = blob[:12], blob[12:28], blob[28:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    return cipher.decrypt_and_verify(ct, tag)


# ── v2 format helpers ─────────────────────────────────────────────────────────

def _pw_masks(password: str) -> tuple:
    """Return (magic_mask[4B], salt_off_mask int, meta_off_mask int, salt_xor[16B])
    derived from SHA256(password)[0:22].  Used to XOR-obfuscate all header fields."""
    h = hashlib.sha256(password.encode("utf-8")).digest()
    return h[0:4], h[4], h[5], h[6:22]


def _encrypt_meta(key: bytes, metadata: dict) -> bytes:
    """Encrypt metadata dict with AES-256-GCM.  Returns nonce(12)+tag(16)+ciphertext."""
    plaintext = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
    nonce     = os.urandom(12)
    cipher    = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ct, tag   = cipher.encrypt_and_digest(plaintext)
    return nonce + tag + ct


def _decrypt_meta(key: bytes, blob: bytes) -> dict:
    """Decrypt metadata blob.  All failure paths raise identical ValueError (no oracle)."""
    try:
        if len(blob) < 28:
            raise ValueError()
        nonce, tag, ct = blob[:12], blob[12:28], blob[28:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return json.loads(cipher.decrypt_and_verify(ct, tag).decode("utf-8"))
    except Exception:
        raise ValueError("Wrong password or corrupted file")


def _middle_region(file_size: int, encrypt_size: int,
                   do_encrypt_tail: bool, encrypt_middle_size: int) -> tuple:
    """Compute center-anchored middle CTR region.  Returns (actual_start, actual_size)."""
    gap_start = encrypt_size
    gap_end   = file_size - encrypt_size if do_encrypt_tail else file_size
    if gap_end <= gap_start:
        return 0, 0
    file_mid      = file_size // 2
    desired_start = file_mid - encrypt_middle_size // 2
    desired_end   = file_mid + encrypt_middle_size // 2
    actual_start  = max(desired_start, gap_start)
    actual_end    = min(desired_end,   gap_end)
    actual_size   = max(0, actual_end - actual_start)
    return actual_start, actual_size


def _apply_ctr(key_suffix: bytes, nonce: bytes, data: bytes, base_key: bytes) -> bytes:
    """Apply AES-256-CTR keystream to data (symmetric: encrypt == decrypt).
    CTR key = SHA256(base_key + key_suffix)."""
    ctr_key = hashlib.sha256(base_key + key_suffix).digest()
    cipher  = AES.new(ctr_key, AES.MODE_CTR, nonce=nonce)
    return cipher.encrypt(data)


def _apply_range_ctr_chunk(chunk: bytearray, chunk_abs_start: int,
                            range_start: int, range_end: int,
                            b_bytes: int, c_bytes: int,
                            range_key: bytes, range_nonce: bytes) -> bytearray:
    """Apply B/C block-stride CTR to a chunk buffer (symmetric: same call encrypts and decrypts).
    Period-based: one AES-CTR cipher created per intersecting period, bulk encrypt() in C.
    O(chunk_size / c_bytes) ciphers instead of one per byte."""
    from Crypto.Util import Counter
    c_blks    = max(1, c_bytes // 16)
    chunk_end = chunk_abs_start + len(chunk)

    # Quick exit if chunk is entirely outside the encrypted range
    if chunk_end <= range_start or chunk_abs_start >= range_end:
        return chunk

    # First period that could overlap this chunk
    first_idx  = max(0, (chunk_abs_start - range_start) // c_bytes)
    period_idx = first_idx
    while True:
        period_start = range_start + period_idx * c_bytes
        if period_start >= range_end or period_start >= chunk_end:
            break

        # Encrypted bytes of this period [enc_start, enc_end)
        enc_start = period_start
        enc_end   = min(period_start + b_bytes, range_end)

        # Intersection with the current chunk
        isect_start = max(enc_start, chunk_abs_start)
        isect_end   = min(enc_end,   chunk_end)

        if isect_start < isect_end:
            length    = isect_end - isect_start
            enc_off   = isect_start - enc_start   # offset within encrypted portion
            start_blk = enc_off // 16
            byte_off  = enc_off % 16              # sub-block byte offset

            ctr_obj = Counter.new(64, prefix=range_nonce,
                                  initial_value=period_idx * c_blks + start_blk,
                                  little_endian=False)
            cipher  = AES.new(range_key, AES.MODE_CTR, counter=ctr_obj)
            # Advance cipher to the correct sub-block byte (rare: only when chunk starts
            # mid-block at an unaligned offset within a period)
            if byte_off:
                cipher.encrypt(b"\x00" * byte_off)

            off = isect_start - chunk_abs_start
            # cipher.encrypt() runs in C — no Python byte loop
            chunk[off : off + length] = bytearray(
                cipher.encrypt(bytes(chunk[off : off + length]))
            )

        period_idx += 1

    return chunk


def _range_auto_bc(percent: int, range_size: int) -> tuple:
    """Auto-compute (b_bytes, c_bytes) from coverage percent using simplest B/C fraction.
    range_size must be > 0 — callers must pass the actual region byte count."""
    if range_size <= 0:
        raise ValueError("range_size must be > 0 for auto mode")
    from fractions import Fraction
    if percent <= 0:
        return 16, 64        # 25% default
    if percent >= 100:
        aligned = max(16, (range_size // 16) * 16)
        return aligned, aligned
    max_c    = max(1, min(256, range_size // 16))
    f        = Fraction(percent, 100).limit_denominator(max_c)
    b_blocks = max(1, f.numerator)
    c_blocks = max(b_blocks, f.denominator)
    return b_blocks * 16, c_blocks * 16


def _range_resolve_bc(b_val: int, c_val: int, unit: str, range_size: int) -> tuple:
    """Resolve manual B/C + unit to 16-byte-aligned byte counts."""
    mul = {"B": 1, "byte": 1, "KB": 1024, "MB": 1_048_576}.get(unit, 1024)
    def _r16(n: int) -> int:
        return max(16, ((n + 15) // 16) * 16)
    b_bytes = _r16(b_val * mul)
    c_bytes = _r16(c_val * mul)
    if range_size > 0:
        c_bytes = min(c_bytes, max(16, range_size))
    b_bytes = min(b_bytes, c_bytes)
    return b_bytes, c_bytes


# ?�?� Password Enhancement ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�

# Cache for auto-passwords to avoid regeneration
_auto_password_cache = {}
_auto_password_lock = threading.Lock()

def _generate_auto_password(filepath: Path) -> str:
    """Generate content-based auto-password from file properties.
    
    Args:
        filepath: Path to file to generate password for.
        
    Returns:
        24-character deterministic password based on file content.
        
    Example:
        >>> auto_pw = _generate_auto_password(Path("video.mp4"))
        >>> len(auto_pw)
        24
        
    Security:
        - Optimized content-based generation
        - Reduced I/O for better performance
        - Same file always produces same password
        - Different files produce different passwords
    """
    # Cache key based on path and modification time for performance
    try:
        stat = filepath.stat()
        cache_key = (str(filepath), stat.st_size, stat.st_mtime_ns)
        
        # Check cache first
        with _auto_password_lock:
            if cache_key in _auto_password_cache:
                return _auto_password_cache[cache_key]
        
        import base64
        
        file_size = struct.pack("<Q", stat.st_size)
        
        # Optimized content fingerprinting with minimal I/O
        with open(filepath, "rb") as f:
            if stat.st_size <= 1024 * 1024:  # Small files (<1MB): single read
                content_hash = hashlib.sha256(f.read()).digest()
            else:  # Large files: minimal strategic sampling
                samples = []
                
                # Essential positions only
                f.seek(0)
                samples.append(f.read(512))  # First 512 bytes
                
                f.seek(-512, 2)  
                samples.append(f.read(512))  # Last 512 bytes
                
                # Single middle sample
                f.seek(stat.st_size // 2)
                samples.append(f.read(512))  # Middle 512 bytes
                
                # Combine samples
                combined = b''.join(samples)
                content_hash = hashlib.sha256(combined).digest()
        
        # Simplified strengthening (single round)
        strengthened = hashlib.pbkdf2_hmac('sha256', content_hash, file_size, 1000, dklen=32)
        
        # Simple encoding
        encoded = base64.b64encode(strengthened).decode()[:24].ljust(24, 'A')
        
        # Cache result (limit cache size to prevent memory growth)
        with _auto_password_lock:
            if len(_auto_password_cache) < 1000:
                _auto_password_cache[cache_key] = encoded
        
        return encoded
        
    except Exception as e:
        # Fallback to simple hash if auto-generation fails
        fallback_data = str(filepath).encode()
        return hashlib.sha256(fallback_data).digest().hex()[:24]


def _generate_hybrid_password(user_password: str, filepath: Path) -> str:
    """Generate hybrid password mixing user input with file fingerprint.
    
    Args:
        user_password: User-provided password.
        filepath: Path to file being encrypted.
        
    Returns:
        Strong hybrid password (43 characters if user password provided).
        
    Example:
        >>> hybrid = _generate_hybrid_password("hello", Path("file.txt"))
        >>> len(hybrid)
        43
        
    Security:
        - Combines user entropy with file-specific entropy
        - HMAC-based mixing prevents simple separation
        - Short user passwords become cryptographically strong
    """
    try:
        import base64
        import hmac
        
        if not user_password:  # Pure auto mode
            return _generate_auto_password(filepath)
        
        # Generate auto component
        auto_password = _generate_auto_password(filepath)
        
        # Normalize inputs
        user_bytes = user_password.encode('utf-8')[:64]  # Limit length
        auto_bytes = auto_password.encode('utf-8')[:32]
        
        # Bidirectional HMAC mixing
        mix_forward = hmac.new(user_bytes, auto_bytes, hashlib.sha256).digest()   # 32 bytes
        mix_reverse = hmac.new(auto_bytes, user_bytes, hashlib.sha256).digest()   # 32 bytes
        combined = mix_forward + mix_reverse  # 64 bytes total
        
        # Final encoding  
        final_b64 = base64.b64encode(combined)
        final_password = final_b64.decode()[:43]  # ~43 chars for 64 bytes
        
        return final_password
        
    except Exception as e:
        # Fallback to user password only if hybrid generation fails
        return user_password


# ?�?� File Format Constants ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�

# Internal magic references — v2 never writes these as plaintext
_MAGIC         = b"KWRP"
_MAGIC_INPLACE = b"KWRI"
# Public aliases kept for compatibility
MAGIC         = _MAGIC
MAGIC_INPLACE = _MAGIC_INPLACE

_SALT_SIZE          = 16          # KDF salt size in bytes
ENCRYPT_MIDDLE_SIZE = 1_048_576   # Default center-anchored middle CTR region (1 MB)

# ── Adaptive compression (copy mode only) ────────────────────────────────────

# Hardcoded fallback — used only if opts key is absent or JSON is missing.
_SKIP_COMPRESS_EXTS_DEFAULT: frozenset = frozenset([
    ".7z", ".aac", ".apk", ".avi", ".avif", ".bmp", ".br",
    ".dmg", ".docx", ".epub", ".flac", ".flv", ".gif",
    ".gz", ".heic", ".heif", ".hevc", ".img", ".ipa",
    ".iso", ".jar", ".jpg", ".jpeg", ".lz", ".lz4", ".lzma",
    ".m4a", ".m4v", ".mkv", ".mov", ".mp3", ".mp4",
    ".odt", ".ods", ".odp", ".ogg", ".opus",
    ".png", ".pptx", ".rar",
    ".vmdk", ".webm", ".webp", ".wmv", ".wma",
    ".xlsx", ".xz", ".zip", ".zst", ".zz",
])


def _skip_exts_from_opts(opts: dict) -> frozenset:
    """Build a normalised frozenset of skip-extensions from opts.
    Each entry is lowercased and guaranteed to start with '.'.
    Falls back to _SKIP_COMPRESS_EXTS_DEFAULT if key absent."""
    raw = opts.get("compress_skip_exts")
    if not raw:
        return _SKIP_COMPRESS_EXTS_DEFAULT
    result = set()
    for e in raw:
        e = e.strip().lower()
        if not e:
            continue
        if not e.startswith("."):
            e = "." + e
        result.add(e)
    return frozenset(result) if result else _SKIP_COMPRESS_EXTS_DEFAULT


def _shannon_entropy(data: bytes) -> float:
    """Return Shannon entropy of *data* in bits per byte (range 0–8)."""
    if not data:
        return 0.0
    from math import log2
    n = len(data)
    freq = [0] * 256
    for b in data:
        freq[b] += 1
    return -sum((c / n) * log2(c / n) for c in freq if c)


def _sample_entropy(filepath: Path) -> float:
    """Read up to 3 KB (head + middle + tail) and return Shannon entropy."""
    file_size = filepath.stat().st_size
    with open(filepath, "rb") as f:
        if file_size <= 3072:
            data = f.read()
        else:
            f.seek(0)
            head = f.read(1024)
            f.seek(file_size // 2 - 512)
            mid = f.read(1024)
            f.seek(file_size - 1024)
            tail = f.read(1024)
            data = head + mid + tail
    return _shannon_entropy(data)


def _zstd_level_for_entropy(entropy: float) -> Optional[int]:
    """Map Shannon entropy to a Zstandard compression level, or None to skip.

    entropy < 5.5          → level 10  (highly compressible)
    5.5 ≤ entropy ≤ 6.8   → level 9..3 (linear interpolation)
    entropy > 6.8          → None (skip — already dense / pre-compressed)
    """
    if entropy < 5.5:
        return 10
    if entropy <= 6.8:
        level = round(9 - (entropy - 5.5) / 1.3 * 6)
        return max(3, min(9, level))
    return None


def _compress_auto(
    src: Path,
    size_limit: int = 0,
    skip_exts: Optional[frozenset] = None,
) -> Tuple[Optional[Path], Optional[str]]:
    """Try to compress *src* with Zstandard; return (temp_path, algo_tag) or (None, None).

    Three ordered gates — any failure returns (None, None) immediately:
    1. Extension gate  : suffix in skip_exts (or default list)
    2. Size gate       : file larger than size_limit (0 = unlimited)
    3. Entropy gate    : sample entropy too high for worthwhile compression
    4. Savings guard   : compressed size must be < orig * 0.95

    Requires: pip install zstandard
    """
    import tempfile

    # Gate 1 — extension
    exts = skip_exts if skip_exts is not None else _SKIP_COMPRESS_EXTS_DEFAULT
    if src.suffix.lower() in exts:
        return None, None

    # Gate 2 — size limit
    orig_size = src.stat().st_size
    if size_limit > 0 and orig_size > size_limit:
        return None, None

    # Gate 3 — entropy
    try:
        entropy = _sample_entropy(src)
    except Exception:
        return None, None
    level = _zstd_level_for_entropy(entropy)
    if level is None:
        return None, None

    # Compress to a temp file
    try:
        import zstandard as zstd
    except ImportError:
        return None, None

    tmp_dir = Path(tempfile.gettempdir()) / "KvhWarp"
    tmp_dir.mkdir(exist_ok=True)
    tmp_path = tmp_dir / f"{time.time_ns()}.zst"
    try:
        cctx = zstd.ZstdCompressor(level=level)
        with open(src, "rb") as fin, open(tmp_path, "wb") as fout:
            with cctx.stream_writer(fout, closefd=False) as writer:
                while True:
                    chunk = fin.read(COPY_CHUNK_SIZE)
                    if not chunk:
                        break
                    writer.write(chunk)
        comp_size = tmp_path.stat().st_size
    except Exception:
        try:
            tmp_path.unlink()
        except Exception:
            pass
        return None, None

    # Gate 4 — savings guard (must save ≥5%)
    if comp_size >= orig_size * 0.95:
        try:
            tmp_path.unlink()
        except Exception:
            pass
        return None, None

    return tmp_path, f"zstd:{level}"


def _decompress_inplace(path: Path, algo: str, expected_size: int) -> None:
    """Decompress *path* (written by _compress_auto) back to its original content.

    Decompresses to a sibling *.dec_tmp* file, verifies byte count, then
    atomically replaces *path*.
    """
    if not algo.startswith("zstd:"):
        raise ValueError(f"Unknown compress_algo: {algo!r}")
    try:
        import zstandard as zstd
    except ImportError:
        raise RuntimeError("zstandard package required to decrypt this file — pip install zstandard")

    tmp_path = path.parent / (path.name + ".dec_tmp")
    try:
        dctx = zstd.ZstdDecompressor()
        written = 0
        with open(path, "rb") as fin, open(tmp_path, "wb") as fout:
            with dctx.stream_reader(fin) as reader:
                while True:
                    chunk = reader.read(COPY_CHUNK_SIZE)
                    if not chunk:
                        break
                    fout.write(chunk)
                    written += len(chunk)
        if written != expected_size:
            raise ValueError(
                f"Decompressed size mismatch: expected {expected_size}, got {written}"
            )
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink()
        except Exception:
            pass
        raise

# v2 File Format — Copy mode (.ks):
#   enc_magic(4) | enc_salt_off(1) | salt_prefix(0-14B) | enc_meta_off(1) |
#   enc_meta_prefix(var) | salt_suffix(var) | meta_enc_len(4) | enc_meta_suffix(var) |
#   enc_head_blob | [middle ± CTR] | [enc_tail_blob]
#   All magic/salt/metadata fields XOR-masked with SHA256(password) bytes.
#
# v2 File Format — In-place footer (.ks, read from end):
#   enc_magic(4) | head_tag(16) | head_nonce(12) | masked_salt(16) |
#   meta_enc_len(4) | enc_meta(var)
#   KDF encoded in salt[0] LSB: 0=SHA-256, 1=scrypt.


def is_warped(filepath: Path, password: str = "") -> bool:
    """Check if a file is already encrypted by KvhWarp (v2 format).

    If *password* is provided the check is exact: XOR-unmask the magic bytes and
    compare to KWRP / KWRI.  Without a password the check falls back to the .ks
    extension heuristic (documented as imperfect — any .ks file will match).

    Returns False on any file access error (fail-safe).
    """
    try:
        if password:
            mask = _pw_masks(password)[0]
            with open(filepath, "rb") as f:
                raw = f.read(4)
                if len(raw) == 4 and bytes(a ^ b for a, b in zip(raw, mask)) == b"KWRP":
                    return True
                f.seek(-4, 2)
                raw = f.read(4)
                if len(raw) == 4 and bytes(a ^ b for a, b in zip(raw, mask)) == b"KWRI":
                    return True
            return False
        # No password: extension heuristic (imperfect — may false-positive on .ks files)
        return filepath.suffix.lower() == KS_EXT
    except Exception:
        return False


def _generate_unique_filename(directory: Path, extension: str) -> str:
    """Generate a unique timestamp-based filename.
    
    Args:
        directory: Directory where file will be created.
        extension: File extension (e.g., '.ks').
        
    Returns:
        Unique filename.
        
    Example:
        >>> filename = _generate_unique_filename(Path('/tmp'), '.ks')
        >>> filename.endswith('.ks')
        True
    """
    for attempt in range(MAX_TIMESTAMP_ATTEMPTS):
        ts_name = f"{time.time_ns()}{extension}"
        if not (directory / ts_name).exists():
            return ts_name
        time.sleep(0.001)  # Brief delay to ensure different timestamp
    # Fallback with random suffix if timestamp collision persists
    import random
    return f"{time.time_ns()}_{random.randint(1000, 9999)}{extension}"


def warp_file(
    filepath: Path,
    password: str,
    *,
    kdf: int = KDF_SCRYPT,
    base_folder: Optional[Path] = None,
    encrypt_size: int = ENCRYPT_SIZE,
    encrypt_tail: bool = False,
    encrypt_middle: bool = True,
    encrypt_middle_size: int = ENCRYPT_MIDDLE_SIZE,
    encrypt_range: bool = False,
    range_start: int = 0,
    range_end: int = 0,
    range_b_bytes: int = 16,
    range_c_bytes: int = 64,
    range_mode: str = "manual",
    range_percent: int = 25,
    compress: bool = False,
    compress_max_bytes: int = 0,
    compress_skip_exts: Optional[frozenset] = None,
) -> str:
    """Encrypt file to a .ks copy using v2 obfuscated format.

    Header magic, salt and encrypted metadata are XOR-masked with SHA256(password).
    Salt and encrypted-metadata blob are interleaved in the header.
    Optional center-anchored middle CTR and B/C-stride range CTR.
    Original file is never modified.
    """
    import time as _time
    start_time = _time.perf_counter()
    _compress_tmp: Optional[Path] = None
    compress_algo: Optional[str]  = None
    try:
        if is_warped(filepath, password):
            return f"SKIP: {filepath.name} (already warped)"

        file_size = filepath.stat().st_size
        if file_size == 0:
            return f"SKIP: {filepath.name} (empty)"
        if file_size < MIN_FILE_SIZE and encrypt_size > file_size:
            return f"SKIP: {filepath.name} (too small)"

        file_size_mb = file_size / (1024 * 1024)
        orig_mtime   = os.path.getmtime(filepath)
        orig_atime   = os.path.getatime(filepath)

        # ── Adaptive compression (copy mode) ──
        src_path = filepath
        src_size = file_size
        if compress:
            _compress_tmp, compress_algo = _compress_auto(
                filepath, compress_max_bytes, compress_skip_exts
            )
            if _compress_tmp is not None:
                src_path = _compress_tmp
                src_size = _compress_tmp.stat().st_size

        # ── Key derivation (salt generated here; kdf encoded in salt[0] LSB) ──
        salt = os.urandom(_SALT_SIZE)
        salt = bytes([salt[0] & 0xFE | (kdf & 0x01)]) + salt[1:]
        key  = _derive_key(password, kdf, salt)

        # ── Encrypt head (GCM blob: nonce+tag+ct) ──
        actual_head_size = min(encrypt_size, src_size)
        with open(src_path, "rb") as f:
            head_data = f.read(actual_head_size)
        encrypted_head = _encrypt_blob(key, head_data)

        # ── Encrypt tail ──
        do_encrypt_tail = encrypt_tail and (src_size >= 2 * encrypt_size)
        encrypted_tail  = None
        if do_encrypt_tail:
            with open(src_path, "rb") as f:
                f.seek(src_size - encrypt_size)
                tail_data = f.read(encrypt_size)
            encrypted_tail = _encrypt_blob(key, tail_data)

        # ── Middle CTR (center-anchored) ──
        m_start, m_size = _middle_region(src_size, actual_head_size,
                                         do_encrypt_tail, encrypt_middle_size)
        middle_nonce = None
        middle_ct    = None
        if encrypt_middle and m_size > 0:
            middle_nonce = os.urandom(8)
            with open(src_path, "rb") as f:
                f.seek(m_start)
                middle_plain = f.read(m_size)
            middle_ct = _apply_ctr(b"\x02", middle_nonce, middle_plain, key)

        # ── Range CTR ──
        r_start = max(0, range_start)
        r_end   = min(range_end if range_end > 0 else src_size, src_size)
        do_encrypt_range = encrypt_range and r_end > r_start
        if do_encrypt_range and range_mode == "auto":
            range_b_bytes, range_c_bytes = _range_auto_bc(range_percent, r_end - r_start)
        range_nonce     = None
        range_key_bytes = None
        if do_encrypt_range:
            range_nonce     = os.urandom(8)
            range_key_bytes = hashlib.sha256(key + b"\x03").digest()

        # ── Build and encrypt metadata ──
        do_enc_middle = encrypt_middle and m_size > 0
        metadata = {
            "orig_name":      filepath.name,
            "orig_size":      file_size,
            "encrypt_size":   actual_head_size,
            "encrypt_tail":   do_encrypt_tail,
            "encrypt_middle": do_enc_middle,
            "middle_start":   m_start if do_enc_middle else 0,
            "middle_size":    m_size  if do_enc_middle else 0,
            "middle_nonce":   middle_nonce.hex() if middle_nonce else None,
            "encrypt_range":  do_encrypt_range,
            "range_start":    r_start,
            "range_end":      r_end,
            "range_b_bytes":  range_b_bytes,
            "range_c_bytes":  range_c_bytes,
            "range_nonce":    range_nonce.hex() if range_nonce else None,
            "compress_algo":  compress_algo,
            "compress_size":  src_size,
        }
        enc_meta = _encrypt_meta(key, metadata)

        # ── Build v2 obfuscated header ──
        magic_mask, salt_off_m, meta_off_m, _ = _pw_masks(password)
        salt_split = random.randint(0, 14)
        meta_split = random.randint(1, min(255, max(1, len(enc_meta) - 1)))

        # ── Write output file ──
        ts_name     = _generate_unique_filename(filepath.parent, KS_EXT)
        output_path = filepath.parent / ts_name

        with open(output_path, "wb") as dest_file:
            # Obfuscated header
            dest_file.write(bytes(a ^ b for a, b in zip(b"KWRP", magic_mask)))  # enc_magic (4)
            dest_file.write(bytes([salt_split ^ salt_off_m]))                    # enc_salt_off (1)
            dest_file.write(salt[:salt_split])                                   # salt_prefix
            dest_file.write(bytes([meta_split ^ meta_off_m]))                   # enc_meta_off (1)
            dest_file.write(enc_meta[:meta_split])                              # enc_meta_prefix
            dest_file.write(salt[salt_split:])                                  # salt_suffix
            dest_file.write(struct.pack("<I", len(enc_meta)))                   # meta_enc_len (4)
            dest_file.write(enc_meta[meta_split:])                             # enc_meta_suffix

            # Encrypted head blob
            dest_file.write(encrypted_head)

            # Stream middle gap with optional CTR transforms
            middle_gap = src_size - actual_head_size - (encrypt_size if do_encrypt_tail else 0)
            if middle_gap > 0:
                with open(src_path, "rb") as src:
                    src.seek(actual_head_size)
                    remaining = middle_gap
                    abs_pos   = actual_head_size
                    while remaining > 0:
                        chunk_sz = min(COPY_CHUNK_SIZE, remaining)
                        chunk    = bytearray(src.read(chunk_sz))
                        if not chunk:
                            break

                        # Apply middle CTR (pre-computed bytes)
                        if do_enc_middle and middle_ct is not None:
                            for i in range(len(chunk)):
                                p = abs_pos + i
                                if m_start <= p < m_start + m_size:
                                    chunk[i] = middle_ct[p - m_start]

                        # Apply range CTR
                        if do_encrypt_range and range_key_bytes is not None:
                            chunk = _apply_range_ctr_chunk(
                                chunk, abs_pos,
                                r_start, r_end,
                                range_b_bytes, range_c_bytes,
                                range_key_bytes, range_nonce,
                            )

                        dest_file.write(bytes(chunk))
                        abs_pos   += len(chunk)
                        remaining -= len(chunk)

            # Encrypted tail blob
            if do_encrypt_tail and encrypted_tail is not None:
                dest_file.write(encrypted_tail)

        os.utime(output_path, (orig_atime, orig_mtime))
        elapsed = _time.perf_counter() - start_time
        compress_note = f", zstd:{compress_algo.split(':')[1]}" if compress_algo else ""
        return f"OK: {filepath.name} -> {output_path.name} ({file_size_mb:.1f}MB{compress_note}, {elapsed:.2f}s)"

    except PermissionError:
        return f"ERR: {filepath.name} - Permission denied"
    except OSError:
        return f"ERR: {filepath.name} - File system error"
    except Exception:
        return f"ERR: {filepath.name} - Encryption failed"
    finally:
        if _compress_tmp is not None:
            try:
                _compress_tmp.unlink()
            except Exception:
                pass


def warp_file_inplace(
    filepath: Path,
    password: str,
    *,
    kdf: int = KDF_SCRYPT,
    base_folder: Optional[Path] = None,
    encrypt_size: int = ENCRYPT_SIZE,
    encrypt_tail: bool = False,
    encrypt_middle: bool = False,
    encrypt_middle_size: int = ENCRYPT_MIDDLE_SIZE,
    encrypt_range: bool = False,
    range_start: int = 0,
    range_end: int = 0,
    range_b_bytes: int = 16,
    range_c_bytes: int = 64,
    range_mode: str = "manual",
    range_percent: int = 25,
) -> str:
    """Encrypt file in-place using v2 footer format.

    Head and optional tail are AES-256-GCM encrypted in-place (same size).
    Footer (appended): enc_meta | meta_enc_len(4) | masked_salt(16) |
                       head_nonce(12) | head_tag(16) | enc_magic(4).
    Optional bounded middle CTR and range CTR keep I/O proportional to
    those regions, NOT to total file size.
    """
    import time as _time
    start_time = _time.perf_counter()
    try:
        if is_warped(filepath, password):
            return f"SKIP: {filepath.name} (already warped)"

        file_size = filepath.stat().st_size
        if file_size == 0:
            return f"SKIP: {filepath.name} (empty)"
        if file_size < MIN_FILE_SIZE and encrypt_size > file_size:
            return f"SKIP: {filepath.name} (too small)"

        file_size_mb     = file_size / (1024 * 1024)
        orig_size        = file_size
        orig_mtime       = os.path.getmtime(filepath)
        orig_atime       = os.path.getatime(filepath)
        actual_head_size = min(encrypt_size, orig_size)

        # ── Key derivation ──
        salt = os.urandom(_SALT_SIZE)
        salt = bytes([salt[0] & 0xFE | (kdf & 0x01)]) + salt[1:]
        key  = _derive_key(password, kdf, salt)

        # ── CTR region parameters ──
        do_encrypt_tail = encrypt_tail and (orig_size >= 2 * encrypt_size)
        m_start, m_size = _middle_region(orig_size, actual_head_size,
                                         do_encrypt_tail, encrypt_middle_size)
        middle_nonce = os.urandom(8) if (encrypt_middle and m_size > 0) else None

        r_start = max(0, range_start)
        r_end   = min(range_end if range_end > 0 else orig_size, orig_size)
        do_encrypt_range = encrypt_range and r_end > r_start
        if do_encrypt_range and range_mode == "auto":
            range_b_bytes, range_c_bytes = _range_auto_bc(range_percent, r_end - r_start)
        range_nonce     = os.urandom(8) if do_encrypt_range else None
        range_key_bytes = hashlib.sha256(key + b"\x03").digest() if do_encrypt_range else None

        # ── Modify file in-place ──
        with open(filepath, "r+b") as f:
            # Encrypt head (GCM: ct overwrites plaintext; nonce+tag go to footer)
            f.seek(0)
            head_data   = f.read(actual_head_size)
            head_cipher = AES.new(key, AES.MODE_GCM, nonce=os.urandom(12))
            ct_head, head_tag = head_cipher.encrypt_and_digest(head_data)
            f.seek(0)
            f.write(ct_head)
            head_nonce = head_cipher.nonce

            # Encrypt tail
            tail_nonce_hex = None
            tail_tag_hex   = None
            if do_encrypt_tail:
                f.seek(orig_size - encrypt_size)
                tail_data   = f.read(encrypt_size)
                tail_cipher = AES.new(key, AES.MODE_GCM, nonce=os.urandom(12))
                ct_tail, tag_tail = tail_cipher.encrypt_and_digest(tail_data)
                f.seek(orig_size - encrypt_size)
                f.write(ct_tail)
                tail_nonce_hex = tail_cipher.nonce.hex()
                tail_tag_hex   = tag_tail.hex()

            # Middle CTR (bounded, center-anchored)
            if encrypt_middle and m_size > 0 and middle_nonce is not None:
                f.seek(m_start)
                mid_plain = f.read(m_size)
                mid_ct    = _apply_ctr(b"\x02", middle_nonce, mid_plain, key)
                f.seek(m_start)
                f.write(mid_ct)

            # Range CTR (period by period — O(range_size) I/O only)
            if do_encrypt_range and range_key_bytes is not None:
                from Crypto.Util import Counter
                c_blks  = max(1, range_c_bytes // 16)
                pos     = r_start
                blk_idx = 0
                while pos < r_end:
                    period_end    = min(pos + range_c_bytes, r_end)
                    enc_end       = min(pos + range_b_bytes, period_end)
                    enc_in_period = enc_end - pos
                    if enc_in_period > 0:
                        f.seek(pos)
                        enc_data = f.read(enc_in_period)
                        ctr_obj  = Counter.new(64, prefix=range_nonce,
                                               initial_value=blk_idx, little_endian=False)
                        cipher   = AES.new(range_key_bytes, AES.MODE_CTR, counter=ctr_obj)
                        ks       = cipher.encrypt(b"\x00" * enc_in_period)
                        f.seek(pos)
                        f.write(bytes(a ^ b for a, b in zip(enc_data, ks)))
                    pos     += range_c_bytes
                    blk_idx += c_blks

        # ── Build and encrypt metadata ──
        do_enc_middle = encrypt_middle and m_size > 0
        metadata = {
            "orig_name":      filepath.name,
            "orig_size":      orig_size,
            "encrypt_size":   actual_head_size,
            "encrypt_tail":   do_encrypt_tail,
            "tail_nonce":     tail_nonce_hex,
            "tail_tag":       tail_tag_hex,
            "encrypt_middle": do_enc_middle,
            "middle_start":   m_start if do_enc_middle else 0,
            "middle_size":    m_size  if do_enc_middle else 0,
            "middle_nonce":   middle_nonce.hex() if middle_nonce else None,
            "encrypt_range":  do_encrypt_range,
            "range_start":    r_start,
            "range_end":      r_end,
            "range_b_bytes":  range_b_bytes,
            "range_c_bytes":  range_c_bytes,
            "range_nonce":    range_nonce.hex() if range_nonce else None,
        }
        enc_meta = _encrypt_meta(key, metadata)

        # ── Append v2 footer ──
        magic_mask, _, _, salt_xor = _pw_masks(password)
        masked_salt = bytes(s ^ x for s, x in zip(salt, salt_xor))

        with open(filepath, "ab") as f:
            f.write(enc_meta)                                                  # enc_meta (var)
            f.write(struct.pack("<I", len(enc_meta)))                          # meta_enc_len (4)
            f.write(masked_salt)                                               # masked_salt (16)
            f.write(head_nonce)                                                # head_nonce (12)
            f.write(head_tag)                                                  # head_tag (16)
            f.write(bytes(a ^ b for a, b in zip(b"KWRI", magic_mask)))        # enc_magic (4)

        # ── Rename and restore timestamps ──
        ts_name  = _generate_unique_filename(filepath.parent, KS_EXT)
        new_path = filepath.parent / ts_name
        filepath.rename(new_path)
        os.utime(new_path, (orig_atime, orig_mtime))

        elapsed = _time.perf_counter() - start_time
        return f"OK: {filepath.name} -> {new_path.name} ({file_size_mb:.1f}MB, {elapsed:.2f}s, in-place)"

    except PermissionError:
        return f"ERR: {filepath.name} - Permission denied"
    except OSError:
        return f"ERR: {filepath.name} - File system error"
    except Exception:
        return f"ERR: {filepath.name} - In-place encryption failed"

def rename_subfolders(base_folder: Path, user_password: str) -> List[str]:
    """Rename subfolders to timestamp-based names for stealth.
    
    Args:
        base_folder: Root directory to process.
        user_password: User password for encrypting folder mapping.
        
    Returns:
        List of status messages for each renamed folder.
        
    Example:
        >>> messages = rename_subfolders(Path("/files"), "mypassword")
        >>> for msg in messages:
        ...     print(msg)  # "Renamed: videos →1234567890"
        
    Security:
        - Obscures folder structure for stealth
        - Preserves encrypted folder structure mapping for restoration
        - Uses unique timestamps to avoid collisions
    """
    messages = []
    if not base_folder.is_dir():
        return messages
    
    # Collect folders first to avoid iteration issues during rename
    folders_to_rename = [item for item in base_folder.iterdir() if item.is_dir()]
    if not folders_to_rename:
        return messages
    
    # Store folder mapping for restoration
    folder_mapping = {}
    
    # Process subdirectories
    for item in folders_to_rename:
        try:
            ts_name = _generate_unique_filename(base_folder, "")
            new_path = base_folder / ts_name
            
            # Store original →renamed mapping
            folder_mapping[ts_name] = item.name
            
            item.rename(new_path)
            messages.append(f"Renamed: {item.name} -> {ts_name}")
        except (PermissionError, OSError) as e:
            messages.append(f"ERR: Could not rename {item.name}")
    
    # Save encrypted folder mapping in single I/O operation
    if folder_mapping:
        try:
            mapping_file = base_folder / ".kvh_folders.map"
            import json
            # Encrypt the mapping data for security
            mapping_json = json.dumps(folder_mapping).encode('utf-8')
            # Derive encryption key from user password and base folder name
            mapping_salt = hashlib.sha256(str(base_folder).encode() + b"folder_mapping").digest()[:16]
            mapping_key = hashlib.pbkdf2_hmac('sha256', user_password.encode('utf-8'), mapping_salt, 10000, dklen=32)
            
            # Encrypt mapping with AES-GCM
            cipher = AES.new(mapping_key, AES.MODE_GCM, nonce=os.urandom(12))
            ciphertext, tag = cipher.encrypt_and_digest(mapping_json)
            encrypted_mapping = cipher.nonce + tag + ciphertext
            
            # Atomic write to prevent corruption
            temp_file = mapping_file.with_suffix('.tmp')
            with open(temp_file, 'wb') as f:
                f.write(encrypted_mapping)
            temp_file.replace(mapping_file)  # replace() overwrites on all platforms
            
            # Hide the mapping file (non-blocking)
            try:
                import subprocess
                if os.name == 'nt':  # Windows
                    subprocess.run(['attrib', '+h', str(mapping_file)], 
                                 capture_output=True, timeout=1)
            except Exception:
                pass  # Hiding failed, but not critical
        except Exception:
            messages.append("Warning: Could not save folder mapping for restoration")
    
    return messages


def restore_subfolders(base_folder: Path, user_password: str) -> List[str]:
    """Restore original subfolder names from encrypted mapping file.
    
    Args:
        base_folder: Root directory containing renamed folders.
        user_password: User password for decrypting folder mapping.
        
    Returns:
        List of status messages for each restored folder.
        
    Security:
        - Decrypts folder mapping with user password
        - Removes mapping file after successful restoration
    """
    messages = []
    if not base_folder.is_dir():
        return messages
    
    mapping_file = base_folder / ".kvh_folders.map"
    if not mapping_file.exists():
        return messages  # No mapping file, nothing to restore
    
    try:
        import json
        with open(mapping_file, 'rb') as f:
            encrypted_mapping = f.read()
        
        # Decrypt the mapping data
        try:
            nonce = encrypted_mapping[:12]
            tag = encrypted_mapping[12:28]
            ciphertext = encrypted_mapping[28:]
            
            # Derive same encryption key from user password and base folder name
            mapping_salt = hashlib.sha256(str(base_folder).encode() + b"folder_mapping").digest()[:16]
            mapping_key = hashlib.pbkdf2_hmac('sha256', user_password.encode('utf-8'), mapping_salt, 10000, dklen=32)
            
            # Decrypt mapping
            cipher = AES.new(mapping_key, AES.MODE_GCM, nonce=nonce)
            mapping_json = cipher.decrypt_and_verify(ciphertext, tag)
            folder_mapping = json.loads(mapping_json.decode('utf-8'))
        except Exception as decrypt_error:
            messages.append(f"ERR: Could not decrypt folder mapping file (wrong password or corrupted file)")
            return messages
        
        # Batch collect existing folders to avoid iteration issues
        existing_folders = {item.name: item for item in base_folder.iterdir() if item.is_dir()}
        
        # Restore folder names: renamed_name →original_name
        for renamed_name, original_name in folder_mapping.items():
            renamed_path = existing_folders.get(renamed_name)
            
            if renamed_path and renamed_path.is_dir():
                try:
                    # Avoid conflicts if original name already exists
                    original_path = base_folder / original_name
                    counter = 1
                    target_path = original_path
                    while target_path.exists() and target_path != renamed_path:
                        target_path = base_folder / f"{original_name}_{counter}"
                        counter += 1
                    
                    renamed_path.rename(target_path)
                    if target_path == original_path:
                        messages.append(f"Restored: {renamed_name} -> {original_name}")
                    else:
                        messages.append(f"Restored: {renamed_name} -> {target_path.name}")
                except (PermissionError, OSError):
                    messages.append(f"ERR: Could not restore {renamed_name}")
        
        # Clean up mapping file after successful restoration
        try:
            mapping_file.unlink()
        except:
            pass  # File removal failed, but not critical
            
    except (json.JSONDecodeError, Exception):
        messages.append("ERR: Could not read folder mapping for restoration")
    
    return messages


def cleanup_empty_dirs(base_folder: Path) -> List[str]:
    """Remove empty directories after processing.
    
    Args:
        base_folder: Root directory to clean.
        
    Returns:
        List of status messages for each removed directory.
        
    Example:
        >>> messages = cleanup_empty_dirs(Path("/files"))
        >>> for msg in messages:
        ...     print(msg)  # "Removed empty: temp_folder"
        
    Security:
        - Only removes genuinely empty directories
        - Prevents accidental deletion of directories with hidden files
    """
    messages = []
    if not base_folder.is_dir():
        return messages
    
    # Process directories depth-first
    for item in sorted(base_folder.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        if item.is_dir():
            try:
                if not any(item.iterdir()):
                    item.rmdir()
                    messages.append(f"Removed empty: {item.relative_to(base_folder)}")
            except (PermissionError, OSError):
                messages.append(f"ERR: Could not remove {item.relative_to(base_folder)}")
    
    return messages


def _unwarp_copy(filepath: Path, password: str) -> str:
    """Decrypt v2 copy-mode .ks file."""
    import time as _time
    start_time = _time.perf_counter()
    try:
        magic_mask, salt_off_m, meta_off_m, _ = _pw_masks(password)

        with open(filepath, "rb") as f:
            # ── Parse v2 interleaved header ──
            raw_magic = f.read(4)
            if len(raw_magic) < 4 or bytes(a ^ b for a, b in zip(raw_magic, magic_mask)) != b"KWRP":
                raise ValueError("Wrong password or corrupted file")

            b = f.read(1)
            if not b:
                raise ValueError("Wrong password or corrupted file")
            salt_split = b[0] ^ salt_off_m
            if salt_split > 14:
                raise ValueError("Wrong password or corrupted file")
            salt_prefix = f.read(salt_split)

            b = f.read(1)
            if not b:
                raise ValueError("Wrong password or corrupted file")
            meta_split      = b[0] ^ meta_off_m
            enc_meta_prefix = f.read(meta_split)

            salt_suffix = f.read(_SALT_SIZE - salt_split)
            if len(salt_suffix) != _SALT_SIZE - salt_split:
                raise ValueError("Wrong password or corrupted file")

            raw_len = f.read(4)
            if len(raw_len) < 4:
                raise ValueError("Wrong password or corrupted file")
            meta_enc_len = struct.unpack("<I", raw_len)[0]
            if meta_enc_len > MAX_META_LEN or meta_enc_len <= meta_split:
                raise ValueError("Wrong password or corrupted file")

            enc_meta_suffix = f.read(meta_enc_len - meta_split)
            if len(enc_meta_suffix) != meta_enc_len - meta_split:
                raise ValueError("Wrong password or corrupted file")

            salt     = salt_prefix + salt_suffix
            enc_meta = enc_meta_prefix + enc_meta_suffix
            data_start = f.tell()   # byte offset of enc_head blob

            # ── Derive key and decrypt metadata ──
            kdf      = salt[0] & 0x01
            key      = _derive_key(password, kdf, salt)
            metadata = _decrypt_meta(key, enc_meta)

            # ── Read encrypted head blob ──
            enc_size      = metadata.get("encrypt_size", ENCRYPT_SIZE)
            orig_size     = metadata["orig_size"]
            compress_algo = metadata.get("compress_algo")
            compress_size = metadata.get("compress_size", orig_size)
            head_blob = f.read(12 + 16 + min(enc_size, compress_size))
            data_after_head = f.tell()

            # ── Read encrypted tail blob (last enc_size+28 bytes before eof) ──
            do_encrypt_tail = metadata.get("encrypt_tail", False)
            tail_blob = None
            if do_encrypt_tail:
                tail_blob_size = 12 + 16 + enc_size
                f.seek(-tail_blob_size, 2)
                tail_blob = f.read(tail_blob_size)

        # ── Decrypt head ──
        if len(head_blob) < 28:
            raise ValueError("Wrong password or corrupted file")
        try:
            h_nonce, h_tag, h_ct = head_blob[:12], head_blob[12:28], head_blob[28:]
            cipher         = AES.new(key, AES.MODE_GCM, nonce=h_nonce)
            decrypted_head = cipher.decrypt_and_verify(h_ct, h_tag)
        except Exception:
            raise ValueError("Wrong password or corrupted file")

        # ── Decrypt tail ──
        decrypted_tail = None
        if do_encrypt_tail and tail_blob and len(tail_blob) >= 28:
            try:
                t_nonce, t_tag, t_ct = tail_blob[:12], tail_blob[12:28], tail_blob[28:]
                cipher_t       = AES.new(key, AES.MODE_GCM, nonce=t_nonce)
                decrypted_tail = cipher_t.decrypt_and_verify(t_ct, t_tag)
            except Exception:
                raise ValueError("Wrong password or corrupted file")

        # ── Resolve output path ──
        orig_name   = metadata["orig_name"]
        output_path = filepath.parent / orig_name
        counter = 1
        while output_path.exists():
            stem        = Path(orig_name).stem
            sfx         = Path(orig_name).suffix
            output_path = filepath.parent / f"{stem}_{counter}{sfx}"
            counter += 1

        # ── Gather CTR decryption params ──
        enc_middle       = metadata.get("encrypt_middle", False)
        m_start          = metadata.get("middle_start", 0)
        m_size           = metadata.get("middle_size", 0)
        middle_nonce_hex = metadata.get("middle_nonce")

        enc_range        = metadata.get("encrypt_range", False)
        r_start          = metadata.get("range_start", 0)
        r_end_val        = metadata.get("range_end", 0)
        r_b              = metadata.get("range_b_bytes", 16)
        r_c              = metadata.get("range_c_bytes", 64)
        range_nonce_hex  = metadata.get("range_nonce")
        range_key_bytes  = hashlib.sha256(key + b"\x03").digest() if enc_range else None

        # Pre-decrypt middle CTR region (bounded size)
        middle_plain_map = None
        if enc_middle and m_size > 0 and middle_nonce_hex:
            m_nonce = bytes.fromhex(middle_nonce_hex)
            with open(filepath, "rb") as f:
                f.seek(data_after_head + (m_start - enc_size))
                mid_enc = f.read(m_size)
            mid_dec          = _apply_ctr(b"\x02", m_nonce, mid_enc, key)
            middle_plain_map = (m_start - enc_size, mid_dec)   # (gap_offset, data)

        # ── Stream-write output file ──
        with open(output_path, "wb") as dest:
            dest.write(decrypted_head)

            middle_gap = compress_size - enc_size - (enc_size if do_encrypt_tail else 0)
            if middle_gap > 0:
                with open(filepath, "rb") as src:
                    src.seek(data_after_head)
                    remaining = middle_gap
                    gap_pos   = 0
                    while remaining > 0:
                        chunk_sz = min(COPY_CHUNK_SIZE, remaining)
                        chunk    = bytearray(src.read(chunk_sz))
                        if not chunk:
                            break

                        # Reverse middle CTR
                        if enc_middle and m_size > 0 and middle_plain_map is not None:
                            gap_off, mid_dec = middle_plain_map
                            for i in range(len(chunk)):
                                g = gap_pos + i
                                if gap_off <= g < gap_off + m_size:
                                    chunk[i] = mid_dec[g - gap_off]

                        # Reverse range CTR (XOR is symmetric)
                        if enc_range and r_end_val > r_start and range_key_bytes and range_nonce_hex:
                            chunk = _apply_range_ctr_chunk(
                                chunk, enc_size + gap_pos,
                                r_start, r_end_val,
                                r_b, r_c,
                                range_key_bytes, bytes.fromhex(range_nonce_hex),
                            )

                        dest.write(bytes(chunk))
                        gap_pos   += len(chunk)
                        remaining -= len(chunk)

            if decrypted_tail is not None:
                dest.write(decrypted_tail)

        orig_stat = filepath.stat()
        os.utime(output_path, (orig_stat.st_atime, orig_stat.st_mtime))
        filepath.unlink()

        # ── Decompress if the payload was compressed before encryption ──
        if compress_algo:
            _decompress_inplace(output_path, compress_algo, orig_size)

        elapsed      = _time.perf_counter() - start_time
        file_size_mb = orig_size / (1024 * 1024)
        return f"OK: {filepath.name} -> {output_path.name} ({file_size_mb:.1f}MB, {elapsed:.2f}s)"

    except ValueError:
        raise
    except PermissionError:
        raise ValueError("Wrong password or corrupted file")
    except Exception:
        raise ValueError("Wrong password or corrupted file")


def _unwarp_inplace(filepath: Path, password: str) -> str:
    """Decrypt v2 in-place .ks file."""
    import time as _time
    start_time = _time.perf_counter()
    try:
        magic_mask, _, _, salt_xor = _pw_masks(password)

        with open(filepath, "rb") as f:
            # ── Verify footer magic (last 4 bytes) ──
            f.seek(-4, 2)
            raw_magic = f.read(4)
            if len(raw_magic) < 4 or bytes(a ^ b for a, b in zip(raw_magic, magic_mask)) != b"KWRI":
                raise ValueError("Wrong password or corrupted file")

            # ── Read fixed footer fields (offsets from end) ──
            f.seek(-20, 2);  head_tag    = f.read(16)   # [−20..−4]
            f.seek(-32, 2);  head_nonce  = f.read(12)   # [−32..−20]
            f.seek(-48, 2);  masked_salt = f.read(16)   # [−48..−32]
            f.seek(-52, 2);  meta_enc_len = struct.unpack("<I", f.read(4))[0]  # [−52..−48]

            if meta_enc_len > MAX_META_LEN:
                raise ValueError("Wrong password or corrupted file")

            f.seek(-(52 + meta_enc_len), 2)
            enc_meta = f.read(meta_enc_len)
            if len(enc_meta) != meta_enc_len:
                raise ValueError("Wrong password or corrupted file")

        # ── Unmask salt, derive key, decrypt metadata ──
        salt     = bytes(s ^ x for s, x in zip(masked_salt, salt_xor))
        kdf      = salt[0] & 0x01
        key      = _derive_key(password, kdf, salt)
        metadata = _decrypt_meta(key, enc_meta)

        orig_size        = metadata["orig_size"]
        enc_size         = metadata.get("encrypt_size", ENCRYPT_SIZE)
        actual_head_size = min(enc_size, orig_size)
        do_encrypt_tail  = metadata.get("encrypt_tail", False)

        # ── Early write-access check (avoids partial modifications) ──
        try:
            with open(filepath, "r+b") as _chk:
                pass
        except PermissionError:
            raise PermissionError(
                f"Cannot write to '{filepath.name}': access denied.\n"
                "Run KvhWarp as Administrator, or copy the file to a writable folder first."
            )

        # ── Step 1: Reverse Range CTR BEFORE GCM-decrypting head/tail ──
        # Range CTR in warp_file_inplace starts at range_start (default 0) and is
        # applied on top of the already GCM-encrypted head and tail bytes.
        # It must be removed first so that GCM tag verification can succeed.
        enc_range       = metadata.get("encrypt_range", False)
        r_start         = metadata.get("range_start", 0)
        r_end_val       = metadata.get("range_end", 0)
        r_b             = metadata.get("range_b_bytes", 16)
        r_c             = metadata.get("range_c_bytes", 64)
        range_nonce_hex = metadata.get("range_nonce")
        if enc_range and r_end_val > r_start and range_nonce_hex:
            from Crypto.Util import Counter
            range_key_bytes = hashlib.sha256(key + b"\x03").digest()
            r_nonce  = bytes.fromhex(range_nonce_hex)
            c_blks   = max(1, r_c // 16)
            with open(filepath, "r+b") as f:
                pos     = r_start
                blk_idx = 0
                while pos < r_end_val:
                    period_end    = min(pos + r_c, r_end_val)
                    enc_end       = min(pos + r_b, period_end)
                    enc_in_period = enc_end - pos
                    if enc_in_period > 0:
                        f.seek(pos)
                        enc_data = f.read(enc_in_period)
                        ctr_obj  = Counter.new(64, prefix=r_nonce,
                                               initial_value=blk_idx, little_endian=False)
                        cipher   = AES.new(range_key_bytes, AES.MODE_CTR, counter=ctr_obj)
                        ks       = cipher.encrypt(b"\x00" * enc_in_period)
                        f.seek(pos)
                        f.write(bytes(a ^ b for a, b in zip(enc_data, ks)))
                    pos     += r_c
                    blk_idx += c_blks

        # ── Step 2: Read head and tail ciphertext (Range CTR now removed) ──
        with open(filepath, "rb") as f:
            f.seek(0)
            ct_head = f.read(actual_head_size)
            ct_tail = None
            if do_encrypt_tail:
                f.seek(orig_size - enc_size)
                ct_tail = f.read(enc_size)

        # ── Step 3: GCM decrypt head ──
        try:
            cipher         = AES.new(key, AES.MODE_GCM, nonce=head_nonce)
            decrypted_head = cipher.decrypt_and_verify(ct_head, head_tag)
        except Exception:
            raise ValueError("Wrong password or corrupted file")

        # ── Step 4: GCM decrypt tail ──
        decrypted_tail = None
        if do_encrypt_tail and ct_tail:
            try:
                t_nonce        = bytes.fromhex(metadata["tail_nonce"])
                t_tag          = bytes.fromhex(metadata["tail_tag"])
                cipher_t       = AES.new(key, AES.MODE_GCM, nonce=t_nonce)
                decrypted_tail = cipher_t.decrypt_and_verify(ct_tail, t_tag)
            except Exception:
                raise ValueError("Wrong password or corrupted file")

        # ── Resolve output path ──
        orig_name   = metadata["orig_name"]
        output_path = filepath.parent / orig_name
        counter = 1
        while output_path.exists():
            stem        = Path(orig_name).stem
            sfx         = Path(orig_name).suffix
            output_path = filepath.parent / f"{stem}_{counter}{sfx}"
            counter += 1

        # ── Step 5: Restore file (truncate footer, write plaintext head/tail/middle) ──
        with open(filepath, "r+b") as f:
            f.truncate(orig_size)

            # Restore GCM-decrypted head and tail
            f.seek(0)
            f.write(decrypted_head)
            if decrypted_tail is not None:
                f.seek(orig_size - enc_size)
                f.write(decrypted_tail)

            # Reverse middle CTR (Range CTR was already removed in Step 1)
            enc_middle       = metadata.get("encrypt_middle", False)
            m_start          = metadata.get("middle_start", 0)
            m_size           = metadata.get("middle_size", 0)
            middle_nonce_hex = metadata.get("middle_nonce")
            if enc_middle and m_size > 0 and middle_nonce_hex:
                f.seek(m_start)
                mid_enc = f.read(m_size)
                mid_dec = _apply_ctr(b"\x02", bytes.fromhex(middle_nonce_hex), mid_enc, key)
                f.seek(m_start)
                f.write(mid_dec)

        # ── Rename and restore timestamps ──
        orig_stat = filepath.stat()
        filepath.rename(output_path)
        os.utime(output_path, (orig_stat.st_atime, orig_stat.st_mtime))

        elapsed      = _time.perf_counter() - start_time
        file_size_mb = orig_size / (1024 * 1024)
        return f"OK: {filepath.name} -> {output_path.name} ({file_size_mb:.1f}MB, {elapsed:.2f}s, in-place)"

    except (ValueError, PermissionError):
        raise
    except Exception:
        raise ValueError("Wrong password or corrupted file")


def unwarp_auto(filepath: Path, password: str, base_folder: Optional[Path] = None) -> str:
    """Auto-detect v2 copy vs in-place and decrypt."""
    try:
        # Skip files that don't carry the .ks extension at all
        if filepath.suffix.lower() != KS_EXT:
            return f"SKIP: {filepath.name} (not a .ks file)"
        if not is_warped(filepath, password):
            return f"SKIP: {filepath.name} (not warped)"

        magic_mask = _pw_masks(password)[0]

        with open(filepath, "rb") as f:
            f.seek(-4, 2)
            raw = f.read(4)

        if len(raw) == 4 and bytes(a ^ b for a, b in zip(raw, magic_mask)) == b"KWRI":
            return _unwarp_inplace(filepath, password)
        else:
            return _unwarp_copy(filepath, password)

    except ValueError as e:
        return f"ERR: {filepath.name} - {e}"
    except PermissionError as e:
        return f"ERR: {filepath.name} - {e}"
    except OSError:
        return f"ERR: {filepath.name} - File access error"
    except Exception:
        return f"ERR: {filepath.name} - Decryption failed"



# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    # Tell Windows to use the window icon in the taskbar (not the Python interpreter icon)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"KVH.{_APP_NAME}.{_APP_VERSION}"
        )
    except Exception:
        pass

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
            self._var_enhance = tk.BooleanVar(value=self.opts.get("password_enhancement", False))
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

            # ── Enhancement option row ──
            row_enhance = tk.Frame(outer, bg=BG)
            row_enhance.pack(fill=tk.X, pady=(0, 12))
            tk.Checkbutton(row_enhance, text="[+] Enable password enhancement (mixes with file fingerprint)", variable=self._var_enhance, font=("Segoe UI", 9), bg=BG, fg=FG, activebackground=BG, activeforeground=CYAN, selectcolor=BG_PANEL, command=self._on_enhancement_toggle).pack(side=tk.LEFT)

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

        def _on_enhancement_toggle(self):
            """Handle password enhancement checkbox toggle with warning dialog."""
            if self._var_enhance.get():  # Being enabled
                if not self.opts.get("enhancement_warning_shown", False):
                    # Show warning dialog
                    from tkinter import messagebox
                    warning_text = """Password Enhancement

This feature strengthens your passwords by mixing them with file-specific data.

Benefits:
  - Much stronger security
  - Same password, better protection

Important:
  - Files encrypted with this enabled require it enabled to decrypt
  - Not compatible with older versions

Continue enabling password enhancement?"""
                    
                    result = messagebox.askyesno("Password Enhancement", warning_text, parent=self)
                    if not result:
                        # User cancelled, revert checkbox
                        self._var_enhance.set(False)
                        return
                    
                    # User confirmed, don't show warning again
                    self.opts["enhancement_warning_shown"] = True
                    _save_opts(self.opts)
                
                # Update settings
                self.opts["password_enhancement"] = True
                _save_opts(self.opts)
            else:  # Being disabled
                self.opts["password_enhancement"] = False
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
                        if use_inplace:
                            result = warp_fn(
                                f, pw,
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
                                f, pw,
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



    # Entry point: single-file decrypt (double-click) or full UI
    if (len(sys.argv) == 2
            and sys.argv[1].lower().endswith(KS_EXT)
            and Path(sys.argv[1]).is_file()):
        _SingleDecryptDialog(Path(sys.argv[1])).mainloop()
    else:
        App().mainloop()
