"""
Suite 15 — In-place mode encrypt_middle_size matrix.
warp_file_inplace -> unwarp_auto -> SHA-256 verify.
40 fixed + 60 random = 100 runs.
Records per-run elapsed time; prints top-10 slowest at end.

Fixed cases:
  A (14) — encrypt_middle=True size sweep × small + medium
  B  (8) — boundary synthetic sizes (0, 1, tiny power-of-two)
  C  (6) — oversized clip (encrypt_middle_size >> file size)
  D  (6) — middle + tail together
  E  (6) — middle + range together
"""
import sys, shutil, hashlib, random, traceback, time
from pathlib import Path

_HERE     = Path(__file__).parent
TESTFILES = _HERE / 'TestFiles'
sys.path.insert(0, str(_HERE.parent))
from kvhwarp_core import (
    warp_file_inplace, unwarp_auto,
    KDF_SCRYPT, KDF_SHA256, ENCRYPT_MIDDLE_SIZE,
)

WORK = TESTFILES / '_tc15'
if WORK.exists(): shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(exist_ok=True)

PW   = 'InplcMid@15!'
SEED = 20260419
rng  = random.Random(SEED)

MP4_SMALL  = TESTFILES / 'Mpg' / '1684427397013.mp4'
MP4_MEDIUM = TESTFILES / 'Mpg' / '1695318021338.mp4'
MP4_LARGE  = TESTFILES / 'Mpg' / '射的騷女臉上到處都是.mp4'

MP4_SMALL_SIZE  = MP4_SMALL.stat().st_size
MP4_MEDIUM_SIZE = MP4_MEDIUM.stat().st_size
MP4_LARGE_SIZE  = MP4_LARGE.stat().st_size

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
    orig = sha256file(src)
    t0 = time.perf_counter()
    try:
        w = warp_file_inplace(src, PW, **warp_kwargs)
        if not w.startswith('OK'):
            raise RuntimeError(f'warp: {w}')
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

# ── A: encrypt_middle size sweep × small + medium (14) ───────────────────────

MID_SIZES = [16, 64, 256, 1024, 4096, 32768, 131072]

for ms in MID_SIZES:
    for src_path, f_lbl in ((MP4_SMALL, 'small'), (MP4_MEDIUM, 'medium')):
        run_id += 1
        reg(*run_case(run_id,
            f'middle_size={ms} {f_lbl}',
            copy_src(src_path),
            dict(kdf=KDF_SHA256, encrypt_middle=True, encrypt_middle_size=ms)))

# ── B: boundary synthetic sizes (8) ──────────────────────────────────────────

BOUNDARY_COMBOS = [
    (0,                   'small',  MP4_SMALL,  'ms=0(default)'),
    (1,                   'small',  MP4_SMALL,  'ms=1'),
    (16,                  'small',  MP4_SMALL,  'ms=16(min)'),
    (ENCRYPT_MIDDLE_SIZE, 'small',  MP4_SMALL,  'ms=MAX'),
    (ENCRYPT_MIDDLE_SIZE, 'medium', MP4_MEDIUM, 'ms=MAX'),
    (ENCRYPT_MIDDLE_SIZE * 2, 'medium', MP4_MEDIUM, 'ms=2×MAX(clip)'),
    (16,                  'large',  MP4_LARGE,  'ms=16 large'),
    (ENCRYPT_MIDDLE_SIZE, 'large',  MP4_LARGE,  'ms=MAX large'),
]

for ms, f_lbl, src_path, desc in BOUNDARY_COMBOS:
    run_id += 1
    reg(*run_case(run_id,
        f'boundary {desc} {f_lbl}',
        copy_src(src_path),
        dict(kdf=KDF_SHA256, encrypt_middle=True, encrypt_middle_size=ms)))

# ── C: oversized clip — ms >> file (6) ───────────────────────────────────────

OVERSIZED_CASES = [
    (MP4_SMALL,  MP4_SMALL_SIZE  * 4, 'small'),
    (MP4_SMALL,  MP4_SMALL_SIZE  * 8, 'small'),
    (MP4_SMALL,  10_000_000,          'small'),
    (MP4_MEDIUM, MP4_MEDIUM_SIZE * 2, 'medium'),
    (MP4_MEDIUM, MP4_MEDIUM_SIZE * 4, 'medium'),
    (MP4_LARGE,  MP4_LARGE_SIZE  * 2, 'large'),
]

for src_path, ms, f_lbl in OVERSIZED_CASES:
    run_id += 1
    reg(*run_case(run_id,
        f'oversized ms={ms} {f_lbl}',
        copy_src(src_path),
        dict(kdf=KDF_SHA256, encrypt_middle=True, encrypt_middle_size=ms)))

# ── D: middle + tail (6) ──────────────────────────────────────────────────────

D_CASES = [
    (16,                  True,  True,  'small',  MP4_SMALL  ),
    (65536,               True,  True,  'small',  MP4_SMALL  ),
    (ENCRYPT_MIDDLE_SIZE, True,  True,  'small',  MP4_SMALL  ),
    (16,                  True,  True,  'medium', MP4_MEDIUM ),
    (65536,               True,  True,  'medium', MP4_MEDIUM ),
    (ENCRYPT_MIDDLE_SIZE, True,  True,  'large',  MP4_LARGE  ),
]

for ms, middle, tail, f_lbl, src_path in D_CASES:
    run_id += 1
    reg(*run_case(run_id,
        f'mid+tail ms={ms} {f_lbl}',
        copy_src(src_path),
        dict(kdf=KDF_SHA256, encrypt_middle=middle, encrypt_middle_size=ms, encrypt_tail=tail)))

# ── E: middle + range (6) ────────────────────────────────────────────────────

E_CASES = [
    (16,                  True, 'auto',   50, 16,  64,  'small',  MP4_SMALL ),
    (65536,               True, 'auto',   25, 16,  64,  'small',  MP4_SMALL ),
    (ENCRYPT_MIDDLE_SIZE, True, 'manual', 25, 32, 128,  'medium', MP4_MEDIUM),
    (16,                  True, 'auto',   75, 16,  64,  'medium', MP4_MEDIUM),
    (65536,               True, 'manual', 25, 64, 256,  'medium', MP4_MEDIUM),
    (ENCRYPT_MIDDLE_SIZE, True, 'auto',   50, 16,  64,  'large',  MP4_LARGE ),
]

for ms, do_rng, r_mode, r_pct, r_b, r_c, f_lbl, src_path in E_CASES:
    run_id += 1
    reg(*run_case(run_id,
        f'mid+range ms={ms} {r_mode} {f_lbl}',
        copy_src(src_path),
        dict(kdf=KDF_SHA256, encrypt_middle=True, encrypt_middle_size=ms,
             encrypt_range=do_rng, range_mode=r_mode, range_percent=r_pct,
             range_b_bytes=r_b, range_c_bytes=r_c)))

# ── Random 60 ─────────────────────────────────────────────────────────────────

MID_POOL = [16, 64, 256, 1024, 4096, 16384, 65536, 262144, ENCRYPT_MIDDLE_SIZE, 4_000_000]

for _ in range(60):
    run_id += 1
    src_path = rng.choice([MP4_SMALL, MP4_MEDIUM, MP4_LARGE])
    kdf      = rng.choice([KDF_SHA256, KDF_SHA256, KDF_SHA256, KDF_SCRYPT])
    kdf_n    = 'scrypt' if kdf == KDF_SCRYPT else 'sha256'
    ms       = rng.choice(MID_POOL)
    tail     = rng.choice([False, True])
    do_rng   = rng.choice([False, False, True])
    r_b      = rng.choice([16, 32, 64])
    r_c      = rng.choice([r_b, r_b * 2, 256])
    label    = (f'rnd:{src_path.stem[:10]} kdf={kdf_n} ms={ms} '
                f'tail={tail} range={do_rng} b={r_b}c={r_c}')
    kwargs   = dict(kdf=kdf, encrypt_middle=True, encrypt_middle_size=ms,
                    encrypt_tail=tail, encrypt_range=do_rng,
                    range_b_bytes=r_b, range_c_bytes=r_c)
    reg(*run_case(run_id, label, copy_src(src_path), kwargs))

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
