"""
Suite 1 — Copy mode fixed combination matrix.
warp_file (creates .ks copy, source unchanged) -> unwarp_auto -> SHA-256 verify.
2 kdf × 2 tail × 2 middle × 6 range = 48 combos × 3 files = 144 runs.
"""
import sys, shutil, hashlib, traceback
from pathlib import Path

sys.path.insert(0, r'e:\Git\GitHub\Kevinhua\KVH_Projects\ESLOCK_Recover')
import importlib, KvhWarp; importlib.reload(KvhWarp)
from KvhWarp import warp_file, unwarp_auto, KDF_SCRYPT, KDF_SHA256

BINARY = Path(r'e:\Git\repository\KvhWarp\binary')
WORK   = BINARY / '_tc1'
if WORK.exists(): shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(exist_ok=True)
PW = 'TestPass!1'

SOURCES = {
    'small (~1.2MB)':  BINARY / '1684427397013.mp4',
    'medium (~3.3MB)': BINARY / '1695318021338.mp4',
    'large (~5MB)':    BINARY / '射的騷女臉上到處都是.mp4',
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
    (False, 'manual', 25,  16,  64,  'none'),
    (True,  'auto',   25,  16,  64,  'auto-25%'),
    (True,  'auto',   50,  16,  64,  'auto-50%'),
    (True,  'manual', 25,  16,  64,  'manual-b16c64'),
    (True,  'manual', 25,  32, 128,  'manual-b32c128'),
    (True,  'manual', 25, 512, 512,  'manual-b512c512(all)'),
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
results = []

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
        ks_file = out_file = None
        try:
            warp_opts = {k: v for k, v in opts.items() if not k.startswith('_')}
            w_res = warp_file(work_src, PW, **warp_opts)
            if not w_res.startswith('OK'):
                raise RuntimeError(f'warp: {w_res}')
            ks_name = w_res.split('->')[1].strip().split()[0]
            ks_file = WORK / ks_name

            # copy mode: source MUST remain intact
            if not work_src.exists():
                raise RuntimeError('source was deleted by warp_file (should be copy mode)')
            if sha256file(work_src) != orig_hash:
                raise RuntimeError('source content changed by warp_file')

            u_res = unwarp_auto(ks_file, PW)
            if not u_res.startswith('OK'):
                raise RuntimeError(f'unwarp: {u_res}')
            out_name = u_res.split('->')[1].strip().split()[0]
            out_file = WORK / out_name

            if sha256file(out_file) != orig_hash:
                raise RuntimeError('HASH MISMATCH')
            PASS += 1
            results.append(f'PASS  {header}')
        except Exception as e:
            FAIL += 1
            tb = traceback.format_exc().strip().splitlines()[-1]
            results.append(f'FAIL  {header}\n      {tb}')
        finally:
            cleanup()

print()
for r in results: print(r)
print()
print('=' * 72)
print(f'  PASS: {PASS}   FAIL: {FAIL}   TOTAL: {PASS + FAIL}')
print('=' * 72)
try: WORK.rmdir()
except: pass
