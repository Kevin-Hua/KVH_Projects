"""
Suite 10 — Copy mode range parameter coverage (warp_file encrypt_range).
warp_file (range variants) -> unwarp_auto -> SHA-256 verify.
40 fixed + 60 random = 100 runs.

Fixed cases:
  A (14) — auto mode, percent sweep {1,5,10,25,50,75,99} × 2 files
  B (12) — manual mode, 6 new b/c combos × 2 files
  C  (8) — range_start > 0, partial-window tests (medium file)
  D  (6) — range_end > 0 (upper-bounded window) × 2 files
"""
import sys, shutil, hashlib, random, traceback
from pathlib import Path

_HERE     = Path(__file__).parent
TESTFILES = _HERE / 'TestFiles'
sys.path.insert(0, str(_HERE.parent))
from kvhwarp_core import (
    warp_file, unwarp_auto,
    KDF_SCRYPT, KDF_SHA256, ENCRYPT_SIZE,
)

WORK = TESTFILES / '_tc10'
if WORK.exists(): shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(exist_ok=True)

PW   = 'RangePw@10!'
SEED = 20260419
rng  = random.Random(SEED)

MP4_SMALL  = TESTFILES / 'Mpg' / '1684427397013.mp4'
MP4_MEDIUM = TESTFILES / 'Mpg' / '1695318021338.mp4'

MP4_SMALL_SIZE  = MP4_SMALL.stat().st_size
MP4_MEDIUM_SIZE = MP4_MEDIUM.stat().st_size

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

def run_case(run_id, label, src, warp_kwargs):
    """Warp src (copy mode) -> unwarp_auto -> SHA-256 verify. Cleans up in finally."""
    orig = sha256file(src)
    try:
        w = warp_file(src, PW, **warp_kwargs)
        if not w.startswith('OK'):
            raise RuntimeError(f'warp: {w}')
        ks = WORK / w.split('->')[1].strip().split()[0]
        if not src.exists():
            raise RuntimeError('source deleted by warp_file (not copy mode?)')
        if sha256file(src) != orig:
            raise RuntimeError('source modified by warp_file')
        u   = unwarp_auto(ks, PW)
        if not u.startswith('OK'):
            raise RuntimeError(f'unwarp: {u}')
        out = WORK / u.split('->')[1].strip().split()[0]
        if sha256file(out) != orig:
            raise RuntimeError('HASH MISMATCH')
        return True, f'PASS  [{run_id:3d}] {label}'
    except Exception:
        tb = traceback.format_exc().strip().splitlines()[-1]
        return False, f'FAIL  [{run_id:3d}] {label}\n      {tb}'
    finally:
        cleanup()

PASS = FAIL = 0
results = []
run_id  = 0

def reg(ok, line):
    global PASS, FAIL
    results.append(line)
    if ok: PASS += 1
    else:  FAIL += 1

# ── A: auto mode, percent sweep 1/5/10/25/50/75/99 × small + medium (14) ─────

for pct in (1, 5, 10, 25, 50, 75, 99):
    for src_path, file_label in ((MP4_SMALL, 'small'), (MP4_MEDIUM, 'medium')):
        run_id += 1
        reg(*run_case(run_id,
            f'range auto-{pct}% {file_label}',
            copy_src(src_path),
            dict(kdf=KDF_SHA256, encrypt_range=True,
                 range_mode='auto', range_percent=pct,
                 range_b_bytes=16, range_c_bytes=64)))

# ── B: manual mode, 6 new b/c combos × small + medium (12) ───────────────────

MANUAL_BC = [
    (16,  16,  'b=c=16(all)' ),
    (64,  64,  'b=c=64'      ),
    (16,  32,  'b16c32'      ),
    (128, 256, 'b128c256'    ),
    (256, 256, 'b=c=256'     ),
    (16,  4096,'b16c4096'    ),
]

for b, c, bc_label in MANUAL_BC:
    for src_path, file_label in ((MP4_SMALL, 'small'), (MP4_MEDIUM, 'medium')):
        run_id += 1
        reg(*run_case(run_id,
            f'range manual-{bc_label} {file_label}',
            copy_src(src_path),
            dict(kdf=KDF_SHA256, encrypt_range=True,
                 range_mode='manual',
                 range_b_bytes=b, range_c_bytes=c)))

# ── C: range_start > 0, partial-window tests on medium file (8) ──────────────

SZ = MP4_MEDIUM_SIZE
RANGE_START_CASES = [
    (512,          0,         'auto',   25, 16,  64, 'start=512'),
    (1024,         0,         'auto',   25, 16,  64, 'start=ENCRYPT_SIZE'),
    (2048,         0,         'manual', 25, 16,  64, 'start=2*ENC_SIZE'),
    (SZ // 4,      0,         'auto',   50, 16,  64, 'start=file/4'),
    (SZ // 2,      0,         'manual', 25, 32, 128, 'start=file/2'),
    (SZ + 1000,    0,         'auto',   25, 16,  64, 'start>file(no-op)'),
    (0,            SZ // 4,   'auto',   25, 16,  64, 'end=file/4'),
    (SZ // 4,      SZ // 2,   'manual', 25, 16,  64, 'start=file/4,end=file/2'),
]

for start, end, r_mode, r_pct, r_b, r_c, desc in RANGE_START_CASES:
    run_id += 1
    reg(*run_case(run_id,
        f'range medium {desc} mode={r_mode}',
        copy_src(MP4_MEDIUM),
        dict(kdf=KDF_SHA256, encrypt_range=True,
             range_start=start, range_end=end,
             range_mode=r_mode, range_percent=r_pct,
             range_b_bytes=r_b, range_c_bytes=r_c)))

# ── D: range_end > 0 (upper-bounded window) × small + medium (6) ─────────────

RANGE_END_CASES = [
    (ENCRYPT_SIZE + 512,     'manual', 16,  64,  'end=head+512'  ),
    (MP4_SMALL_SIZE // 4,    'auto',   16,  64,  'end=file/4'    ),
    (MP4_SMALL_SIZE // 2,    'auto',   50, 128,  'end=file/2'    ),
]

for end_small, r_mode, r_b, r_c, desc in RANGE_END_CASES:
    for src_path, file_label, end_val in (
        (MP4_SMALL,  'small',  end_small),
        (MP4_MEDIUM, 'medium', end_small),   # same byte offset on larger file
    ):
        run_id += 1
        reg(*run_case(run_id,
            f'range {file_label} {desc} mode={r_mode}',
            copy_src(src_path),
            dict(kdf=KDF_SHA256, encrypt_range=True,
                 range_start=0, range_end=end_val,
                 range_mode=r_mode,
                 range_b_bytes=r_b, range_c_bytes=r_c)))

# ── Random 60 ─────────────────────────────────────────────────────────────────

SOURCES_LIST = [
    (MP4_SMALL,  MP4_SMALL_SIZE,  'small'),
    (MP4_MEDIUM, MP4_MEDIUM_SIZE, 'medium'),
]

for _ in range(60):
    run_id += 1
    src_path, file_size, f_lbl = rng.choice(SOURCES_LIST)
    kdf     = rng.choice([KDF_SHA256, KDF_SHA256, KDF_SHA256, KDF_SCRYPT])
    kdf_n   = 'scrypt' if kdf == KDF_SCRYPT else 'sha256'
    do_range = rng.choice([True, True, False])
    r_mode  = rng.choice(['auto', 'auto', 'manual'])
    r_pct   = rng.randint(1, 99)
    # b/c bytes: b in multiples of 1, c >= b (using small values for speed)
    r_b     = rng.choice([1, 8, 16, 32, 64, 128])
    r_c     = rng.choice([r_b, r_b * 2, r_b * 4, 256, 512])
    # start/end: bias toward 0
    r_start = rng.choice([0, 0, 0,
                          rng.randint(1, file_size // 4),
                          rng.randint(file_size // 4, file_size // 2)])
    r_end   = rng.choice([0, 0, 0,
                          file_size // 4, file_size // 2, file_size])
    tail    = rng.choice([False, True])
    middle  = rng.choice([False, True])

    label = (f'rnd:{f_lbl} range={do_range} mode={r_mode} pct={r_pct} '
             f'b={r_b}c={r_c} start={r_start} end={r_end} kdf={kdf_n}')
    reg(*run_case(run_id, label, copy_src(src_path),
        dict(kdf=kdf, encrypt_range=do_range,
             range_mode=r_mode, range_percent=r_pct,
             range_b_bytes=r_b, range_c_bytes=r_c,
             range_start=r_start, range_end=r_end,
             encrypt_tail=tail, encrypt_middle=middle)))

# ── Summary ───────────────────────────────────────────────────────────────────

print()
for r in results: print(r)
print()
print('=' * 72)
print(f'  PASS: {PASS}   FAIL: {FAIL}   TOTAL: {PASS + FAIL}')
print('=' * 72)
try: WORK.rmdir()
except: pass
