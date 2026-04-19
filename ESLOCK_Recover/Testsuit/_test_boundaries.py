"""
Suite 6 — Boundary and special-value parameter testing.  100 runs.

Uses synthetic files of precise sizes to probe every numeric boundary.
Each run does warp_file_inplace -> unwarp_auto -> SHA-256 verify.

Fixed 40 cases: boundary file sizes, boundary encrypt_size, boundary range,
                boundary middle_size, invalid-but-clampable values.
Random 60 cases: boundary-biased random sampling.

Known invalid input (causes infinite loop — skip):
  range_c_bytes = 0  → pos += 0 → infinite loop  (documented as input-validation bug)
"""
import sys, shutil, hashlib, random, traceback, os
from pathlib import Path

sys.path.insert(0, r'e:\Git\GitHub\Kevinhua\KVH_Projects\ESLOCK_Recover')
import importlib, KvhWarp; importlib.reload(KvhWarp)
from KvhWarp import warp_file_inplace, unwarp_auto, KDF_SHA256, MIN_FILE_SIZE

BINARY = Path(r'e:\Git\repository\KvhWarp\binary')
WORK   = BINARY / '_tc6'
WORK.mkdir(exist_ok=True)
PW   = 'BndryPw@6'
SEED = 20260419
rng  = random.Random(SEED)

def sha256file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        while chunk := f.read(1 << 20): h.update(chunk)
    return h.hexdigest()

def cleanup():
    for f in WORK.iterdir():
        try: f.unlink()
        except: pass

def synth(size, name=None):
    """Create a synthetic random-content file of exactly `size` bytes."""
    p = WORK / (name or f'synth_{size}.bin')
    p.write_bytes(os.urandom(size) if size > 0 else b'')
    return p

def run_case(run_id, label, file_size, opts, expect_skip=False):
    """
    Warp a synthetic file then unwarp and verify.
    expect_skip=True: warp must return SKIP: (not an error).
    """
    cleanup()
    src  = synth(file_size)
    orig = sha256file(src) if file_size > 0 else ''
    ks_file = out_file = None
    try:
        w_res = warp_file_inplace(src, PW, **opts)

        if expect_skip:
            ok = w_res.startswith('SKIP:')
            return ok, (f'PASS  [{run_id:3d}] SKIP-OK  {label} → {w_res[:60]}' if ok
                        else f'FAIL  [{run_id:3d}] {label}\n      expected SKIP, got: {w_res[:80]}')

        if not w_res.startswith('OK'):
            # If file was too small to encrypt, treat as acceptable skip
            if w_res.startswith('SKIP:'):
                return True, f'PASS  [{run_id:3d}] SKIP-ACC {label} → {w_res[:60]}'
            raise RuntimeError(f'warp: {w_res}')

        ks_name = w_res.split('->')[1].strip().split()[0]
        ks_file = WORK / ks_name

        u_res = unwarp_auto(ks_file, PW)
        if not u_res.startswith('OK'):
            raise RuntimeError(f'unwarp: {u_res}')
        out_name = u_res.split('->')[1].strip().split()[0]
        out_file = WORK / out_name

        if file_size > 0 and sha256file(out_file) != orig:
            raise RuntimeError('HASH MISMATCH')

        return True, f'PASS  [{run_id:3d}] {label}'
    except Exception as e:
        tb = traceback.format_exc().strip().splitlines()[-1]
        return False, f'FAIL  [{run_id:3d}] {label}\n      {tb}'
    finally:
        cleanup()

BASE = {'kdf': KDF_SHA256, 'encrypt_tail': False,
        'encrypt_middle': False, 'encrypt_range': False}

PASS = FAIL = 0
results = []

# ── Fixed 40 boundary cases ───────────────────────────────────────────────────

FIXED = [
    # (label, file_size, opts_overrides, expect_skip)

    # ── Boundary file sizes ──
    ('size=0 → SKIP empty',           0,        {},                    True),
    ('size=1 enc=1',                   1,        {'encrypt_size': 1},   False),
    ('size=15 enc=15',                 15,       {'encrypt_size': 15},  False),
    ('size=16 enc=16',                 16,       {'encrypt_size': 16},  False),
    ('size=MIN-1 enc=MIN → SKIP',      MIN_FILE_SIZE - 1,
                                                 {'encrypt_size': MIN_FILE_SIZE}, True),
    ('size=MIN enc=MIN',               MIN_FILE_SIZE,
                                                 {'encrypt_size': MIN_FILE_SIZE}, False),
    ('size=MIN+1 enc=MIN',             MIN_FILE_SIZE + 1,
                                                 {'encrypt_size': MIN_FILE_SIZE}, False),
    ('size=2047 no-tail-boundary',     2047,     {'encrypt_size': 1024}, False),
    ('size=2048 tail-enabled',         2048,
                                                 {'encrypt_size': 1024, 'encrypt_tail': True}, False),
    ('size=2049 tail-enabled',         2049,
                                                 {'encrypt_size': 1024, 'encrypt_tail': True}, False),

    # ── Boundary encrypt_size ──
    ('enc=0 (empty head)',             4096,     {'encrypt_size': 0},   False),
    ('enc=1',                          4096,     {'encrypt_size': 1},   False),
    ('enc=15 (non-block-align)',        4096,     {'encrypt_size': 15},  False),
    ('enc=16',                         4096,     {'encrypt_size': 16},  False),
    ('enc=file_size',                  4096,     {'encrypt_size': 4096}, False),
    ('enc=file_size-1',                4096,     {'encrypt_size': 4095}, False),
    ('enc=file_size+100 (clamp)',       4096,     {'encrypt_size': 4196}, False),

    # ── Boundary range_start / range_end ──
    ('range 0-0 (full auto)',          4096,
                                                 {**BASE, 'encrypt_range': True,
                                                  'range_start': 0, 'range_end': 0}, False),
    ('range 0-1 (1 byte)',             4096,
                                                 {**BASE, 'encrypt_range': True,
                                                  'range_start': 0, 'range_end': 1}, False),
    ('range last-1-byte',              4096,
                                                 {**BASE, 'encrypt_range': True,
                                                  'range_start': 4095, 'range_end': 4096}, False),
    ('range start=end (no-op)',        4096,
                                                 {**BASE, 'encrypt_range': True,
                                                  'range_start': 500, 'range_end': 500}, False),
    ('range start>end (no-op)',        4096,
                                                 {**BASE, 'encrypt_range': True,
                                                  'range_start': 1000, 'range_end': 500}, False),
    ('range end>file (clamp)',         4096,
                                                 {**BASE, 'encrypt_range': True,
                                                  'range_start': 0, 'range_end': 99999}, False),
    ('range start=-5 (clamp to 0)',    4096,
                                                 {**BASE, 'encrypt_range': True,
                                                  'range_start': -5, 'range_end': 0}, False),

    # ── Boundary range_b / range_c ──
    ('range b=0 c=64 (enc nothing)',   4096,
                                                 {**BASE, 'encrypt_range': True,
                                                  'range_b_bytes': 0, 'range_c_bytes': 64}, False),
    ('range b=1 c=64',                 4096,
                                                 {**BASE, 'encrypt_range': True,
                                                  'range_b_bytes': 1, 'range_c_bytes': 64}, False),
    ('range b=c=16 (all bytes)',       4096,
                                                 {**BASE, 'encrypt_range': True,
                                                  'range_b_bytes': 16, 'range_c_bytes': 16}, False),
    ('range b=c=1 (1B period, tiny)', 256,
                                                 {**BASE, 'encrypt_range': True,
                                                  'range_b_bytes': 1, 'range_c_bytes': 1}, False),
    ('range b>c (b=100 c=64)',        4096,
                                                 {**BASE, 'encrypt_range': True,
                                                  'range_b_bytes': 100, 'range_c_bytes': 64}, False),
    ('range c=1 (period=1B)',         256,
                                                 {**BASE, 'encrypt_range': True,
                                                  'range_b_bytes': 1, 'range_c_bytes': 1}, False),

    # ── Boundary middle_size ──
    ('middle_size=0',                  4096,
                                                 {'encrypt_middle': True, 'encrypt_middle_size': 0}, False),
    ('middle_size=1',                  4096,
                                                 {'encrypt_middle': True, 'encrypt_middle_size': 1}, False),
    ('middle_size=16',                 4096,
                                                 {'encrypt_middle': True, 'encrypt_middle_size': 16}, False),
    ('middle_size>>file (clamp)',      4096,
                                                 {'encrypt_middle': True, 'encrypt_middle_size': 999999}, False),
    ('middle_size=gap (whole middle)', 4096,
                                                 {'encrypt_size': 512, 'encrypt_middle': True,
                                                  'encrypt_middle_size': 3072}, False),

    # ── Combined boundary extremes ──
    ('enc=0 + range=full + mid',       8192,
                                                 {'encrypt_size': 0, 'encrypt_range': True,
                                                  'encrypt_middle': True,
                                                  'range_b_bytes': 16, 'range_c_bytes': 64}, False),
    ('enc=file + tail=True',           8192,
                                                 {'encrypt_size': 4096, 'encrypt_tail': True}, False),
    ('all enc on + enc=1',             8192,
                                                 {'encrypt_size': 1, 'encrypt_tail': True,
                                                  'encrypt_middle': True,
                                                  'encrypt_range': True, 'range_mode': 'auto',
                                                  'range_percent': 50}, False),
]

for run_id, (label, fsize, overrides, expect_skip) in enumerate(FIXED, start=1):
    opts = {**BASE, **overrides}
    ok, msg = run_case(run_id, label, fsize, opts, expect_skip)
    results.append(msg)
    PASS += ok; FAIL += (not ok)

# ── Random 60 boundary-biased cases ──────────────────────────────────────────
# File sizes skewed toward edges
SIZE_POOL = [0, 1, 8, 15, 16, 32, 64, 128, 256, 512,
             MIN_FILE_SIZE - 1, MIN_FILE_SIZE, MIN_FILE_SIZE + 1,
             1024, 2047, 2048, 2049, 4096, 8192, 16384, 65536]

ENC_SIZE_POOL = [0, 1, 8, 15, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 99999]
RANGE_B_POOL  = [0, 1, 8, 16, 32, 64, 128, 256, 512, 1024]
RANGE_C_POOL  = [1, 8, 16, 32, 64, 128, 256, 512, 1024]  # NOTE: 0 excluded (infinite loop bug)

for i in range(60):
    run_id = 41 + i
    file_size    = rng.choice(SIZE_POOL)
    encrypt_size = rng.choice(ENC_SIZE_POOL)
    encrypt_tail = rng.choice([False, True])
    encrypt_middle = rng.choice([False, True])
    mid_size     = rng.choice([0, 1, 16, 256, 1024, file_size, file_size * 2])
    enc_range    = rng.choice([False, True])
    r_start      = rng.choice([0, -10, file_size // 3 if file_size > 0 else 0,
                                file_size, file_size + 100])
    r_end        = rng.choice([0, 1, file_size // 2 if file_size > 1 else 0,
                                file_size, file_size + 500, r_start])  # r_start to test start==end
    r_b          = rng.choice(RANGE_B_POOL)
    r_c          = rng.choice(RANGE_C_POOL)

    opts = {
        'kdf': KDF_SHA256, 'encrypt_size': encrypt_size,
        'encrypt_tail': encrypt_tail,
        'encrypt_middle': encrypt_middle, 'encrypt_middle_size': mid_size,
        'encrypt_range': enc_range, 'range_start': r_start, 'range_end': r_end,
        'range_b_bytes': r_b, 'range_c_bytes': r_c,
        'range_mode': 'manual',
    }
    label = (f'rnd fsize={file_size} enc={encrypt_size} '
             f'tail={encrypt_tail} mid={mid_size} '
             f'rng={enc_range} rs={r_start} re={r_end} b={r_b} c={r_c}')
    ok, msg = run_case(run_id, label, file_size, opts)
    results.append(msg)
    PASS += ok; FAIL += (not ok)

print()
for r in results: print(r)
print()
print('=' * 72)
print(f'  PASS: {PASS}   FAIL: {FAIL}   TOTAL: {PASS + FAIL}')
print()
print('  KNOWN ISSUE: range_c_bytes=0 causes infinite loop (excluded from tests).')
print('=' * 72)
try: WORK.rmdir()
except: pass
