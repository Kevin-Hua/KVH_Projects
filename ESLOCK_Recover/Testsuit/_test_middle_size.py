"""
Suite 11 — Copy mode encrypt_middle_size coverage (warp_file).
warp_file (vary encrypt_middle_size) -> unwarp_auto -> SHA-256 verify.
40 fixed + 60 random = 100 runs.

Fixed cases:
  A (14) — middle_size sweep {1KB,4KB,16KB,64KB,256KB,512KB,2MB} × small + medium
  B  (8) — boundary synthetic files (exact sizes, gap=0 and gap=small)
  C  (6) — over-large middle_size clips to gap × 3 file/size combos
  D  (6) — middle_size + encrypt_tail=True × 3 sizes × 2 files
  E  (6) — middle_size + encrypt_range=True × 2 middle_sizes × 3 files
"""
import sys, os, shutil, hashlib, random, traceback
from pathlib import Path

_HERE     = Path(__file__).parent
TESTFILES = _HERE / 'TestFiles'
sys.path.insert(0, str(_HERE.parent))
from kvhwarp_core import (
    warp_file, unwarp_auto,
    KDF_SCRYPT, KDF_SHA256, ENCRYPT_SIZE, ENCRYPT_MIDDLE_SIZE,
)

WORK = TESTFILES / '_tc11'
if WORK.exists(): shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(exist_ok=True)

PW   = 'MidSzPw@11!'
SEED = 20260419
rng  = random.Random(SEED)

MP4_SMALL  = TESTFILES / 'Mpg' / '1684427397013.mp4'
MP4_MEDIUM = TESTFILES / 'Mpg' / '1695318021338.mp4'

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

def synth(size_bytes, name=None):
    """Synthetic binary file of exactly size_bytes bytes."""
    p = WORK / (name or f'synth_{size_bytes}.bin')
    p.write_bytes(os.urandom(size_bytes) if size_bytes > 0 else b'')
    return p

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

# ── A: middle_size sweep × small + medium file (14) ───────────────────────────
# Values cover: well below default (1MB), at default, above default

MIDDLE_SIZES = [
    (    1_024, '1KB' ),
    (    4_096, '4KB' ),
    (   16_384, '16KB'),
    (   65_536, '64KB'),
    (  262_144, '256KB'),
    (  524_288, '512KB'),
    (2_097_152, '2MB' ),
]

for mid_sz, mid_lbl in MIDDLE_SIZES:
    for src_path, f_lbl in ((MP4_SMALL, 'small'), (MP4_MEDIUM, 'medium')):
        run_id += 1
        reg(*run_case(run_id,
            f'middle_size={mid_lbl} {f_lbl}',
            copy_src(src_path),
            dict(kdf=KDF_SHA256, encrypt_middle=True,
                 encrypt_middle_size=mid_sz,
                 encrypt_tail=False, encrypt_range=False)))

# ── B: boundary synthetic files (8) ───────────────────────────────────────────
# ENCRYPT_SIZE = 1024, so:
#   file=2048, tail=True  → gap=0  → middle region empty (no middle applied)
#   file=2048, tail=False → gap=1024 → middle can fill it
#   file=3072, various tail/middle_size combos

BOUNDARY_CASES = [
    # (label, file_size, kdf, tail, middle_size)
    ('2048 tail=T gap=0 mid=1MB',  2048, KDF_SHA256, True,  ENCRYPT_MIDDLE_SIZE  ),
    ('2048 tail=T gap=0 mid=1KB',  2048, KDF_SCRYPT, True,  1_024                ),
    ('2048 tail=F gap=1KB mid=1MB', 2048, KDF_SHA256, False, ENCRYPT_MIDDLE_SIZE ),
    ('2048 tail=F gap=1KB mid=64',  2048, KDF_SHA256, False, 64                  ),
    ('3072 tail=T gap=1KB mid=1MB',   3072, KDF_SHA256, True,  ENCRYPT_MIDDLE_SIZE ),
    ('3072 tail=F gap=2KB mid=1MB',   3072, KDF_SHA256, False, ENCRYPT_MIDDLE_SIZE ),
    ('3072 tail=T gap=1KB mid=16KB',  3072, KDF_SCRYPT, True,  16_384             ),
    ('4096 tail=T gap=2KB mid=2MB',   4096, KDF_SHA256, True,  2_097_152          ),
]

for label, file_size, kdf, tail, mid_sz in BOUNDARY_CASES:
    run_id += 1
    reg(*run_case(run_id, label,
        synth(file_size),
        dict(kdf=kdf, encrypt_middle=True,
             encrypt_middle_size=mid_sz, encrypt_tail=tail)))

# ── C: over-large middle_size clips to full gap (6) ───────────────────────────
# middle_size >> file_size → desired window extends beyond gap → clips to whole gap

OVERSIZE_CASES = [
    (MP4_SMALL,  'small',  10_000_000,   '10MB'),
    (MP4_SMALL,  'small',  100_000_000,  '100MB'),
    (MP4_MEDIUM, 'medium', 10_000_000,   '10MB'),
    (MP4_MEDIUM, 'medium', 100_000_000,  '100MB'),
    (MP4_SMALL,  'small',  50_000_000,   '50MB'),
    (MP4_MEDIUM, 'medium', 50_000_000,   '50MB'),
]

for src_path, f_lbl, mid_sz, sz_lbl in OVERSIZE_CASES:
    run_id += 1
    reg(*run_case(run_id,
        f'middle_size={sz_lbl}(clips) {f_lbl}',
        copy_src(src_path),
        dict(kdf=KDF_SHA256, encrypt_middle=True,
             encrypt_middle_size=mid_sz, encrypt_tail=False)))

# ── D: middle_size + encrypt_tail=True × 3 sizes × 2 files (6) ───────────────

TAIL_SIZES = [16_384, 262_144, ENCRYPT_MIDDLE_SIZE]

for mid_sz in TAIL_SIZES:
    sz_lbl = f'{mid_sz // 1024}KB' if mid_sz < 1_000_000 else f'{mid_sz // 1_048_576}MB'
    for src_path, f_lbl in ((MP4_SMALL, 'small'), (MP4_MEDIUM, 'medium')):
        run_id += 1
        reg(*run_case(run_id,
            f'middle_size={sz_lbl}+tail=T {f_lbl}',
            copy_src(src_path),
            dict(kdf=KDF_SHA256, encrypt_middle=True,
                 encrypt_middle_size=mid_sz, encrypt_tail=True)))

# ── E: middle_size + encrypt_range=True × 2 sizes × 3 files (6) ──────────────

RANGE_MID_SIZES = [16_384, ENCRYPT_MIDDLE_SIZE]
RANGE_SOURCES   = [
    (MP4_SMALL,  'small' ),
    (MP4_MEDIUM, 'medium'),
]

for mid_sz in RANGE_MID_SIZES:
    sz_lbl = f'{mid_sz // 1024}KB' if mid_sz < 1_000_000 else f'{mid_sz // 1_048_576}MB'
    for src_path, f_lbl in RANGE_SOURCES:
        run_id += 1
        reg(*run_case(run_id,
            f'middle_size={sz_lbl}+range=auto25% {f_lbl}',
            copy_src(src_path),
            dict(kdf=KDF_SHA256, encrypt_middle=True,
                 encrypt_middle_size=mid_sz,
                 encrypt_range=True, range_mode='auto', range_percent=25,
                 range_b_bytes=16, range_c_bytes=64)))

# ── small + medium only = 4 cases; add 2 more with medium + range manual ──────

for mid_sz, desc in ((65_536, '64KB'), (524_288, '512KB')):
    run_id += 1
    reg(*run_case(run_id,
        f'middle_size={desc}+range=manual-b16c64 medium',
        copy_src(MP4_MEDIUM),
        dict(kdf=KDF_SHA256, encrypt_middle=True,
             encrypt_middle_size=mid_sz,
             encrypt_range=True, range_mode='manual',
             range_b_bytes=16, range_c_bytes=64)))

# ── Random 60 ─────────────────────────────────────────────────────────────────

SOURCES_LIST = [
    (MP4_SMALL,  'small' ),
    (MP4_MEDIUM, 'medium'),
]
MID_SIZE_POOL = [
    1_024, 4_096, 16_384, 65_536, 262_144,
    ENCRYPT_MIDDLE_SIZE, 2_097_152, 10_000_000,
]

for _ in range(60):
    run_id += 1
    src_path, f_lbl = rng.choice(SOURCES_LIST)
    kdf     = rng.choice([KDF_SHA256, KDF_SHA256, KDF_SHA256, KDF_SCRYPT])
    kdf_n   = 'scrypt' if kdf == KDF_SCRYPT else 'sha256'
    mid_sz  = rng.choice(MID_SIZE_POOL)
    tail    = rng.choice([False, False, True])
    do_range = rng.choice([False, False, True])
    r_pct   = rng.randint(1, 99)
    r_b     = rng.choice([16, 32, 64])
    r_c     = rng.choice([r_b, r_b * 2, r_b * 4])

    sz_lbl  = (f'{mid_sz // 1024}KB' if mid_sz < 1_000_000
               else f'{mid_sz // 1_048_576}MB')
    label   = (f'rnd:{f_lbl} mid_size={sz_lbl} tail={tail} '
               f'range={do_range} kdf={kdf_n}')

    kwargs = dict(kdf=kdf, encrypt_middle=True, encrypt_middle_size=mid_sz,
                  encrypt_tail=tail, encrypt_range=do_range)
    if do_range:
        kwargs.update(range_mode='auto', range_percent=r_pct,
                      range_b_bytes=r_b, range_c_bytes=r_c)

    reg(*run_case(run_id, label, copy_src(src_path), kwargs))

# ── Summary ───────────────────────────────────────────────────────────────────

print()
for r in results: print(r)
print()
print('=' * 72)
print(f'  PASS: {PASS}   FAIL: {FAIL}   TOTAL: {PASS + FAIL}')
print('=' * 72)
try: WORK.rmdir()
except: pass
