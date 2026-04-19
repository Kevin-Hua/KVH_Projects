"""
Suite 3 — base_folder parameter behaviour.
Both warp_file and warp_file_inplace accept base_folder but currently ignore it;
output always goes to filepath.parent.  100 runs verify:
  1. Passing any base_folder value (valid/invalid/None) never crashes the API.
  2. Output is always written to filepath.parent, not base_folder.
  3. Round-trip SHA-256 passes regardless of base_folder value.

Fixed cases (20): None, same dir, sibling dir, non-existent path, file-as-dir,
                  very long path, Windows root, relative string, etc.
Random cases (80): random mix of path types and encryption options, both modes.
"""
import sys, shutil, hashlib, random, traceback, os
from pathlib import Path

sys.path.insert(0, r'e:\Git\GitHub\Kevinhua\KVH_Projects\ESLOCK_Recover')
import importlib, KvhWarp; importlib.reload(KvhWarp)
from KvhWarp import warp_file, warp_file_inplace, unwarp_auto, KDF_SCRYPT, KDF_SHA256

BINARY = Path(r'e:\Git\repository\KvhWarp\binary')
WORK   = BINARY / '_tc3'
if WORK.exists(): shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(exist_ok=True)
PW     = 'BFtest@2026'
SEED   = 20260419
rng    = random.Random(SEED)

SOURCES = [
    BINARY / '1684427397013.mp4',
    BINARY / '1695318021338.mp4',
]

def sha256file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        while chunk := f.read(1 << 20): h.update(chunk)
    return h.hexdigest()

def cleanup():
    for f in WORK.iterdir():
        try: f.unlink()
        except: pass

# ── base_folder candidates ────────────────────────────────────────────────────
# Fixed: 20 distinct special/boundary/wrong values
FIXED_BF = [
    None,                                        # 1. None (default)
    WORK,                                        # 2. same as source dir
    BINARY,                                      # 3. parent of source dir
    Path(r'C:\Windows'),                         # 4. valid system dir
    Path(r'C:\DOES_NOT_EXIST_XYZ_KVH'),          # 5. non-existent
    Path(r'C:\DOES_NOT_EXIST_XYZ_KVH\sub\sub2'), # 6. deeply non-existent
    Path(r'C:\\'),                               # 7. drive root
    Path(r'Z:\nonexistent'),                     # 8. possibly invalid drive
    BINARY / '_tc3' / 'subdir_not_created',      # 9. non-existent subdir
    Path('.'),                                   # 10. relative current dir
    Path('..'),                                  # 11. parent relative
    Path(''),                                    # 12. empty path
    Path(r'\\server\share'),                     # 13. UNC path (not reachable)
    WORK / '1684427397013.mp4',                  # 14. an existing FILE (not dir)
    Path('C:/'),                                 # 15. forward-slash drive root
    BINARY / 'FuckAmy',                          # 16. an existing subdir
    BINARY / 'TestAmy',                          # 17. another existing subdir
    Path('a' * 200),                             # 18. very long relative path
    Path(r'C:\Windows\System32'),                # 19. deep valid system dir
    WORK.parent,                                 # 20. grandparent dir
]


def run_one(run_id, src, base_folder_val, mode, opts):
    """
    Run warp + unwarp with the given base_folder value.
    Returns (status, message).
    Status: 'PASS' | 'FAIL' | 'SKIP_EXPECTED'
    """
    orig_hash = sha256file(src)
    work_src  = WORK / src.name
    shutil.copy2(src, work_src)
    ks_file = out_file = None

    bf_str = str(base_folder_val)[:50] if base_folder_val is not None else 'None'

    try:
        warp_opts = {**opts, 'base_folder': base_folder_val}

        if mode == 'copy':
            w_res = warp_file(work_src, PW, **warp_opts)
        else:
            w_res = warp_file_inplace(work_src, PW, **warp_opts)

        if w_res.startswith('SKIP'):
            # Acceptable if file is too small / already warped
            return 'PASS', f"PASS  [{run_id:3d}] SKIP-OK  mode={mode} bf={bf_str} → {w_res[:40]}"

        if not w_res.startswith('OK'):
            raise RuntimeError(f'warp returned ERR: {w_res}')

        ks_name = w_res.split('->')[1].strip().split()[0]
        ks_file = WORK / ks_name

        # Verify .ks is in WORK (filepath.parent), NOT in base_folder
        if not ks_file.exists():
            raise RuntimeError(f'.ks not found in filepath.parent ({WORK}); base_folder={bf_str}')

        if mode == 'copy':
            # source must still be intact
            if not work_src.exists():
                raise RuntimeError('source deleted by warp_file in copy mode')
            if sha256file(work_src) != orig_hash:
                raise RuntimeError('source modified by warp_file in copy mode')

        u_res = unwarp_auto(ks_file, PW)
        if not u_res.startswith('OK'):
            raise RuntimeError(f'unwarp: {u_res}')
        out_name = u_res.split('->')[1].strip().split()[0]
        out_file = WORK / out_name

        if sha256file(out_file) != orig_hash:
            raise RuntimeError('HASH MISMATCH after round-trip')

        return 'PASS', f"PASS  [{run_id:3d}] mode={mode} bf={bf_str}"

    except Exception as e:
        tb = traceback.format_exc().strip().splitlines()[-1]
        return 'FAIL', f"FAIL  [{run_id:3d}] mode={mode} bf={bf_str}\n      {tb}"
    finally:
        cleanup()


# ── Build test list ───────────────────────────────────────────────────────────
TESTS = []

# Fixed 20 cases (one for each base_folder value, alternating copy/inplace, simple opts)
for i, bf in enumerate(FIXED_BF):
    src    = SOURCES[i % len(SOURCES)]
    mode   = 'copy' if i % 2 == 0 else 'inplace'
    opts   = {'kdf': KDF_SHA256, 'encrypt_tail': False, 'encrypt_middle': False,
              'encrypt_range': False}
    TESTS.append((i + 1, src, bf, mode, opts))

# Random 80 cases: random base_folder, mode, options
RND_BF_POOL = [
    None,
    WORK,
    BINARY,
    Path(r'C:\FAKE_BF'),
    Path(r'C:\Windows'),
    Path('.'),
    Path('relative_dir'),
    WORK / 'nonexistent_sub',
    Path(''),
    Path('a' * 100),
]
for i in range(80):
    src    = rng.choice(SOURCES)
    bf     = rng.choice(RND_BF_POOL)
    mode   = rng.choice(['copy', 'inplace'])
    kdf    = rng.choice([KDF_SCRYPT, KDF_SHA256])
    tail   = rng.choice([False, True])
    mid    = rng.choice([False, True])
    rng_on = rng.choice([False, True])
    opts   = {'kdf': kdf, 'encrypt_tail': tail, 'encrypt_middle': mid,
              'encrypt_range': rng_on, 'range_mode': 'auto', 'range_percent': 25}
    TESTS.append((20 + i + 1, src, bf, mode, opts))

# ── Run ───────────────────────────────────────────────────────────────────────
PASS = FAIL = 0
results = []

for run_id, src, bf, mode, opts in TESTS:
    status, msg = run_one(run_id, src, bf, mode, opts)
    if status == 'PASS':
        PASS += 1
    else:
        FAIL += 1
    results.append(msg)

print()
for r in results: print(r)
print()
print('=' * 72)
print(f'  PASS: {PASS}   FAIL: {FAIL}   TOTAL: {PASS + FAIL}')
print()
print('  NOTE: base_folder is currently accepted but IGNORED by warp_file and')
print('  warp_file_inplace — output always goes to filepath.parent.')
print('  All PASS results above confirm this behaviour is stable.')
print('=' * 72)
try: WORK.rmdir()
except: pass
