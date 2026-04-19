"""
Suite 9 — Copy mode adaptive compression (warp_file compress parameter).
warp_file(compress=True/False) -> unwarp_auto -> SHA-256 verify.
40 fixed + 60 random = 100 runs.

Fixed cases:
  A (12) — compress=True, compressible .txt, kdf / tail / middle combos
  B  (4) — compress=True + range CTR enabled
  C  (8) — extension gate (.mp4) and entropy gate (random bytes), still roundtrips OK
  D  (6) — compress_max_bytes size gate: below / above / unlimited
  E  (4) — custom compress_skip_exts
  F  (6) — compress=False baseline (txt, mp4, random-bytes)
"""
import sys, os, shutil, hashlib, random, traceback
from pathlib import Path

_HERE     = Path(__file__).parent
TESTFILES = _HERE / 'TestFiles'
sys.path.insert(0, str(_HERE.parent))
from kvhwarp_core import (
    warp_file, unwarp_auto,
    KDF_SCRYPT, KDF_SHA256,
)

WORK = TESTFILES / '_tc9'
if WORK.exists(): shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(exist_ok=True)

PW   = 'CmprssPw@9!'
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

def make_txt(size_bytes, name='sample.txt'):
    """Create a compressible text file (repeating content)."""
    p = WORK / name
    unit = b'the quick brown fox jumps over the lazy dog 0123456789\n'
    p.write_bytes((unit * (size_bytes // len(unit) + 1))[:size_bytes])
    return p

def make_random(size_bytes, name='random.dat'):
    """Create a high-entropy file (compression entropy gate will fire)."""
    p = WORK / name
    p.write_bytes(os.urandom(size_bytes))
    return p

def copy_mp4(src):
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
        ks  = WORK / w.split('->')[1].strip().split()[0]
        # copy mode: source must remain intact
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

# ── A: compress=True, compressible .txt, kdf/tail/middle matrix (12) ──────────

for kdf, kdf_n in ((KDF_SCRYPT, 'scrypt'), (KDF_SHA256, 'sha256')):
    for tail in (False, True):
        for middle in (False, True):
            run_id += 1
            reg(*run_case(run_id,
                f'txt-50KB compress=T kdf={kdf_n} tail={tail} mid={middle}',
                make_txt(50_000),
                dict(kdf=kdf, compress=True,
                     encrypt_tail=tail, encrypt_middle=middle)))

for size, kdf, kdf_n, tail, middle in [
    (200_000, KDF_SCRYPT, 'scrypt', True,  True ),
    (100_000, KDF_SHA256, 'sha256', False, True ),
    (100_000, KDF_SCRYPT, 'scrypt', True,  True ),
    (  2_000, KDF_SHA256, 'sha256', False, False),
]:
    run_id += 1
    reg(*run_case(run_id,
        f'txt-{size//1000}KB compress=T kdf={kdf_n} tail={tail} mid={middle}',
        make_txt(size),
        dict(kdf=kdf, compress=True, encrypt_tail=tail, encrypt_middle=middle)))

# ── B: compress=True + range CTR (4) ──────────────────────────────────────────

for kdf, kdf_n, r_mode, r_pct, r_b, r_c, desc in [
    (KDF_SCRYPT, 'scrypt', 'auto',   25, 16,  64, 'auto-25%'),
    (KDF_SHA256, 'sha256', 'auto',   50, 16,  64, 'auto-50%'),
    (KDF_SCRYPT, 'scrypt', 'manual', 25, 16,  64, 'manual-b16c64'),
    (KDF_SHA256, 'sha256', 'auto',   25, 32, 128, 'auto-25%+b32c128'),
]:
    run_id += 1
    reg(*run_case(run_id,
        f'txt-50KB compress=T {desc} kdf={kdf_n}',
        make_txt(50_000),
        dict(kdf=kdf, compress=True,
             encrypt_range=True, range_mode=r_mode,
             range_percent=r_pct, range_b_bytes=r_b, range_c_bytes=r_c)))

# ── C: Extension gate – .mp4 (compress skipped, still encrypts OK) (4) ────────

for src_path, kdf, kdf_n, tail, middle in [
    (MP4_SMALL,  KDF_SCRYPT, 'scrypt', False, True ),
    (MP4_SMALL,  KDF_SHA256, 'sha256', True,  False),
    (MP4_MEDIUM, KDF_SCRYPT, 'scrypt', True,  True ),
    (MP4_MEDIUM, KDF_SHA256, 'sha256', False, False),
]:
    run_id += 1
    reg(*run_case(run_id,
        f'{src_path.stem[:16]} compress=T(ext-gate) kdf={kdf_n} tail={tail} mid={middle}',
        copy_mp4(src_path),
        dict(kdf=kdf, compress=True, encrypt_tail=tail, encrypt_middle=middle)))

# ── C: Entropy gate – random bytes .dat (compression skipped, still OK) (4) ──

for kdf, kdf_n, tail, middle in [
    (KDF_SCRYPT, 'scrypt', False, True ),
    (KDF_SHA256, 'sha256', True,  False),
    (KDF_SCRYPT, 'scrypt', True,  True ),
    (KDF_SHA256, 'sha256', False, False),
]:
    run_id += 1
    reg(*run_case(run_id,
        f'random-50KB compress=T(entropy-gate) kdf={kdf_n} tail={tail} mid={middle}',
        make_random(50_000),
        dict(kdf=kdf, compress=True, encrypt_tail=tail, encrypt_middle=middle)))

# ── D: compress_max_bytes size gate (6) ───────────────────────────────────────

for size, kdf, kdf_n, max_b, tail, middle, gate_desc in [
    (50_000, KDF_SCRYPT, 'scrypt', 10_000,   False, True,  'max<file→skip'),
    (50_000, KDF_SHA256, 'sha256', 10_000,   True,  False, 'max<file→skip'),
    (50_000, KDF_SCRYPT, 'scrypt', 100_000,  False, True,  'max>file→allow'),
    (50_000, KDF_SHA256, 'sha256', 100_000,  True,  True,  'max>file→allow'),
    (50_000, KDF_SCRYPT, 'scrypt', 0,        False, True,  'max=0(unlimited)'),
    (10_000, KDF_SHA256, 'sha256', 5_000,    False, False, '10KB+max=5KB→skip'),
]:
    run_id += 1
    reg(*run_case(run_id,
        f'txt-{size//1000}KB compress=T {gate_desc} kdf={kdf_n}',
        make_txt(size),
        dict(kdf=kdf, compress=True, compress_max_bytes=max_b,
             encrypt_tail=tail, encrypt_middle=middle)))

# ── E: custom compress_skip_exts (4) ──────────────────────────────────────────

for skip_exts, kdf, kdf_n, ext_desc in [
    (frozenset({'.txt'}),        KDF_SCRYPT, 'scrypt', '{.txt}→skip-txt'    ),
    (frozenset({'.txt'}),        KDF_SHA256, 'sha256', '{.txt}→skip-txt'    ),
    (frozenset(),                KDF_SCRYPT, 'scrypt', '{}→allow-all'       ),
    (frozenset({'.py', '.zip'}), KDF_SHA256, 'sha256', '{.py,.zip}→txt-OK' ),
]:
    run_id += 1
    reg(*run_case(run_id,
        f'txt-50KB compress=T skip_exts={ext_desc} kdf={kdf_n}',
        make_txt(50_000),
        dict(kdf=kdf, compress=True, compress_skip_exts=skip_exts)))

# ── F: compress=False baseline (6) ────────────────────────────────────────────

for ftype, size, src_path, kdf, kdf_n, tail, middle in [
    ('txt',    50_000, None,        KDF_SCRYPT, 'scrypt', False, True ),
    ('txt',    50_000, None,        KDF_SHA256, 'sha256', True,  True ),
    ('mp4',         0, MP4_SMALL,  KDF_SCRYPT, 'scrypt', False, True ),
    ('mp4',         0, MP4_MEDIUM, KDF_SHA256, 'sha256', True,  False),
    ('random', 50_000, None,        KDF_SCRYPT, 'scrypt', True,  True ),
    ('random', 50_000, None,        KDF_SHA256, 'sha256', False, False),
]:
    run_id += 1
    if ftype == 'txt':    src = make_txt(size)
    elif ftype == 'mp4':  src = copy_mp4(src_path)
    else:                 src = make_random(size)
    reg(*run_case(run_id,
        f'{ftype} compress=F kdf={kdf_n} tail={tail} mid={middle}',
        src,
        dict(kdf=kdf, compress=False, encrypt_tail=tail, encrypt_middle=middle)))

# ── Random 60 ─────────────────────────────────────────────────────────────────

FILE_TYPES     = ['txt', 'txt', 'txt', 'mp4_small', 'mp4_medium', 'random']
TXT_SIZES      = [2_000, 10_000, 50_000, 200_000]
RANDOM_SIZES   = [5_000, 50_000]
MAX_BYTES_OPTS = [0, 1_000, 50_000, 500_000]
SKIP_VARIANTS  = [
    None,
    frozenset({'.txt'}),
    frozenset({'.mp4'}),
    frozenset(),
    frozenset({'.dat', '.bin'}),
]

for _ in range(60):
    run_id += 1
    ftype    = rng.choice(FILE_TYPES)
    kdf      = rng.choice([KDF_SHA256, KDF_SHA256, KDF_SHA256, KDF_SCRYPT])
    kdf_n    = 'scrypt' if kdf == KDF_SCRYPT else 'sha256'
    compress = rng.choice([True, True, False])
    max_b    = rng.choice(MAX_BYTES_OPTS)
    skip_ext = rng.choice(SKIP_VARIANTS)
    tail     = rng.choice([False, True])
    middle   = rng.choice([False, True])

    if ftype == 'txt':
        size = rng.choice(TXT_SIZES)
        src  = make_txt(size)
        desc = f'txt-{size//1000}KB'
    elif ftype == 'mp4_small':
        src  = copy_mp4(MP4_SMALL)
        desc = 'mp4-small'
    elif ftype == 'mp4_medium':
        src  = copy_mp4(MP4_MEDIUM)
        desc = 'mp4-med'
    else:
        size = rng.choice(RANDOM_SIZES)
        src  = make_random(size)
        desc = f'random-{size//1000}KB'

    kwargs = dict(kdf=kdf, compress=compress, compress_max_bytes=max_b,
                  encrypt_tail=tail, encrypt_middle=middle)
    if skip_ext is not None:
        kwargs['compress_skip_exts'] = skip_ext

    reg(*run_case(run_id,
        f'rnd:{desc} compress={compress} max={max_b} kdf={kdf_n}',
        src, kwargs))

# ── Summary ───────────────────────────────────────────────────────────────────

print()
for r in results: print(r)
print()
print('=' * 72)
print(f'  PASS: {PASS}   FAIL: {FAIL}   TOTAL: {PASS + FAIL}')
print('=' * 72)
try: WORK.rmdir()
except: pass
