"""
Suite 16 — In-place mode extended boundary and edge-case coverage.
warp_file_inplace -> unwarp_auto -> SHA-256 verify.
53 fixed runs, no random.

Group A (20): range_mode='auto' + boundary percents {0,1,50,99,100} × 2 files
              + degenerate range windows (range_start==range_end, start>file)
Group B (23): encrypt_tail silent-clamp — file too small for 2×encrypt_size
              → warp must succeed (OK not SKIP or error)
Group C (10): kdf=KDF_SCRYPT + multiple conditions (tail/middle/range combos)
"""
import sys, shutil, hashlib, traceback, time
from pathlib import Path

_HERE     = Path(__file__).parent
TESTFILES = _HERE / 'TestFiles'
sys.path.insert(0, str(_HERE.parent))
from kvhwarp_core import (
    warp_file_inplace, unwarp_auto,
    KDF_SCRYPT, KDF_SHA256, ENCRYPT_SIZE, ENCRYPT_MIDDLE_SIZE,
)

WORK = TESTFILES / '_tc16'
if WORK.exists(): shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(exist_ok=True)

PW = 'InplcBnd@16!'

MP4_SMALL  = TESTFILES / 'Mpg' / '1684427397013.mp4'
MP4_MEDIUM = TESTFILES / 'Mpg' / '1695318021338.mp4'

SMALL_SIZE  = MP4_SMALL.stat().st_size
MEDIUM_SIZE = MP4_MEDIUM.stat().st_size

# ── helpers ───────────────────────────────────────────────────────────────────

def sha256file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        while chunk := f.read(1 << 20): h.update(chunk)
    return h.hexdigest()

def cleanup():
    for f in WORK.iterdir():
        try: f.unlink()
        except: pass

def copy_src(src):
    dst = WORK / src.name
    shutil.copy2(src, dst)
    return dst

def run_case(run_id, label, src, warp_kwargs, *, expect_ok=True):
    orig = sha256file(src)
    t0 = time.perf_counter()
    try:
        w = warp_file_inplace(src, PW, **warp_kwargs)
        if expect_ok:
            if not w.startswith('OK'):
                raise RuntimeError(f'warp returned {w!r} (expected OK)')
            ks  = WORK / w.split('->')[1].strip().split()[0]
            u   = unwarp_auto(ks, PW)
            if not u.startswith('OK'):
                raise RuntimeError(f'unwarp: {u}')
            out = WORK / u.split('->')[1].strip().split()[0]
            if sha256file(out) != orig:
                raise RuntimeError('HASH MISMATCH')
        elapsed = time.perf_counter() - t0
        return True, f'PASS  [{run_id:3d}] {label}', elapsed
    except Exception:
        elapsed = time.perf_counter() - t0
        tb = traceback.format_exc().strip().splitlines()[-1]
        return False, f'FAIL  [{run_id:3d}] {label}\n      {tb}', elapsed
    finally:
        cleanup()

PASS = FAIL = 0
results = []
timings = []
run_id  = 0

def reg(ok, line, elapsed):
    global PASS, FAIL
    results.append(line)
    timings.append((elapsed, line.split('\n')[0]))
    if ok: PASS += 1
    else:  FAIL += 1

# ── Group A: range_mode=auto + boundary percents + degenerate windows (20) ───

# A1: percent sweep {0,1,50,99,100} × small + medium (10)
for pct in (0, 1, 50, 99, 100):
    for src_path, f_lbl in ((MP4_SMALL, 'small'), (MP4_MEDIUM, 'medium')):
        run_id += 1
        reg(*run_case(run_id,
            f'A auto pct={pct:3d} {f_lbl}',
            copy_src(src_path),
            dict(kdf=KDF_SHA256, encrypt_range=True,
                 range_mode='auto', range_percent=pct,
                 range_b_bytes=16, range_c_bytes=64)))

# A2: degenerate windows — range_start == range_end (>0) → do_encrypt_range=False (5)
for src_path, f_lbl, mid_pt in (
    (MP4_SMALL,  'small',  SMALL_SIZE  // 2),
    (MP4_MEDIUM, 'medium', MEDIUM_SIZE // 2),
    (MP4_SMALL,  'small',  512),
    (MP4_MEDIUM, 'medium', MEDIUM_SIZE - 512),
    (MP4_SMALL,  'small',  1),
):
    run_id += 1
    reg(*run_case(run_id,
        f'A degenerate rs=re={mid_pt} {f_lbl} (no-op)',
        copy_src(src_path),
        dict(kdf=KDF_SHA256, encrypt_range=True,
             range_mode='auto', range_percent=50,
             range_start=mid_pt, range_end=mid_pt)))

# A3: range_start > file_size → do_encrypt_range=False (5)
for src_path, f_lbl, over_start in (
    (MP4_SMALL,  'small',  SMALL_SIZE  + 1),
    (MP4_SMALL,  'small',  SMALL_SIZE  + 1024),
    (MP4_MEDIUM, 'medium', MEDIUM_SIZE + 1),
    (MP4_MEDIUM, 'medium', MEDIUM_SIZE + 65536),
    (MP4_SMALL,  'small',  SMALL_SIZE  * 2),
):
    run_id += 1
    reg(*run_case(run_id,
        f'A start>{f_lbl}_size rs={over_start} (no-op)',
        copy_src(src_path),
        dict(kdf=KDF_SHA256, encrypt_range=True,
             range_mode='auto', range_percent=50,
             range_start=over_start, range_end=0)))

# ── Group B: encrypt_tail silent clamp (20) ───────────────────────────────────
# When file_size < 2 * encrypt_size, do_encrypt_tail silently becomes False.
# Result must be OK (not SKIP/error). Verified by round-trip hash check.

# B1: encrypt_size > file_size//2 → tail is silently dropped (10)
for src_path, f_lbl, fsz in (
    (MP4_SMALL, 'small', SMALL_SIZE), (MP4_MEDIUM, 'medium', MEDIUM_SIZE),
):
    for enc_sz in (fsz // 2 + 1, fsz // 2 + 512, fsz // 2 + 65536,
                   fsz - 1,       fsz):
        run_id += 1
        reg(*run_case(run_id,
            f'B tail_clamp enc_sz={enc_sz} {f_lbl} (tail silently dropped)',
            copy_src(src_path),
            dict(kdf=KDF_SHA256, encrypt_tail=True, encrypt_size=enc_sz)))

# B2: encrypt_size exactly at boundary (2*enc_sz == fsz-1, fsz, fsz+1) (6)
for src_path, f_lbl, fsz in (
    (MP4_SMALL, 'small', SMALL_SIZE), (MP4_MEDIUM, 'medium', MEDIUM_SIZE),
    (MP4_SMALL, 'small', SMALL_SIZE),
):
    half = fsz // 2
    for enc_sz in (half - 1, half, half + 1):
        run_id += 1
        reg(*run_case(run_id,
            f'B tail_boundary enc_sz={enc_sz} fsz={fsz} {f_lbl}',
            copy_src(src_path),
            dict(kdf=KDF_SHA256, encrypt_tail=True, encrypt_size=enc_sz)))

# B3: encrypt_size=0 with tail=True (tail silently dropped since 2*0=0<=fsz) (4)
for src_path, f_lbl in (
    (MP4_SMALL,  'small' ), (MP4_SMALL,  'small' ),
    (MP4_MEDIUM, 'medium'), (MP4_MEDIUM, 'medium'),
):
    run_id += 1
    reg(*run_case(run_id,
        f'B enc_size=0 tail=True {f_lbl} (head=empty,tail dropped)',
        copy_src(src_path),
        dict(kdf=KDF_SHA256, encrypt_tail=True, encrypt_size=0)))

# ── Group C: kdf=KDF_SCRYPT conditions (20) ──────────────────────────────────

# C1: scrypt + plain (no extras) × 3 files (3) — well just small+medium+3rd variant
for src_path, f_lbl in (
    (MP4_SMALL,  'small' ),
    (MP4_MEDIUM, 'medium'),
):
    for extra, elbl in (
        ({}, 'plain'),
        ({'encrypt_tail': True}, 'tail'),
        ({'encrypt_middle': True}, 'middle'),
        ({'encrypt_middle': True, 'encrypt_tail': True}, 'mid+tail'),
        ({'encrypt_range': True, 'range_mode': 'auto', 'range_percent': 25,
          'range_b_bytes': 16, 'range_c_bytes': 64}, 'range-25%'),
    ):
        run_id += 1
        reg(*run_case(run_id,
            f'C scrypt {elbl} {f_lbl}',
            copy_src(src_path),
            dict(kdf=KDF_SCRYPT, **extra)))

# ── Summary ───────────────────────────────────────────────────────────────────

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
