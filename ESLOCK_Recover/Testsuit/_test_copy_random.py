"""
Suite 2 — Copy mode randomised parameters.
warp_file -> unwarp_auto -> SHA-256 verify. 100 runs.
Randomises: kdf, encrypt_size, encrypt_tail, encrypt_middle, encrypt_middle_size,
            range_start, range_end, range_mode, range_percent, range_b, range_c.
"""
import sys, shutil, hashlib, random, traceback
from pathlib import Path

sys.path.insert(0, r'e:\Git\GitHub\Kevinhua\KVH_Projects\ESLOCK_Recover')
import importlib, KvhWarp; importlib.reload(KvhWarp)
from KvhWarp import warp_file, unwarp_auto, KDF_SCRYPT, KDF_SHA256, MIN_FILE_SIZE

BINARY = Path(r'e:\Git\repository\KvhWarp\binary')
WORK   = BINARY / '_tc2'
if WORK.exists(): shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(exist_ok=True)
PW     = 'TestRnd@99'
SEED   = 20260419
RUNS   = 100

rng = random.Random(SEED)

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
        except: pass

def rand_params(file_size):
    kdf = rng.choice([KDF_SCRYPT, KDF_SHA256])
    enc_size_max = max(256, min(8192, file_size // 4))
    encrypt_size = max(256, (rng.randint(256, enc_size_max) // 16) * 16)

    can_tail = file_size >= 2 * encrypt_size
    encrypt_tail = rng.choice([False, True]) if can_tail else False

    gap_start = encrypt_size
    gap_end   = file_size - encrypt_size if encrypt_tail else file_size
    gap_avail = gap_end - gap_start
    encrypt_middle = False
    encrypt_middle_size = 1024
    if gap_avail >= 512:
        encrypt_middle = rng.choice([False, True])
        if encrypt_middle:
            mid_max = max(256, min(65536, gap_avail // 2))
            encrypt_middle_size = max(256, (rng.randint(256, mid_max) // 16) * 16)

    encrypt_range = rng.choice([False, True])
    range_start = range_end = 0
    range_mode = 'manual'; range_percent = 25; range_b_bytes = 16; range_c_bytes = 64
    if encrypt_range:
        range_start = rng.randint(0, file_size // 3)
        r_end_min = range_start + 64
        if r_end_min < file_size:
            range_end = rng.randint(r_end_min, file_size)
            range_mode = rng.choice(['auto', 'manual'])
            if range_mode == 'auto':
                range_percent = rng.choice([10, 25, 33, 50, 75])
            else:
                range_c_bytes = rng.choice([32, 64, 128, 256, 512, 1024])
                b_frac = rng.choice([0.1, 0.25, 0.5, 0.75, 1.0])
                range_b_bytes = max(16, (int(range_c_bytes * b_frac) // 16) * 16)
                range_b_bytes = max(16, min(range_b_bytes, range_c_bytes))
        else:
            encrypt_range = False

    return dict(kdf=kdf, encrypt_size=encrypt_size, encrypt_tail=encrypt_tail,
                encrypt_middle=encrypt_middle, encrypt_middle_size=encrypt_middle_size,
                encrypt_range=encrypt_range, range_start=range_start, range_end=range_end,
                range_mode=range_mode, range_percent=range_percent,
                range_b_bytes=range_b_bytes, range_c_bytes=range_c_bytes)

def param_summary(opts, file_size):
    k = KDF_NAMES[opts['kdf']]
    t = 'T' if opts['encrypt_tail'] else '-'
    m = f"M{opts['encrypt_middle_size']}" if opts['encrypt_middle'] else '-'
    enc = opts['encrypt_size']
    if opts['encrypt_range']:
        rs = opts['range_start']; re = opts['range_end']
        if opts['range_mode'] == 'auto':
            r = f"Rauto{opts['range_percent']}%({rs//1024}K-{re//1024}K)"
        else:
            r = f"Rb{opts['range_b_bytes']}c{opts['range_c_bytes']}({rs//1024}K-{re//1024}K)"
    else:
        r = 'R-'
    return f"kdf={k} enc={enc} tail={t} mid={m} {r}"

src_hashes = {src: sha256file(src) for src in SOURCES}
PASS = FAIL = 0
results = []

for run_idx in range(1, RUNS + 1):
    src = rng.choice(SOURCES)
    file_size = src.stat().st_size
    opts = rand_params(file_size)
    orig_hash = src_hashes[src]
    header = f"[{run_idx:3d}] {src.name[:24]:24s}  {param_summary(opts, file_size)}"

    work_src = WORK / src.name
    shutil.copy2(src, work_src)
    ks_file = out_file = None
    try:
        w_res = warp_file(work_src, PW, **opts)
        if not w_res.startswith('OK'):
            raise RuntimeError(f'warp: {w_res}')
        ks_name = w_res.split('->')[1].strip().split()[0]
        ks_file = WORK / ks_name

        if not work_src.exists():
            raise RuntimeError('source deleted by warp_file')
        if sha256file(work_src) != orig_hash:
            raise RuntimeError('source modified by warp_file')

        u_res = unwarp_auto(ks_file, PW)
        if not u_res.startswith('OK'):
            raise RuntimeError(f'unwarp: {u_res}')
        out_name = u_res.split('->')[1].strip().split()[0]
        out_file = WORK / out_name

        if sha256file(out_file) != orig_hash:
            raise RuntimeError(f'HASH MISMATCH')
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
print('=' * 80)
print(f'  PASS: {PASS}   FAIL: {FAIL}   TOTAL: {PASS + FAIL}')
print('=' * 80)
try: WORK.rmdir()
except: pass
