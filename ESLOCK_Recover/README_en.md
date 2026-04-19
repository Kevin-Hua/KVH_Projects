# ESLock & KVH Locker — Security Whitepaper

> This document is a comprehensive security whitepaper for ESLock Encryptor / Decryptor and KVH Locker, covering system architecture, encryption models, threat analysis, and known limitations.

---

## Table of Contents

1. [Overview](#overview)
2. [System Architecture](#system-architecture)
3. [Encryption Model](#encryption-model)
4. [ESLock Compatibility](#eslock-compatibility)
5. [Partial Encryption Mechanism](#partial-encryption-mechanism)
6. [ESLock Recovery Tool](#eslock-recovery-tool)
7. [KVH Locker Design Goals](#kvh-locker-design-goals)
8. [Solution Comparison](#solution-comparison)
9. [STRIDE Threat Model](#stride-threat-model)
10. [LINDDUN Privacy Model](#linddun-privacy-model)
11. [Attack Surface](#attack-surface)
12. [Security Assumptions](#security-assumptions)
13. [Risk Assessment](#risk-assessment)
14. [Legal & Compliance](#legal--compliance)
15. [Limitations & Risks](#limitations--risks)
16. [Conclusion](#conclusion)

---

## Overview

This document covers:
- ESLock Encryptor / Decryptor — encryption tooling compatible with ES File Explorer
- KVH Locker — a high-security, low-identifiability local encryption solution
- ESLock Recovery — a tool that recovers `.eslock` files when the password is lost, by exploiting the key embedded in the footer

---

## System Architecture

- User-side tooling (CLI / GUI)
- Cryptographic core (AES / PBKDF2 / HMAC)
- File format layer (ESLock footer / KVH header)
- Local storage and backup media

---

## Encryption Model

### ESLock

- AES-128-CFB (mobile compatible)
- Key derived from MD5(password)
- Supports partial and full encryption

### KVH Locker

- PBKDF2-HMAC-SHA256 (600k iterations)
- Master key to file key derivation
- AES encryption with HMAC integrity check

---

## ESLock Compatibility

### Encryption Algorithm

- AES-128 CFB mode
- IV: fixed `[0x00 .. 0x0F]`
- Key derivation: `MD5(password)`
- Filename and Key stored in Footer (matches mobile app)
  - This is the primary reason why the encryption can be cracked and restored — **no original password needed to recover the file**
  - Pro: designed for personal use; does not affect user experience; purely for privacy from casual viewers
  - Con: weak security — easily cracked

```
Key = MD5(password)[:16]
Cipher = AES-CFB-128(Key, IV=[0x00..0x0F])
```

### ESLock File Layout

```
[ Encrypted Data Stream ]
[ Optional Partial Encryption ]
[ Footer ]
  ├─ Flags (0x04 = partial, 0xFF = full)
  ├─ Block Size
  ├─ Encrypted Original Filename
  ├─ Embedded AES Key
  ├─ CRC32 Integrity Check
  └─ Footer Length
```

---

## Partial Encryption Mechanism

- Only encrypts **first N bytes + last N bytes** of the file
- Middle data remains plaintext (extremely fast)
- Suitable for videos, backups, and sync data

```
[ AES(head) ][ plain middle ][ AES(tail) ][ footer ]
```

> ⚠️ **Security Notice:** Partial encryption protects metadata and file headers but does NOT guarantee full confidentiality.

---

## ESLock Recovery Tool

### ES File Explorer Encryption Algorithm

ES File Explorer uses **AES-128-CFB** (Cipher Feedback Mode, 128-bit segment) with a *fixed* initialisation vector to encrypt files. The encrypted output is saved with the `.eslock` extension.

### Key Derivation

```
Password  (UTF-8 string)
     │
     ▼
  MD5 hash  (16 bytes)
     │
     ▼
  AES-128 key  (first 16 bytes of MD5 digest)
```

- The user password is hashed with **MD5**
- The full 16-byte digest is used directly as the AES key
- No salt, no iterations, no key stretching (PBKDF2 / scrypt / Argon2 is **not** used)

### Full Encryption (small files)

```
┌─────────────────────────────────┐
│         Original File           │
│       (all bytes)               │
└────────────────┬────────────────┘
                 │
                 ▼
   AES-128-CFB  encrypt  (stream)
   IV  = [0x00, 0x01, 0x02 … 0x0F]
   Key = MD5(password)
                 │
                 ▼
┌─────────────────────────────────┐
│       Encrypted Content         │
├─────────────────────────────────┤
│            Footer               │
│   (metadata + KEY + CRC)        │
└─────────────────────────────────┘
           output: file.eslock
```

### Partial Encryption (large files)

```
┌──────────────┬───────────────────────┬──────────────┐
│  First Block │     Middle Section    │  Last Block  │
│  (1024 B)    │     (plaintext)       │  (1024 B)    │
└──────┬───────┴───────────────────────┴──────┬───────┘
       │                                      │
       ▼                                      ▼
  AES-CFB encrypt                        AES-CFB encrypt
  (fresh cipher)                         (fresh cipher)
       │                                      │
       ▼                                      ▼
┌──────────────┬───────────────────────┬──────────────┐
│  Encrypted   │  Plaintext (pass-     │  Encrypted   │
│  First Block │  through, untouched)  │  Last Block  │
├──────────────┴───────────────────────┴──────────────┤
│                       Footer                         │
└──────────────────────────────────────────────────────┘
```

- Default **block size = 1024 bytes**
- Only the **first** and **last** 1024-byte blocks are encrypted
- The **middle** of the file remains **plaintext**
- Each block gets a **fresh cipher** (same key + same IV)

### Footer Structure

The footer is appended after the encrypted content. The last 4 bytes always hold the footer length.

| Field | Size | Description |
|---|---|---|
| Encryption flag | 1 B | `0xFF` = full encryption; other = partial |
| Block size | 4 B (BE) | Only present if partial encryption |
| Name length | 1 B | `0xFF` = name not stored |
| Encrypted filename | variable | AES-CFB encrypted, same key |
| Key prefix magic | 1 B | Always `0x10` |
| **★ AES KEY ★** | 16 B | The decryption key, stored in **plaintext** |
| Key postfix magic | 1 B | `0x00` or `0x02` |
| CRC padding | 4 B | All zeros |
| Stored CRC32 | 4 B (BE) | CRC of footer bytes before the CRC section |
| Footer length | 4 B (BE) | Total footer size (including this field) |

### The Loophole — Why Recovery Is Possible

> ⚠️ **The AES decryption key is stored in plaintext inside the encrypted file itself.**

The `.eslock` format is closer to **obfuscation** than encryption. The "locked" file carries its own key — like a padlocked box with the key taped to the outside.

| Weakness | Detail |
|---|---|
| 🔴 Critical: Key stored in file | The 16-byte AES key sits between magic bytes `0x10` and `0x00`/`0x02`. Anyone who can read the file can extract it. |
| 🔴 Critical: No password needed | Because the key is in the footer, the user's password is **never required** for decryption. |
| 🟡 High: Weak key derivation | `MD5(password)` — no salt, no iterations. Trivially brute-forceable even without the embedded key. |
| 🟡 High: Fixed IV | `IV = [0, 1, 2, …, 15]` — the same IV for every file, every block. Breaks CFB semantic security. |
| 🟡 High: Partial encryption leaks data | In partial mode the entire middle of the file is plaintext. Media files are largely viewable. |
| 🟡 Medium: CRC only, no MAC | CRC32 verifies structure integrity, **not** file authenticity. No HMAC or AEAD. |

### Recovery Flow

```
Start CLI
  → Scan input path for *.eslock
  → For each .eslock file:
      Read last 4 bytes → footer_length
      Valid length?
        No  → --heuristic? Scan last 128KB for signatures
        Yes ↓
      Parse footer structure (key, CRC, enc_mode, block_size, encrypted filename)
      CRC valid?
        No  → --ignore-crc? Otherwise SKIP
        Yes ↓
      Extract 16-byte AES key from footer
      Decrypt original filename (if any)
      Decrypt file content:
        Full    → AES-CFB entire stream
        Partial → first + last blocks
      Write recovered file to output directory ✓
```

### Requirements

| Package | Version | Purpose |
|---|---|---|
| Python | ≥ 3.8 | Runtime |
| pycryptodome | ≥ 3.9 | AES-128-CFB decryption |

Standard library modules: `argparse`, `hashlib`, `os`, `struct`, `sys`, `zlib`, `datetime`, `pathlib`, `typing`

```bash
pip install pycryptodome
```

### Usage

```bash
# Recover all .eslock files in the current directory
python ESLOCK_Recovery.py

# Recover a single file
python ESLOCK_Recovery.py photo.eslock

# Recover a directory to a specific output folder
python ESLOCK_Recovery.py ./encrypted ./recovered

# Handle corrupted files with heuristic search
python ESLOCK_Recovery.py --heuristic ./damaged

# Force recovery despite CRC mismatches
python ESLOCK_Recovery.py --ignore-crc ./files

# Overwrite existing output files
python ESLOCK_Recovery.py --overwrite ./files ./output
```

| Argument | Description |
|---|---|
| `input` | File or directory to recover (default: current directory) |
| `output` | Destination directory (default: auto-created `recovered-YYYYMMDD-HHMMSS`) |
| `--overwrite` | Overwrite existing output files |
| `--ignore-crc` | Proceed even if footer CRC check fails |
| `--heuristic` | Use heuristic footer search (for corrupted/truncated files) |

### Function Reference

**Cryptography**

| Function | Description |
|---|---|
| `_make_aes(key)` | Creates an AES-128-CFB cipher with fixed IV `[0..15]` |
| `decrypt_stream(…)` | Decrypts file body — full or partial mode |
| `decrypt_file_name(…)` | Decrypts the original filename (padded to 16-byte boundary) |

**Footer Parsing**

| Function | Description |
|---|---|
| `read_footer_standard(path)` | Fast reader — reads last 1024 bytes, parses from declared length |
| `read_footer_heuristic(path)` | Resilient reader — scans last 128 KB for signatures & structural patterns |
| `_seq_parse(buf, total, offset)` | Attempts footer parse at a given buffer offset (used by heuristic) |
| `_read_tail(path, length)` | Reads the last N bytes of a file |
| `_crc32(data)` | Computes unsigned 32-bit CRC32 |

**Recovery**

| Function | Description |
|---|---|
| `_recover_one(…)` | Core: read footer → extract key → validate CRC → decrypt → write output |
| `main()` | CLI entry point: parse args, collect files, run recovery, print summary |

**Data Model**

| Class | Description |
|---|---|
| `EslockFooter` | Holds parsed footer fields: `key`, `is_partial_encryption`, `encrypted_block_size`, `encrypted_original_name`, `stored_crc`, `calculated_crc`, `footer_offset`, `footer_length`. Property `is_crc_valid` checks CRC match. |

---

## KVH Locker Design Goals

### Core Design Principles

- 🚀 **High-speed encryption**: AES-CFB stream mode handles large files without padding overhead
- 👁️ **Low identifiability**: output contains no recognizable magic bytes or fixed headers — resembles random noise
- 🧠 **Format anti-fingerprinting**: does not match any known encrypted format (not ESLock / ZIP / PGP / VeraCrypt)
- 🔐 **Modern key derivation**: PBKDF2-HMAC-SHA256 with 600,000 iterations effectively resists brute-force attacks
- 🔗 **Integrity verification**: HMAC-SHA256 appended to each file; decryption is refused if authentication fails
- 🗝️ **No embedded key**: neither the key nor password is stored in the output file (contrast with ESLock's design)
- 🔄 **Random IV per file**: a fresh random IV is generated each time, so identical plaintexts produce different ciphertexts

### Key Differences vs. ESLock

| Feature | ESLock | KVH Locker |
|---|---|---|
| Key derivation | `MD5(password)` | `PBKDF2-HMAC-SHA256` × 600k |
| IV | Fixed `[0x00..0x0F]` | Random 16 bytes |
| Key embedded in file | ✅ Plaintext | ❌ Never |
| Integrity check | CRC32 (weak) | HMAC-SHA256 |
| Format identifiability | High (signature header) | Very low (pseudo-random) |
| Recoverable without password | ⚠️ Yes | ❌ No |

### KVH Key Hierarchy

```
User Password
   ↓ PBKDF2-HMAC-SHA256
   ↓   salt = random 16 bytes
   ↓   iterations = 600,000
   ↓   dkLen = 32 bytes
Master Key (32 bytes)
   ↓ SHA256(Master Key + IV)
File Key (32 bytes)
   ↓ AES-256-CFB, IV = random 16 bytes
Encrypted Ciphertext
   ↓ HMAC-SHA256(File Key, Ciphertext)
Auth Tag (32 bytes) → appended to file
```

### File Format (Header Layout)

```
[ Magic / Version  :  4 bytes  ]  ← internal ID, not exposed externally
[ Salt             : 16 bytes  ]  ← random salt for PBKDF2
[ IV               : 16 bytes  ]  ← random initialisation vector
[ Ciphertext       :  N bytes  ]  ← AES-256-CFB encrypted content
[ HMAC-SHA256      : 32 bytes  ]  ← integrity authentication tag (end)
```

---

## Solution Comparison

| Solution | Speed | Security | Identifiability |
|---|---|---|---|
| ESLock Partial | ★★★★★ | ★☆☆☆☆ | High |
| ESLock Full | ★★★☆☆ | ★★★☆☆ | High |
| KVH Locker | ★★★★☆ | ★★★★★ | Very Low |
| ZIP + AES | ★★☆☆☆ | ★★★☆☆ | High |
| VeraCrypt | ★☆☆☆☆ | ★★★★★ | Medium |

---

## STRIDE Threat Model

| Category | Threat | Mitigation |
|---|---|---|
| Spoofing | Impersonation | Key-based cryptography |
| Tampering | Data modification | CRC / HMAC |
| Repudiation | Action denial | No central authority |
| Information Disclosure | Data leakage | KVH full encryption |
| Denial of Service | Availability attack | Fail-secure design |
| Elevation of Privilege | Unauthorized access | OS-level boundary |

---

## LINDDUN Privacy Model

| Category | Risk | Mitigation |
|---|---|---|
| Linkability | Cross-file correlation | Random IV |
| Identifiability | User inference | Format obfuscation |
| Non-repudiation | Action traceability | No centralized logging |
| Detectability | Signature detection | Masked header |
| Data Disclosure | Plaintext leakage | KVH full encryption |
| Unawareness | User misunderstanding | Explicit documentation |
| Non-compliance | Legal misuse | User responsibility |

---

## Attack Surface

The attack surface includes user input, filesystem access, and encrypted artifacts. Defense boundaries consist of encryption logic and integrity validation.

**Components inside the security boundary:**
- Encryption Logic (AES / PBKDF2)
- Integrity Check (HMAC-SHA256)

**External encrypted storage:**
- `.eslock` / `.kvh` files

---

## Security Assumptions

- Endpoint OS integrity is trusted
- Password secrecy is the user's responsibility
- No key escrow or recovery mechanism

---

## Risk Assessment

- ESLock uses MD5 (kept for compatibility, not best practice)
- Partial Encryption cannot prevent content extraction
- Footer location is readable in plaintext (by design)
- KVH Locker password loss = **permanent, irreversible data loss**

---

## Legal & Compliance

This tool is intended only for:

- ✔ Personal data protection
- ✔ Lawful forensics / digital evidence
- ✔ Backup and privacy management
- ❌ Unauthorized access, theft, or destruction of others' data is prohibited

---

## Limitations & Risks

- Partial encryption does not guarantee full confidentiality
- Password loss results in permanent data loss
- No protection against compromised operating systems

---

## Conclusion

ESLock provides **100% mobile compatibility**. KVH Locker offers a **higher-security, low-identifiability concealment solution**.

**Recommended usage strategy:**

| Use Case | Recommended Tool |
|---|---|
| 📱 Mobile sync | ESLock |
| 🗄️ Private archiving | KVH Locker |
| 🧪 Forensic analysis | ESLockDecryptor |

---

*© 2025-2026 Rewolf — MIT License*
