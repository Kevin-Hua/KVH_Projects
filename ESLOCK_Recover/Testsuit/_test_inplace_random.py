"""
Suite 13 — In-place mode randomized round-trips.
warp_file_inplace -> unwarp_auto -> SHA-256 verify.
100 fully randomized runs covering encrypt_size, middle_size, range_start/end,
kdf, tail, middle, range_mode, range_percent, b/c bytes.
Records per-run elapsed time; prints top-10 slowest at end.
"""
import sys, shutil, hashlib, random, traceback, time, os
from pathlib import Path

_HERE     = Path(__file__).parent
TESTFILES = _HERE / 'TestFiles'
sys.path.insert(0, str(_HERE.parent))
from kvhwarp_core import (
    warp_file_inplace, unwarp_auto,
    KDF_SCRYPT, KDF_SHA256, ENCRYPT_SIZE, ENCRYPT_MIDDLE_SIZE,
)

WORK = TESTFILES / '_tc13'
if WORK.exists(): shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(exist_ok=True)

PW   = 'InplcRnd@13!'
SEED = 20260419
RUNS = 100
rng  = random.Random(SEED)

MP4_SMALL  = TESTFILES / 'Mpg' / '1684427397013.mp4'
MP4_MEDIUM = TESTFILES / 'Mpg' / '1695318021338.mp4'
MP4_LARGE  = TESTFILES / 'Mpg' / '射的騷女臉上到處都是.mp4'

SOURCES = [MP4_SMALL, MP4_MEDIUM, MP4_LARGE]
KDF_POOL = [KDF_SHA256, KDF_SHA256, KDF_SHA256, KDF_SCRYPT]  # 3:1 weight

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
    file_size = src.stat().st_size

    kdf      = rng.choice(KDF_POOL)
    kdf_n    = 'scrypt' if kdf == KDF_SCRYPT else 'sha256'
    enc_size = rng.choice([0, 16, 64, 256, 512, 1024, 2048, 4096, 8192, 65536])
    tail     = rng.choice([False, True])
    middle   = rng.choice([False, True])
    # extended middle_size pool — up to ENCRYPT_MIDDLE_SIZE (1 MB)
    mid_sz   = rng.choice([16, 256, 1024, 4096, 16384, 65536,
                            262144, ENCRYPT_MIDDLE_SIZE, 4_000_000])
    do_range = rng.choice([False, False, True])
    r_mode   = rng.choice(['auto', 'manual'])
    r_pct    = rng.randint(1, 99)
    r_start  = rng.choice([0, 0, rng.randint(0, file_size // 4),
                            rng.randint(file_size // 4, file_size // 2)])
    r_end    = rng.choice([0, 0, file_size // 4, file_size // 2, file_size])
    r_b      = rng.choice([16, 32, 64, 128, 256])
    r_c      = rng.choice([r_b, r_b * 2, r_b * 4, 512, 1024])

    label = (f'[{src.stem[:14]}] kdf={kdf_n} enc={enc_size} '
             f'tail={tail} mid={middle} mid_sz={mid_sz} '
             f'rng={do_range} mode={r_mode} pct={r_pct} '
             f'rs={r_start} re={r_end} b={r_b} c={r_c}')

    work_src = WORK / src.name
    shutil.copy2(src, work_src)
    orig_hash = sha256file(work_src)
    t0 = time.perf_counter()
    try:
        kwargs = dict(kdf=kdf, encrypt_size=enc_size,
                      encrypt_tail=tail, encrypt_middle=middle,
                      encrypt_middle_size=mid_sz, encrypt_range=do_range,
                      range_mode=r_mode, range_percent=r_pct,
                      range_start=r_start, range_end=r_end,
                      range_b_bytes=r_b, range_c_bytes=r_c)
        w = warp_file_inplace(work_src, PW, **kwargs)
        if not w.startswith('OK'):
            raise RuntimeError(f'warp: {w}')
        ks = WORK / w.split('->')[1].strip().split()[0]
        u  = unwarp_auto(ks, PW)
        if not u.startswith('OK'):
            raise RuntimeError(f'unwarp: {u}')
        out = WORK / u.split('->')[1].strip().split()[0]
        if sha256file(out) != orig_hash:
            raise RuntimeError('HASH MISMATCH')
        elapsed = time.perf_counter() - t0
        PASS += 1
        results.append(f'PASS  [{run_id:3d}] {label}')
    except Exception:
        elapsed = time.perf_counter() - t0
        FAIL += 1
        tb = traceback.format_exc().strip().splitlines()[-1]
        results.append(f'FAIL  [{run_id:3d}] {label}\n      {tb}')
    finally:
        timings.append((elapsed, f'[{run_id:3d}] {label}'))
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


BINARY = Path(r'e:\Git\repository\KvhWarp\binary')
WORK   = BINARY / '_test_random'
WORK.mkdir(exist_ok=True)
PW     = 'TestRnd@99'
SEED   = 20260419
RUNS   = 100

rng = random.Random(SEED)

# ── Source files ─────────────────────────────────────────────────────────────
SOURCES = [
    BINARY / '1684427397013.mp4',           # ~1.2 MB  (smallest)
    BINARY / '1695318021338.mp4',            # ~3.3 MB
    BINARY / '射的騷女臉上到處都是.mp4',    # ~5   MB  (largest)
]

KDF_NAMES = {KDF_SCRYPT: 'scrypt', KDF_SHA256: 'sha256'}

def sha256file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()

def rand_params(file_size: int) -> dict:
    """Generate random but legal warp_file_inplace parameters for a given file_size."""
    kdf = rng.choice([KDF_SCRYPT, KDF_SHA256])

    # encrypt_size: 256 B .. min(8192, file_size // 4)
    # Keep small enough that tail & middle can still fit inside the file.
    enc_size_max = max(256, min(8192, file_size // 4))
    encrypt_size = rng.randint(256, enc_size_max)
    # Round to 16-byte boundary for cleaner AES block alignment
    encrypt_size = max(256, (encrypt_size // 16) * 16)

    # tail needs file_size >= 2 * encrypt_size
    can_tail = file_size >= 2 * encrypt_size
    encrypt_tail = rng.choice([False, True]) if can_tail else False

    # middle: size 256..min(file_size // 4, 65536), must fit in gap between head & tail
    gap_start = encrypt_size
    gap_end   = file_size - encrypt_size if encrypt_tail else file_size
    gap_avail = gap_end - gap_start
    encrypt_middle = False
    encrypt_middle_size = 1024
    if gap_avail >= 512:
        encrypt_middle = rng.choice([False, True])
        if encrypt_middle:
            mid_max = max(256, min(65536, gap_avail // 2))
            encrypt_middle_size = rng.randint(256, mid_max)
            encrypt_middle_size = max(256, (encrypt_middle_size // 16) * 16)

    # range: start/end are byte offsets into the file
    encrypt_range = rng.choice([False, True])
    range_start = 0
    range_end   = 0
    range_mode  = 'manual'
    range_percent = 25
    range_b_bytes = 16
    range_c_bytes = 64
    if encrypt_range:
        # range_start: 0..file_size//3  (leave room for a meaningful range)
        range_start = rng.randint(0, file_size // 3)
        # range_end: range_start + at least 64 B .. file_size
        r_end_min = range_start + 64
        r_end_max = file_size
        if r_end_min >= r_end_max:
            # degenerate — disable range
            encrypt_range = False
        else:
            range_end = rng.randint(r_end_min, r_end_max)
            range_mode = rng.choice(['auto', 'manual'])
            if range_mode == 'auto':
                range_percent = rng.choice([10, 25, 33, 50, 75])
            else:
                # b must be <= c; c must be >= 16
                c_choices = [32, 64, 128, 256, 512, 1024]
                range_c_bytes = rng.choice(c_choices)
                # b: 1..c (fraction of c that gets encrypted)
                b_frac = rng.choice([0.1, 0.25, 0.5, 0.75, 1.0])
                range_b_bytes = max(16, int(range_c_bytes * b_frac))
                range_b_bytes = (range_b_bytes // 16) * 16
                range_b_bytes = max(16, min(range_b_bytes, range_c_bytes))

    return dict(
        kdf=kdf,
        encrypt_size=encrypt_size,
        encrypt_tail=encrypt_tail,
        encrypt_middle=encrypt_middle,
        encrypt_middle_size=encrypt_middle_size,
        encrypt_range=encrypt_range,
        range_start=range_start,
        range_end=range_end,
        range_mode=range_mode,
        range_percent=range_percent,
        range_b_bytes=range_b_bytes,
        range_c_bytes=range_c_bytes,
    )

def param_summary(opts: dict, file_size: int) -> str:
    k   = KDF_NAMES[opts['kdf']]
    enc = opts['encrypt_size']
    t   = 'T' if opts['encrypt_tail']   else '-'
    m   = f"M{opts['encrypt_middle_size']}" if opts['encrypt_middle'] else '-'
    if opts['encrypt_range']:
        rs  = opts['range_start']
        re  = opts['range_end']
        rm  = opts['range_mode']
        pct = f"{rs//1024}K-{re//1024}K"
        if rm == 'auto':
            r = f"Rauto{opts['range_percent']}%({pct})"
        else:
            r = f"Rb{opts['range_b_bytes']}c{opts['range_c_bytes']}({pct})"
    else:
        r = 'R-'
    return f"kdf={k} enc={enc} tail={t} mid={m} {r}"

def cleanup_work():
    for item in WORK.iterdir():
        try: item.unlink()
        except Exception: pass

# ── Pre-compute hashes ────────────────────────────────────────────────────────
src_hashes = {src: sha256file(src) for src in SOURCES}

# ── Run tests ─────────────────────────────────────────────────────────────────
PASS = FAIL = 0
results = []

for run_idx in range(1, RUNS + 1):
    src = rng.choice(SOURCES)
    file_size = src.stat().st_size
    opts = rand_params(file_size)
    orig_hash = src_hashes[src]
    summary = param_summary(opts, file_size)
    header = f"[{run_idx:3d}] {src.name[:24]:24s}  {summary}"

    work_copy = WORK / src.name
    shutil.copy2(src, work_copy)

    try:
        w_res = warp_file_inplace(work_copy, PW, **opts)
        if not w_res.startswith('OK'):
            raise RuntimeError(f'warp: {w_res}')
        ks_name  = w_res.split('->')[1].strip().split()[0]
        ks_file  = WORK / ks_name

        u_res = unwarp_auto(ks_file, PW)
        if not u_res.startswith('OK'):
            raise RuntimeError(f'unwarp: {u_res}')
        out_name = u_res.split('->')[1].strip().split()[0]
        out_file = WORK / out_name

        restored = sha256file(out_file)
        if restored != orig_hash:
            raise RuntimeError(f'HASH MISMATCH orig={orig_hash[:12]} restored={restored[:12]}')

        PASS += 1
        results.append(f'PASS  {header}')
    except Exception as e:
        FAIL += 1
        tb = traceback.format_exc().strip().splitlines()[-1]
        results.append(f'FAIL  {header}\n      {tb}')
    finally:
        cleanup_work()

# ── Summary ───────────────────────────────────────────────────────────────────
print()
for r in results:
    print(r)
print()
print('=' * 80)
print(f'  PASS: {PASS}   FAIL: {FAIL}   TOTAL: {PASS + FAIL}')
print('=' * 80)

try:
    WORK.rmdir()
except Exception:
    pass
