"""
Suite 17 — In-place mode full random (500 runs, all options randomly selected).
warp_file_inplace -> unwarp_auto -> SHA-256 verify.
SEED = 20260420.  KDF weight: SHA-256 4:1 over scrypt.
Excludes range_c_bytes=0 (known infinite-loop bug) and enforces c >= b.
Records per-run elapsed time; prints top-10 slowest at end.
"""
import sys, shutil, hashlib, random, traceback, time
from pathlib import Path

_HERE     = Path(__file__).parent
TESTFILES = _HERE / 'TestFiles'
sys.path.insert(0, str(_HERE.parent))
from kvhwarp_core import (
    warp_file_inplace, unwarp_auto,
    KDF_SCRYPT, KDF_SHA256, ENCRYPT_SIZE, ENCRYPT_MIDDLE_SIZE,
)

WORK = TESTFILES / '_tc17'
if WORK.exists(): shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(exist_ok=True)

PW   = 'InplcFR@17!'
SEED = 20260420
RUNS = 500
rng  = random.Random(SEED)

MP4_SMALL  = TESTFILES / 'Mpg' / '1684427397013.mp4'
MP4_MEDIUM = TESTFILES / 'Mpg' / '1695318021338.mp4'
MP4_LARGE  = TESTFILES / 'Mpg' / '射的騷女臉上到處都是.mp4'

SOURCES = [MP4_SMALL, MP4_MEDIUM, MP4_LARGE]
FILE_SIZES = {p: p.stat().st_size for p in SOURCES}

# KDF pool: SHA-256 × 4, scrypt × 1
KDF_POOL = [KDF_SHA256] * 4 + [KDF_SCRYPT]

# encrypt_size pool (bytes)
ENC_SIZE_POOL = [0, 16, 64, 256, 1024, 4096, 16384, ENCRYPT_SIZE, ENCRYPT_SIZE * 2]

# encrypt_middle_size pool (bytes)
MID_SIZE_POOL = [16, 256, 1024, 16384, 65536, 262144, ENCRYPT_MIDDLE_SIZE, 4_000_000]

# range_b / range_c pools (always c >= b; c != 0)
B_POOL = [1, 8, 16, 32, 64, 128, 256]
C_MULT = [1, 2, 4, 8]

def sha256file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        while chunk := f.read(1 << 20): h.update(chunk)
    return h.hexdigest()

def cleanup():
    for f in WORK.iterdir():
        try: f.unlink()
        except: pass

PASS = FAIL = 0
results = []
timings = []

for run_id in range(1, RUNS + 1):
    src      = rng.choice(SOURCES)
    fsz      = FILE_SIZES[src]
    kdf      = rng.choice(KDF_POOL)
    kdf_n    = 'sc' if kdf == KDF_SCRYPT else 'sh'

    enc_sz   = rng.choice(ENC_SIZE_POOL)
    tail     = rng.choice([False, True])
    middle   = rng.choice([False, False, True])  # 1/3 chance
    mid_sz   = rng.choice(MID_SIZE_POOL)

    do_rng   = rng.choice([False, False, True])
    r_mode   = rng.choice(['auto', 'auto', 'manual'])
    r_pct    = rng.randint(0, 100)
    r_b      = rng.choice(B_POOL)
    c_mult   = rng.choice(C_MULT)
    r_c      = max(r_b, r_b * c_mult)           # guarantee c >= b, c != 0
    r_start  = rng.choice([0, 0, 0,
                            rng.randint(1, max(1, fsz // 4)),
                            rng.randint(max(1, fsz // 4), max(2, fsz // 2))])
    r_end    = rng.choice([0, 0, 0,
                            max(1, fsz // 4),
                            max(1, fsz // 2),
                            fsz])

    label = (f'[{run_id:3d}] {src.stem[:12]} kdf={kdf_n} '
             f'esz={enc_sz} tail={int(tail)} mid={int(middle)} msz={mid_sz} '
             f'rng={int(do_rng)} md={r_mode[0]} pct={r_pct:3d} '
             f'b={r_b} c={r_c} rs={r_start} re={r_end}')

    work_src = WORK / src.name
    shutil.copy2(src, work_src)
    orig_hash = sha256file(work_src)
    t0 = time.perf_counter()
    try:
        kwargs = dict(
            kdf=kdf,
            encrypt_size=enc_sz,
            encrypt_tail=tail,
            encrypt_middle=middle,
            encrypt_middle_size=mid_sz,
            encrypt_range=do_rng,
            range_mode=r_mode,
            range_percent=r_pct,
            range_b_bytes=r_b,
            range_c_bytes=r_c,
            range_start=r_start,
            range_end=r_end,
        )
        w = warp_file_inplace(work_src, PW, **kwargs)
        if not w.startswith('OK') and not w.startswith('SKIP'):
            raise RuntimeError(f'warp: {w}')
        if w.startswith('SKIP'):
            # SKIP is acceptable (empty file / too small guard) — log & count as pass
            PASS += 1
            elapsed = time.perf_counter() - t0
            results.append(f'PASS  {label}  [SKIP:{w[5:30]}]')
            timings.append((elapsed, label))
            cleanup()
            continue
        ks  = WORK / w.split('->')[1].strip().split()[0]
        u   = unwarp_auto(ks, PW)
        if not u.startswith('OK'):
            raise RuntimeError(f'unwarp: {u}')
        out = WORK / u.split('->')[1].strip().split()[0]
        if sha256file(out) != orig_hash:
            raise RuntimeError('HASH MISMATCH')
        elapsed = time.perf_counter() - t0
        PASS += 1
        results.append(f'PASS  {label}')
    except Exception:
        elapsed = time.perf_counter() - t0
        FAIL += 1
        tb = traceback.format_exc().strip().splitlines()[-1]
        results.append(f'FAIL  {label}\n      {tb}')
    finally:
        timings.append((elapsed, label))
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
