"""
Suite 8 — Copy-mode vs In-place mode equivalence.  100 runs.

For each run:
  1. Copy source → copy_A and copy_B in WORK
  2. warp_file(copy_A, PW, **opts)      → ks_copy   (copy_A unchanged)
  3. warp_file_inplace(copy_B, PW, **opts) → ks_inplace (copy_B renamed)
  4. unwarp_auto(ks_copy, PW)    → restored_copy
  5. unwarp_auto(ks_inplace, PW) → restored_inplace
  6. SHA-256: restored_copy == restored_inplace == original

Fixed 20 cases: deterministic option sets (minimal, full, boundary params).
Random 80 cases: fully randomised options.
"""
import sys, shutil, hashlib, random, traceback
from pathlib import Path

sys.path.insert(0, r'e:\Git\GitHub\Kevinhua\KVH_Projects\ESLOCK_Recover')
import importlib, KvhWarp; importlib.reload(KvhWarp)
from KvhWarp import warp_file, warp_file_inplace, unwarp_auto, KDF_SCRYPT, KDF_SHA256

BINARY = Path(r'e:\Git\repository\KvhWarp\binary')
WORK   = BINARY / '_tc8'
if WORK.exists(): shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(exist_ok=True)
PW     = 'EqvPw@Test8'
SEED   = 20260419
rng    = random.Random(SEED)

SOURCES = [
    BINARY / '1684427397013.mp4',
    BINARY / '1695318021338.mp4',
    BINARY / '射的騷女臉上到處都是.mp4',
]
KDF_NAMES = {KDF_SCRYPT: 'scrypt', KDF_SHA256: 'sha256'}

def sha256file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        while chunk := f.read(1 << 20): h.update(chunk)
    return h.hexdigest()

def cleanup():
    for f in WORK.iterdir():
        try: f.unlink()
        except OSError:
            import time; time.sleep(0.05)
            try: f.unlink()
            except: pass

def run_equivalence(run_id, src, opts):
    orig_hash = sha256file(src)
    label = (f"kdf={KDF_NAMES[opts['kdf']]} tail={opts['encrypt_tail']} "
             f"mid={opts['encrypt_middle']} rng={opts['encrypt_range']}")

    # Prepare two copies
    copy_a = WORK / (src.stem + '_A' + src.suffix)
    copy_b = WORK / (src.stem + '_B' + src.suffix)
    shutil.copy2(src, copy_a)
    shutil.copy2(src, copy_b)

    try:
        # ── Copy mode ──
        w_copy = warp_file(copy_a, PW, **opts)
        if not w_copy.startswith('OK'):
            raise RuntimeError(f'warp_file: {w_copy}')
        ks_copy = WORK / w_copy.split('->')[1].strip().split()[0]
        if not copy_a.exists():
            raise RuntimeError('copy_a deleted by warp_file (copy mode violated)')

        # ── In-place mode ──
        w_inp = warp_file_inplace(copy_b, PW, **opts)
        if not w_inp.startswith('OK'):
            raise RuntimeError(f'warp_file_inplace: {w_inp}')
        ks_inp = WORK / w_inp.split('->')[1].strip().split()[0]

        # ── Unwarp both ──
        u_copy = unwarp_auto(ks_copy, PW)
        if not u_copy.startswith('OK'):
            raise RuntimeError(f'unwarp copy: {u_copy}')
        out_copy = WORK / u_copy.split('->')[1].strip().split()[0]

        u_inp = unwarp_auto(ks_inp, PW)
        if not u_inp.startswith('OK'):
            raise RuntimeError(f'unwarp inplace: {u_inp}')
        out_inp = WORK / u_inp.split('->')[1].strip().split()[0]

        # ── Verify ──
        h_copy = sha256file(out_copy)
        h_inp  = sha256file(out_inp)

        if h_copy != orig_hash:
            raise RuntimeError(f'copy-mode hash mismatch: got {h_copy[:12]}')
        if h_inp != orig_hash:
            raise RuntimeError(f'inplace-mode hash mismatch: got {h_inp[:12]}')
        if h_copy != h_inp:
            raise RuntimeError('copy and inplace restored files differ!')

        return True, f'PASS  [{run_id:3d}] {src.name[:20]:20s}  {label}'
    except Exception as e:
        tb = traceback.format_exc().strip().splitlines()[-1]
        return False, f'FAIL  [{run_id:3d}] {src.name[:20]:20s}  {label}\n      {tb}'
    finally:
        cleanup()

# ── Fixed 20 option sets ──────────────────────────────────────────────────────
FIXED_OPTS = [
    # minimal
    {'kdf': KDF_SHA256, 'encrypt_tail': False, 'encrypt_middle': False,
     'encrypt_range': False},
    # tail only
    {'kdf': KDF_SHA256, 'encrypt_tail': True, 'encrypt_middle': False,
     'encrypt_range': False},
    # middle only
    {'kdf': KDF_SHA256, 'encrypt_tail': False, 'encrypt_middle': True,
     'encrypt_range': False},
    # range auto 25%
    {'kdf': KDF_SHA256, 'encrypt_tail': False, 'encrypt_middle': False,
     'encrypt_range': True, 'range_mode': 'auto', 'range_percent': 25},
    # range auto 50%
    {'kdf': KDF_SHA256, 'encrypt_tail': False, 'encrypt_middle': False,
     'encrypt_range': True, 'range_mode': 'auto', 'range_percent': 50},
    # range manual b16/c64
    {'kdf': KDF_SHA256, 'encrypt_tail': False, 'encrypt_middle': False,
     'encrypt_range': True, 'range_mode': 'manual',
     'range_b_bytes': 16, 'range_c_bytes': 64},
    # all on, sha256
    {'kdf': KDF_SHA256, 'encrypt_tail': True, 'encrypt_middle': True,
     'encrypt_range': True, 'range_mode': 'auto', 'range_percent': 25},
    # all on, scrypt
    {'kdf': KDF_SCRYPT, 'encrypt_tail': True, 'encrypt_middle': True,
     'encrypt_range': True, 'range_mode': 'auto', 'range_percent': 25},
    # scrypt, no extras
    {'kdf': KDF_SCRYPT, 'encrypt_tail': False, 'encrypt_middle': False,
     'encrypt_range': False},
    # range manual b=c (full coverage)
    {'kdf': KDF_SHA256, 'encrypt_tail': False, 'encrypt_middle': False,
     'encrypt_range': True, 'range_mode': 'manual',
     'range_b_bytes': 512, 'range_c_bytes': 512},
    # small encrypt_size
    {'kdf': KDF_SHA256, 'encrypt_size': 256, 'encrypt_tail': False,
     'encrypt_middle': False, 'encrypt_range': False},
    # large encrypt_size
    {'kdf': KDF_SHA256, 'encrypt_size': 4096, 'encrypt_tail': False,
     'encrypt_middle': False, 'encrypt_range': False},
    # encrypt_size + tail + middle
    {'kdf': KDF_SHA256, 'encrypt_size': 512, 'encrypt_tail': True,
     'encrypt_middle': True, 'encrypt_range': False},
    # range with non-zero start
    {'kdf': KDF_SHA256, 'encrypt_tail': False, 'encrypt_middle': False,
     'encrypt_range': True, 'range_mode': 'manual',
     'range_start': 1024, 'range_end': 0,
     'range_b_bytes': 16, 'range_c_bytes': 64},
    # range auto 75%
    {'kdf': KDF_SHA256, 'encrypt_tail': True, 'encrypt_middle': True,
     'encrypt_range': True, 'range_mode': 'auto', 'range_percent': 75},
    # range auto 10%
    {'kdf': KDF_SHA256, 'encrypt_tail': False, 'encrypt_middle': False,
     'encrypt_range': True, 'range_mode': 'auto', 'range_percent': 10},
    # mid + range manual
    {'kdf': KDF_SHA256, 'encrypt_tail': False, 'encrypt_middle': True,
     'encrypt_range': True, 'range_mode': 'manual',
     'range_b_bytes': 32, 'range_c_bytes': 128},
    # tail + range manual b32/c128
    {'kdf': KDF_SHA256, 'encrypt_tail': True, 'encrypt_middle': False,
     'encrypt_range': True, 'range_mode': 'manual',
     'range_b_bytes': 32, 'range_c_bytes': 128},
    # scrypt + all + range manual
    {'kdf': KDF_SCRYPT, 'encrypt_tail': True, 'encrypt_middle': True,
     'encrypt_range': True, 'range_mode': 'manual',
     'range_b_bytes': 64, 'range_c_bytes': 256},
    # tiny encrypt_size + range
    {'kdf': KDF_SHA256, 'encrypt_size': 64, 'encrypt_tail': False,
     'encrypt_middle': False,
     'encrypt_range': True, 'range_mode': 'auto', 'range_percent': 50},
]

src_hashes = {src: sha256file(src) for src in SOURCES}
PASS = FAIL = 0
results = []

for run_id, opts in enumerate(FIXED_OPTS, start=1):
    src = SOURCES[run_id % len(SOURCES)]
    ok, msg = run_equivalence(run_id, src, opts)
    results.append(msg)
    PASS += ok; FAIL += (not ok)

# ── Random 80 cases ───────────────────────────────────────────────────────────
def rand_opts(file_size):
    kdf = rng.choice([KDF_SCRYPT, KDF_SHA256])
    enc_size_max = max(256, min(8192, file_size // 4))
    encrypt_size = max(256, (rng.randint(256, enc_size_max) // 16) * 16)
    can_tail = file_size >= 2 * encrypt_size
    encrypt_tail   = rng.choice([False, True]) if can_tail else False
    encrypt_middle = rng.choice([False, True])
    encrypt_range  = rng.choice([False, True])
    opts = {'kdf': kdf, 'encrypt_size': encrypt_size,
            'encrypt_tail': encrypt_tail,
            'encrypt_middle': encrypt_middle,
            'encrypt_range': encrypt_range}
    if encrypt_range:
        mode = rng.choice(['auto', 'manual'])
        if mode == 'auto':
            opts.update({'range_mode': 'auto',
                         'range_percent': rng.choice([10, 25, 33, 50, 75])})
        else:
            rc = rng.choice([32, 64, 128, 256, 512])
            rb = max(16, (int(rc * rng.choice([0.25, 0.5, 0.75, 1.0])) // 16) * 16)
            opts.update({'range_mode': 'manual',
                         'range_b_bytes': rb, 'range_c_bytes': rc,
                         'range_start': rng.randint(0, file_size // 3),
                         'range_end':   0})
    return opts

for i in range(80):
    run_id = 21 + i
    src = rng.choice(SOURCES)
    opts = rand_opts(src.stat().st_size)
    ok, msg = run_equivalence(run_id, src, opts)
    results.append(msg)
    PASS += ok; FAIL += (not ok)

print()
for r in results: print(r)
print()
print('=' * 72)
print(f'  PASS: {PASS}   FAIL: {FAIL}   TOTAL: {PASS + FAIL}')
print('=' * 72)
try: WORK.rmdir()
except: pass
