# ESLOCK_Recovery

**Version 1.0** | © 2025-2026 Rewolf | MIT License

A single-purpose CLI tool that recovers ES File Explorer encrypted files (`.eslock`) when the password is lost — by exploiting the encryption key embedded in the file footer.

---

## Table of Contents

- [ES File Explorer Encryption Algorithm](#es-file-explorer-encryption-algorithm)
  - [Key Derivation](#key-derivation)
  - [File Encryption Flow](#file-encryption-flow)
  - [Footer Structure](#footer-structure)
- [The Loophole — Why Recovery Is Possible](#the-loophole--why-recovery-is-possible)
- [Recovery Flow](#recovery-flow)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Function Reference](#function-reference)

---

## ES File Explorer Encryption Algorithm

ES File Explorer uses **AES-128-CFB** (Cipher Feedback Mode) with a fixed IV to encrypt files. The encrypted output is written to a new file with the `.eslock` extension.

### Key Derivation

```
Password (UTF-8 string)
        │
        ▼
   MD5 hash (16 bytes)
        │
        ▼
   AES-128 key (first 16 bytes of MD5 digest)
```

- The user's password is hashed with **MD5**.
- The full 16-byte MD5 digest is used directly as the **AES-128 key**.
- No salt, no iterations, no key stretching (PBKDF2 / scrypt / Argon2 is **not** used).

### File Encryption Flow

ES File Explorer supports two encryption modes depending on file size:

#### Full Encryption (small files)

```
┌─────────────────────────────────┐
│         Original File           │
│  (all bytes, any size)          │
└────────────────┬────────────────┘
                 │
                 ▼
   AES-128-CFB encrypt (stream)
   IV = [0x00, 0x01, 0x02, ..., 0x0F]
   Key = MD5(password)
                 │
                 ▼
┌─────────────────────────────────┐
│       Encrypted Content         │
├─────────────────────────────────┤
│            Footer               │
│  (metadata + key + CRC)         │
└─────────────────────────────────┘
         output: file.eslock
```

All bytes are encrypted through a single AES-CFB cipher instance (no cipher reset between chunks).

#### Partial Encryption (large files, performance optimization)

```
┌──────────────┬───────────────────────┬──────────────┐
│  First Block │     Middle Section    │  Last Block  │
│  (1024 bytes)│     (plaintext)       │ (1024 bytes) │
└──────┬───────┴───────────────────────┴──────┬───────┘
       │                                      │
       ▼                                      ▼
  AES-CFB encrypt                       AES-CFB encrypt
  (fresh cipher)                        (fresh cipher)
       │                                      │
       ▼                                      ▼
┌──────────────┬───────────────────────┬──────────────┐
│  Encrypted   │  Plaintext (pass-     │  Encrypted   │
│  First Block │  through, untouched)  │  Last Block  │
├──────────────┴───────────────────────┴──────────────┤
│                      Footer                          │
└──────────────────────────────────────────────────────┘
                  output: file.eslock
```

- The default **block size** is **1024 bytes**.
- Only the **first** and **last** 1024-byte blocks are encrypted.
- The **middle** of the file is left as **plaintext**.
- Each block uses a **fresh AES cipher** (IV is reused; same key, same IV for both blocks).

### Footer Structure

The footer is appended after the (partially or fully) encrypted content:

```
Offset from start of footer:
┌───────────────────────────────────────────────────────────┐
│ [0]        Encryption flag     (1 byte)                   │
│                 0xFF = full encryption                     │
│                 other = partial encryption                 │
├───────────────────────────────────────────────────────────┤
│ [1..4]     Encrypted block size (4 bytes, BE int32)       │
│            (only present if partial encryption)            │
├───────────────────────────────────────────────────────────┤
│ [next]     Original name length (1 byte)                  │
│                 0xFF = name not stored                     │
├───────────────────────────────────────────────────────────┤
│ [next..N]  Encrypted original filename (variable length)  │
│            (AES-CFB encrypted, same key)                  │
├───────────────────────────────────────────────────────────┤
│            Key prefix magic    (1 byte = 0x10)            │
├───────────────────────────────────────────────────────────┤
│            *** AES KEY ***     (16 bytes)                  │
│            The actual decryption key, stored in plaintext  │
├───────────────────────────────────────────────────────────┤
│            Key postfix magic   (1 byte: 0x00 or 0x02)     │
├───────────────────────────────────────────────────────────┤
│            CRC padding         (4 bytes, all zeros)        │
├───────────────────────────────────────────────────────────┤
│            Stored CRC32        (4 bytes, BE uint32)        │
│            (CRC of footer bytes before the CRC section)    │
├───────────────────────────────────────────────────────────┤
│            Footer length       (4 bytes, BE int32)         │
│            (total footer size including this field)         │
└───────────────────────────────────────────────────────────┘
```

The footer length is always the **last 4 bytes** of the `.eslock` file, which makes it the entry point for parsing.

---

## The Loophole — Why Recovery Is Possible

The ES File Explorer encryption has a **critical design flaw**:

> **The AES decryption key is stored in plaintext inside the encrypted file itself.**

Specifically:

| Weakness | Detail |
|---|---|
| **Key stored in file** | The 16-byte AES key is embedded in the footer between magic bytes `0x10` (prefix) and `0x00`/`0x02` (postfix). Anyone who can read the file can extract the key. |
| **No password needed** | Because the key is in the footer, the user's password is **never required** for decryption. The password only served to derive the key at encryption time. |
| **Weak key derivation** | `MD5(password)` — no salt, no iterations. Even if the key weren't stored, the password could be brute-forced trivially. |
| **Fixed IV** | `IV = [0, 1, 2, ..., 15]` — the same IV is used for every file, every block. Combined with the same key, this breaks CFB's semantic security. |
| **Partial encryption leaks data** | In partial mode, the entire middle of the file is plaintext. For media files, this means the content is largely viewable. |
| **CRC integrity only** | The CRC32 in the footer is **not a cryptographic MAC**. It verifies footer structure integrity, not file authenticity. |

### In Summary

The `.eslock` format is closer to **obfuscation** than encryption. The "locked" file carries its own key — like a padlocked box with the key taped to the outside.

---

## Recovery Flow

```
                    ┌──────────────┐
                    │  Start CLI   │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ Scan input   │
                    │ for *.eslock │
                    └──────┬───────┘
                           │
              ┌────────────▼────────────┐
              │  For each .eslock file  │
              └────────────┬────────────┘
                           │
                ┌──────────▼──────────┐
                │ Read last 4 bytes   │
                │ → footer_length     │
                └──────────┬──────────┘
                           │
                   ┌───────▼────────┐  No
                   │ Valid length?  ├──────┐
                   └───────┬────────┘      │
                      Yes  │          ┌────▼────────────┐
                           │          │ --heuristic?    │
                           │          │ Scan last 128KB │
                           │          │ for signatures  │
                           │          └────┬────────────┘
                           │               │
                ┌──────────▼───────────────▼┐
                │  Parse footer structure   │
                │  Extract: key, CRC,       │
                │  enc_mode, block_size,    │
                │  encrypted filename       │
                └──────────┬────────────────┘
                           │
                   ┌───────▼────────┐
                   │ CRC valid?     │
                   └──┬──────────┬──┘
                  Yes │          │ No
                      │    ┌─────▼──────────┐
                      │    │ --ignore-crc?  │
                      │    └──┬──────────┬──┘
                      │   Yes │          │ No → SKIP
                      │       │          │
                ┌─────▼───────▼──┐       │
                │ Extract 16-byte│       │
                │ AES key from   │       │
                │ footer         │       │
                └───────┬────────┘       │
                        │                │
              ┌─────────▼─────────┐      │
              │ Decrypt original  │      │
              │ filename (if any) │      │
              └─────────┬─────────┘      │
                        │                │
              ┌─────────▼─────────┐      │
              │ Decrypt content:  │      │
              │ Full: AES-CFB     │      │
              │   entire stream   │      │
              │ Partial: first +  │      │
              │   last blocks     │      │
              └─────────┬─────────┘      │
                        │                │
              ┌─────────▼─────────┐      │
              │ Write recovered   │      │
              │ file to output    │      │
              └─────────┬─────────┘      │
                        │                │
                  ┌─────▼──┐      ┌──────▼──┐
                  │  [OK]  │      │ [FAIL]  │
                  └────────┘      └─────────┘
```

---

## Requirements

| Package | Version | Purpose |
|---|---|---|
| **Python** | ≥ 3.8 | Runtime |
| **pycryptodome** | ≥ 3.9 | AES-128-CFB decryption |

Standard library modules used: `argparse`, `hashlib`, `os`, `struct`, `sys`, `zlib`, `datetime`, `pathlib`, `typing`.

### Install

```bash
pip install pycryptodome
```

---

## Installation

No installation required. Single-file script.

```bash
# Run directly
python ESLOCK_Recovery.py [input] [output] [options]

# Or build standalone EXE (Windows)
_pack_recovery.bat
# → dist/ESLOCK_Recovery.exe
```

---

## Usage

```bash
# Recover all .eslock files in the current directory
python ESLOCK_Recovery.py

# Recover a single file
python ESLOCK_Recovery.py photo.eslock

# Recover a directory to a specific output folder
python ESLOCK_Recovery.py ./encrypted ./recovered

# Handle corrupted files with heuristic footer search
python ESLOCK_Recovery.py --heuristic ./damaged

# Force recovery despite CRC mismatches
python ESLOCK_Recovery.py --ignore-crc ./files

# Overwrite existing output files
python ESLOCK_Recovery.py --overwrite ./files ./output
```

### CLI Arguments

| Argument | Description |
|---|---|
| `input` | File or directory to recover (default: current directory) |
| `output` | Destination directory (default: auto-created `recovered-YYYYMMDD-HHMMSS`) |
| `--overwrite` | Overwrite existing output files |
| `--ignore-crc` | Proceed even if the footer CRC check fails |
| `--heuristic` | Use heuristic footer search (for corrupted/truncated files) |

---

## Function Reference

### Cryptography

| Function | Description |
|---|---|
| `_make_aes(key)` | Creates an AES-128-CFB cipher instance with the fixed IV `[0..15]` |
| `decrypt_stream(fin, fout, key, orig_len, is_partial, block_size)` | Decrypts the file body — handles both full and partial encryption modes |
| `decrypt_file_name(encrypted_name, key)` | Decrypts the original filename stored in the footer (padded to 16-byte boundary) |

### Footer Parsing

| Function | Description |
|---|---|
| `read_footer_standard(path)` | Fast footer reader. Reads the last 1024 bytes, parses the footer from the declared length field. Fails on corrupted files. |
| `read_footer_heuristic(path)` | Resilient reader. Scans the last 128 KB for known byte signatures (`0x04 0x00 0x00 0x04 0x00`, etc.) and structural patterns (key prefix `0x10`, postfix `0x00`/`0x02`). Returns the best candidate ranked by CRC validity. |
| `_seq_parse(buf, total, offset)` | Attempts to parse a footer structure at a given buffer offset. Used by the heuristic scanner. |
| `_read_tail(path, length)` | Reads the last N bytes of a file. |
| `_crc32(data)` | Computes CRC32 (unsigned 32-bit). |

### Recovery

| Function | Description |
|---|---|
| `_recover_one(input_file, output_dir, heuristic, overwrite, ignore_crc)` | Core recovery function for a single file: reads footer → extracts key → validates CRC → decrypts → writes output. Returns `True`/`False`. |
| `main()` | CLI entry point. Parses arguments, collects `.eslock` files, runs recovery, prints summary. |

### Data Model

| Class | Description |
|---|---|
| `EslockFooter` | Holds all parsed footer fields: `key`, `is_partial_encryption`, `encrypted_block_size`, `encrypted_original_name`, `stored_crc`, `calculated_crc`, `footer_offset`, `footer_length`. Property `is_crc_valid` checks CRC match. |
