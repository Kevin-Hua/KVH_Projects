"""
Suite 4 — is_warped() exhaustive test.  100 runs.

Fixed 30 cases: plain file, copy .ks, inplace .ks, wrong pw, empty pw, no pw,
                non-existent, truncated, tiny files, extension-only heuristic.
Random 70 cases: random file/password/mode combinations.

Expected outcomes are pre-computed; any deviation is a FAIL.
"""
import sys, shutil, hashlib, random, traceback, os
from pathlib import Path

sys.path.insert(0, r'e:\Git\GitHub\Kevinhua\KVH_Projects\ESLOCK_Recover')
import importlib, KvhWarp; importlib.reload(KvhWarp)
from KvhWarp import (is_warped, warp_file, warp_file_inplace,
                     KDF_SCRYPT, KDF_SHA256)

BINARY = Path(r'e:\Git\repository\KvhWarp\binary')
WORK   = BINARY / '_tc4'
if WORK.exists(): shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(exist_ok=True)
PW     = 'IsWarpedPw!'
WRONG  = 'TotallyWrongPassword'
SEED   = 20260419
rng    = random.Random(SEED)

SRC    = BINARY / '1684427397013.mp4'   # small reference file
OPTS   = {'kdf': KDF_SHA256, 'encrypt_tail': False,
          'encrypt_middle': False, 'encrypt_range': False}

def cleanup():
    for f in WORK.iterdir():
        try: f.unlink()
        except: pass

def make_copy_ks():
    """Warp a copy of SRC and return the .ks Path."""
    src = WORK / SRC.name
    shutil.copy2(SRC, src)
    res = warp_file(src, PW, **OPTS)
    assert res.startswith('OK'), res
    ks = WORK / res.split('->')[1].strip().split()[0]
    src.unlink()
    return ks

def make_inplace_ks():
    """Warp SRC in-place and return the .ks Path."""
    src = WORK / SRC.name
    shutil.copy2(SRC, src)
    res = warp_file_inplace(src, PW, **OPTS)
    assert res.startswith('OK'), res
    ks = WORK / res.split('->')[1].strip().split()[0]
    return ks

def make_plain():
    """Return Path to a plain (non-warped) copy of SRC."""
    plain = WORK / SRC.name
    shutil.copy2(SRC, plain)
    return plain

def make_tiny(size, name='tiny.bin'):
    """Create a synthetic file of exactly `size` bytes."""
    p = WORK / name
    p.write_bytes(os.urandom(max(size, 0)) if size > 0 else b'')
    return p

# ── Fixed cases ───────────────────────────────────────────────────────────────
# Each entry: (label, callable_that_returns_path, password_arg, expected_result)
# password_arg = '' means call is_warped(path, '') or is_warped(path) with empty pw
# None means call with no password (heuristic)

FIXED = [
    # ── plain file ──
    ('plain + correct pw',          make_plain,       PW,    False),
    ('plain + wrong pw',            make_plain,       WRONG, False),
    ('plain + empty pw',            make_plain,       '',    False),
    ('plain + no pw (heuristic)',   make_plain,       None,  False),  # .mp4, not .ks
    # ── copy-mode .ks ──
    ('copy .ks + correct pw',       make_copy_ks,     PW,    True),
    ('copy .ks + wrong pw',         make_copy_ks,     WRONG, False),
    ('copy .ks + empty pw',         make_copy_ks,     '',    True),   # empty pw = no pw → extension heuristic → True
    ('copy .ks + no pw (heuristic)',make_copy_ks,     None,  True),   # .ks extension
    # ── inplace .ks ──
    ('inplace .ks + correct pw',    make_inplace_ks,  PW,    True),
    ('inplace .ks + wrong pw',      make_inplace_ks,  WRONG, False),
    ('inplace .ks + empty pw',      make_inplace_ks,  '',    True),   # empty pw = no pw → extension heuristic → True
    ('inplace .ks + no pw',         make_inplace_ks,  None,  True),   # .ks extension
    # ── tiny / edge files ──
    ('0-byte file + correct pw',    lambda: make_tiny(0),   PW,    False),
    ('1-byte file + correct pw',    lambda: make_tiny(1),   PW,    False),
    ('3-byte file + correct pw',    lambda: make_tiny(3),   PW,    False),
    ('4-byte file + correct pw',    lambda: make_tiny(4),   PW,    False),
    ('16-byte file + correct pw',   lambda: make_tiny(16),  PW,    False),
    ('0-byte .ks ext + no pw',      lambda: make_tiny(0,'z.ks'), None, True),  # heuristic
    # ── boundary passwords ──
    ('copy .ks + 1-char wrong pw',  make_copy_ks,     'X',   False),
    ('copy .ks + pw-1-char-off',    make_copy_ks,     PW[:-1], False),
    ('copy .ks + pw+extra-char',    make_copy_ks,     PW+'!', False),
    ('copy .ks + unicode pw',       make_copy_ks,     '密碼測試', False),
    ('copy .ks + very long wrong pw', make_copy_ks,   'A' * 512, False),
    ('copy .ks + whitespace pw',    make_copy_ks,     '   ', False),
    ('copy .ks + newline in pw',    make_copy_ks,     PW + '\n', False),
    # ── non-existent path ──
    ('non-existent path',           lambda: WORK / 'does_not_exist.mp4', PW, False),
    ('non-existent .ks path',       lambda: WORK / 'does_not_exist.ks',  PW, False),
    ('non-existent .ks no pw',      lambda: WORK / 'does_not_exist.ks',  None, True),  # heuristic
    # ── truncated warped file ──
    # NOTE: is_warped() only checks the 4-byte magic header, not full integrity.
    # A truncated copy .ks still has an intact header → returns True.
    ('copy .ks truncated by 1B',    None, PW, True),    # header intact; data area truncated
    ('copy .ks truncated by 8B',    None, PW, True),    # header intact; data area truncated
]

PASS = FAIL = 0
results = []
run_id = 0

for label, factory, pw_arg, expected in FIXED:
    run_id += 1
    cleanup()
    path = None
    try:
        if label.startswith('copy .ks truncated by 1B'):
            ks = make_copy_ks()
            data = ks.read_bytes()
            ks.write_bytes(data[:-1])
            path = ks
        elif label.startswith('copy .ks truncated by 8B'):
            ks = make_copy_ks()
            data = ks.read_bytes()
            ks.write_bytes(data[:-8])
            path = ks
        else:
            path = factory()

        if pw_arg is None:
            result = is_warped(path)
        else:
            result = is_warped(path, pw_arg)

        if result == expected:
            PASS += 1
            results.append(f'PASS  [{run_id:3d}] {label} → {result}')
        else:
            FAIL += 1
            results.append(f'FAIL  [{run_id:3d}] {label} → got={result} expected={expected}')
    except Exception as e:
        # is_warped should NEVER raise — it must return False on any error
        FAIL += 1
        tb = traceback.format_exc().strip().splitlines()[-1]
        results.append(f'FAIL  [{run_id:3d}] {label} RAISED EXCEPTION: {tb}')
    finally:
        cleanup()

# ── Random 70 cases ───────────────────────────────────────────────────────────
WRONG_PASSWORDS = [
    '', 'X', 'wrong', WRONG, PW[:-1], PW + '!', PW.upper(),
    '密碼', 'A' * 100, ' ' + PW, PW + ' ', '\t', '\n', '\x00' * 4,
]

for _ in range(70):
    run_id += 1
    cleanup()

    # Pick scenario
    scenario = rng.choice(['plain', 'copy_correct', 'copy_wrong',
                           'inplace_correct', 'inplace_wrong', 'tiny', 'no_pw'])
    try:
        if scenario == 'plain':
            path = make_plain()
            pw   = rng.choice([PW] + WRONG_PASSWORDS)
            expected = False
            result = is_warped(path, pw)

        elif scenario == 'copy_correct':
            path = make_copy_ks()
            expected = True
            result = is_warped(path, PW)

        elif scenario == 'copy_wrong':
            path = make_copy_ks()
            pw   = rng.choice(WRONG_PASSWORDS)
            # empty pw → extension heuristic → True; non-empty wrong pw → False
            expected = True if pw == '' else False
            result = is_warped(path, pw)

        elif scenario == 'inplace_correct':
            path = make_inplace_ks()
            expected = True
            result = is_warped(path, PW)

        elif scenario == 'inplace_wrong':
            path = make_inplace_ks()
            pw   = rng.choice(WRONG_PASSWORDS)
            # empty pw → extension heuristic → True; non-empty wrong pw → False
            expected = True if pw == '' else False
            result = is_warped(path, pw)

        elif scenario == 'tiny':
            size = rng.choice([0, 1, 2, 3, 4, 8, 15, 16, 32])
            path = make_tiny(size)
            pw   = rng.choice([PW] + WRONG_PASSWORDS)
            expected = False
            result = is_warped(path, pw)

        else:  # no_pw heuristic
            choice = rng.choice(['plain_mp4', 'copy_ks', 'inplace_ks', 'fake_ks'])
            if choice == 'plain_mp4':
                path = make_plain()
                expected = False   # .mp4 extension
            elif choice == 'copy_ks':
                path = make_copy_ks()
                expected = True    # .ks extension
            elif choice == 'inplace_ks':
                path = make_inplace_ks()
                expected = True    # .ks extension
            else:
                # rename plain file to .ks
                path = make_plain()
                fake = path.with_suffix('.ks')
                path.rename(fake)
                path = fake
                expected = True    # .ks extension → heuristic returns True
            result = is_warped(path)

        if result == expected:
            PASS += 1
            results.append(f'PASS  [{run_id:3d}] rnd:{scenario} → {result}')
        else:
            FAIL += 1
            results.append(f'FAIL  [{run_id:3d}] rnd:{scenario} → got={result} expected={expected}')
    except Exception as e:
        FAIL += 1
        tb = traceback.format_exc().strip().splitlines()[-1]
        results.append(f'FAIL  [{run_id:3d}] rnd:{scenario} RAISED: {tb}')
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
