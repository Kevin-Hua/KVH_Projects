"""
Suite 12 — In-place mode fixed combination matrix.
warp_file_inplace -> unwarp_auto -> SHA-256 verify.
2 kdf × 2 tail × 2 middle × 6 range = 48 combos × 3 files = 144 runs.
Records per-run elapsed time; prints top-10 slowest at end.
"""
import sys, shutil, hashlib, traceback, time
from pathlib import Path

_HERE     = Path(__file__).parent
TESTFILES = _HERE / 'TestFiles'
sys.path.insert(0, str(_HERE.parent))
from kvhwarp_core import (
    warp_file_inplace, unwarp_auto,
    KDF_SCRYPT, KDF_SHA256,
)

WORK = TESTFILES / '_tc12'
if WORK.exists(): shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(exist_ok=True)
PW = 'InplcCmb@12!'

MP4_SMALL  = TESTFILES / 'Mpg' / '1684427397013.mp4'
MP4_MEDIUM = TESTFILES / 'Mpg' / '1695318021338.mp4'
MP4_LARGE  = TESTFILES / 'Mpg' / '射的騷女臉上到處都是.mp4'

SOURCES = {
    'small (~1.2MB)':  MP4_SMALL,
    'medium (~3.3MB)': MP4_MEDIUM,
    'large (~5MB)':    MP4_LARGE,
}

def sha256file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        while chunk := f.read(1 << 20): h.update(chunk)
    return h.hexdigest()

def cleanup():
    for f in WORK.iterdir():
        try: f.unlink()
        except: pass

KDF_NAMES = {KDF_SCRYPT: 'scrypt', KDF_SHA256: 'sha256'}

RANGE_VARIANTS = [
    (False, 'manual', 25,   16,  64,  'none'),
    (True,  'auto',   25,   16,  64,  'auto-25%'),
    (True,  'auto',   50,   16,  64,  'auto-50%'),
    (True,  'manual', 25,   16,  64,  'manual-b16c64'),
    (True,  'manual', 25,   32, 128,  'manual-b32c128'),
    (True,  'manual', 25,  512, 512,  'manual-b512c512(all)'),
]

COMBOS = []
for kdf in (KDF_SCRYPT, KDF_SHA256):
    for tail in (False, True):
        for middle in (False, True):
            for enc_range, r_mode, r_pct, r_b, r_c, r_label in RANGE_VARIANTS:
                COMBOS.append({
                    'kdf': kdf, 'encrypt_tail': tail, 'encrypt_middle': middle,
                    'encrypt_range': enc_range, 'range_mode': r_mode,
                    'range_percent': r_pct, 'range_b_bytes': r_b, 'range_c_bytes': r_c,
                    'range_start': 0, 'range_end': 0, '_label': r_label,
                })

PASS = FAIL = 0
results  = []  # (result_line,)
timings  = []  # (elapsed, label_for_leaderboard)

for src_label, src in SOURCES.items():
    orig_hash = sha256file(src)
    for opts in COMBOS:
        combo_id = (
            f"kdf={KDF_NAMES[opts['kdf']]:6s} tail={str(opts['encrypt_tail']):5s} "
            f"mid={str(opts['encrypt_middle']):5s} range={opts['_label']}"
        )
        header = f"[{src_label}]  {combo_id}"
        work_src = WORK / src.name
        shutil.copy2(src, work_src)
        t0 = time.perf_counter()
        try:
            warp_opts = {k: v for k, v in opts.items() if not k.startswith('_')}
            w_res = warp_file_inplace(work_src, PW, **warp_opts)
            if not w_res.startswith('OK'):
                raise RuntimeError(f'warp: {w_res}')
            ks_name = w_res.split('->')[1].strip().split()[0]
            ks_file = WORK / ks_name

            u_res = unwarp_auto(ks_file, PW)
            if not u_res.startswith('OK'):
                raise RuntimeError(f'unwarp: {u_res}')
            out_name = u_res.split('->')[1].strip().split()[0]
            out_file = WORK / out_name

            if sha256file(out_file) != orig_hash:
                raise RuntimeError('HASH MISMATCH')
            elapsed = time.perf_counter() - t0
            PASS += 1
            results.append(f'PASS  {header}')
        except Exception:
            elapsed = time.perf_counter() - t0
            FAIL += 1
            tb = traceback.format_exc().strip().splitlines()[-1]
            results.append(f'FAIL  {header}\n      {tb}')
        finally:
            timings.append((elapsed, header))
            cleanup()

print()
for r in results: print(r)
print()
print('=' * 72)
print(f'  PASS: {PASS}   FAIL: {FAIL}   TOTAL: {PASS + FAIL}')
print('=' * 72)

top = sorted(timings, key=lambda x: x[0], reverse=True)[:10]
print()
print('── Top 10 Slowest Runs ' + '─' * 49)
for rank, (secs, lbl) in enumerate(top, 1):
    print(f'  {rank:2d}.  {secs:7.3f}s  {lbl}')
print('─' * 72)

try: WORK.rmdir()
except: pass

WORK   = BINARY / '_test_inplace'
WORK.mkdir(exist_ok=True)
PW     = 'TestPass!1'

# ── Source files (one per size bucket) ──────────────────────────────────────
SOURCES = {
    'small (~1.2MB)':   BINARY / '1684427397013.mp4',
    'medium (~3.3MB)':  BINARY / '1695318021338.mp4',
    'large (~5MB)':     BINARY / '射的騷女臉上到處都是.mp4',
}

def sha256file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()

def cleanup_work():
    for item in WORK.iterdir():
        try:
            item.unlink()
        except Exception:
            pass

# ── Option matrix ────────────────────────────────────────────────────────────
# kdf × encrypt_tail × encrypt_middle × range variant
KDF_NAMES = {KDF_SCRYPT: 'scrypt', KDF_SHA256: 'sha256'}

RANGE_VARIANTS = [
    # (encrypt_range, range_mode, range_percent, range_b_bytes, range_c_bytes, label)
    (False, 'manual', 25,  16,  64,  'none'),
    (True,  'auto',   25,  16,  64,  'auto-25%'),
    (True,  'auto',   50,  16,  64,  'auto-50%'),
    (True,  'manual', 25,  16,  64,  'manual-b16c64'),
    (True,  'manual', 25,  32, 128,  'manual-b32c128'),
    (True,  'manual', 25, 512,  512, 'manual-b512c512(all)'),
]

COMBOS = []
for kdf in (KDF_SCRYPT, KDF_SHA256):
    for tail in (False, True):
        for middle in (False, True):
            for enc_range, r_mode, r_pct, r_b, r_c, r_label in RANGE_VARIANTS:
                COMBOS.append({
                    'kdf':            kdf,
                    'encrypt_tail':   tail,
                    'encrypt_middle': middle,
                    'encrypt_range':  enc_range,
                    'range_mode':     r_mode,
                    'range_percent':  r_pct,
                    'range_b_bytes':  r_b,
                    'range_c_bytes':  r_c,
                    'range_start':    0,
                    'range_end':      0,
                    '_label':         r_label,
                })

# ── Run tests ────────────────────────────────────────────────────────────────
PASS = FAIL = 0
results = []

for src_label, src in SOURCES.items():
    orig_hash = sha256file(src)
    for opts in COMBOS:
        label = opts['_label']
        combo_id = (
            f"kdf={KDF_NAMES[opts['kdf']]:6s} "
            f"tail={str(opts['encrypt_tail']):5s} "
            f"mid={str(opts['encrypt_middle']):5s} "
            f"range={label}"
        )
        header = f"[{src_label}]  {combo_id}"

        # Copy source into work dir
        work_copy = WORK / src.name
        shutil.copy2(src, work_copy)

        ks_file  = None
        out_file = None
        try:
            # Strip the internal _label key before passing to warp
            warp_opts = {k: v for k, v in opts.items() if not k.startswith('_')}

            # Warp
            w_res = warp_file_inplace(work_copy, PW, **warp_opts)
            if not w_res.startswith('OK'):
                raise RuntimeError(f'warp failed: {w_res}')
            ks_name = w_res.split('->')[1].strip().split()[0]
            ks_file = WORK / ks_name

            # Unwarp
            u_res = unwarp_auto(ks_file, PW)
            if not u_res.startswith('OK'):
                raise RuntimeError(f'unwarp failed: {u_res}')
            out_name = u_res.split('->')[1].strip().split()[0]
            out_file = WORK / out_name

            # SHA-256 verify
            restored_hash = sha256file(out_file)
            if restored_hash != orig_hash:
                raise RuntimeError(
                    f'HASH MISMATCH  orig={orig_hash[:16]}  restored={restored_hash[:16]}'
                )

            PASS += 1
            results.append(f'PASS  {header}')

        except Exception as e:
            FAIL += 1
            tb = traceback.format_exc().strip().splitlines()[-1]
            results.append(f'FAIL  {header}\n      {tb}')

        finally:
            # Cleanup leftover files regardless of outcome
            cleanup_work()

# ── Summary ──────────────────────────────────────────────────────────────────
print()
for r in results:
    print(r)
print()
print(f'{"="*70}')
print(f'  PASS: {PASS}   FAIL: {FAIL}   TOTAL: {PASS + FAIL}')
print(f'{"="*70}')

# Remove work dir if empty
try:
    WORK.rmdir()
except Exception:
    pass
