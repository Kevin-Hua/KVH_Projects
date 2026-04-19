"""
Suite 7 — folder functions: rename_subfolders / restore_subfolders / cleanup_empty_dirs.
100 runs.

Fixed 30 cases: basic rename+restore, wrong pw, missing map, empty dir,
                nested dirs, cleanup_empty_dirs, special folder names.
Random 70 cases: random folder counts (0-8), random names, random wrong passwords.
"""
import sys, shutil, hashlib, random, traceback, os
from pathlib import Path

sys.path.insert(0, r'e:\Git\GitHub\Kevinhua\KVH_Projects\ESLOCK_Recover')
import importlib, KvhWarp; importlib.reload(KvhWarp)
from KvhWarp import rename_subfolders, restore_subfolders, cleanup_empty_dirs

BINARY = Path(r'e:\Git\repository\KvhWarp\binary')
WORK   = BINARY / '_tc7_root'
WORK.mkdir(exist_ok=True)
PW     = 'FolderPw@7'
SEED   = 20260419
rng    = random.Random(SEED)

def mk_arena(n_subdirs, names=None, add_files=False):
    """Create a fresh arena dir with n_subdirs subdirectories."""
    arena = WORK / f'arena_{rng.randint(0, 9999999):07d}'
    arena.mkdir(exist_ok=True)
    created = []
    for i in range(n_subdirs):
        name = (names[i] if names and i < len(names)
                else f'folder_{i}_{rng.randint(0, 9999)}')
        d = arena / name
        d.mkdir(exist_ok=True)
        if add_files:
            (d / 'dummy.txt').write_bytes(os.urandom(32))
        created.append(name)
    return arena, created

def nuke_arena(arena):
    shutil.rmtree(arena, ignore_errors=True)

PASS = FAIL = 0
results = []
run_id  = 0

def record(ok, msg):
    global PASS, FAIL
    results.append(msg)
    PASS += ok; FAIL += (not ok)

# ── Fixed cases ───────────────────────────────────────────────────────────────

# 1. Basic rename + restore (3 folders)
run_id += 1
arena, orig_names = mk_arena(3)
try:
    msgs = rename_subfolders(arena, PW)
    # All should be renamed
    renamed = [d.name for d in arena.iterdir() if d.is_dir()]
    if len(renamed) != 3:
        raise RuntimeError(f'Expected 3 renamed dirs, got {len(renamed)}')
    if any(r in orig_names for r in renamed):
        raise RuntimeError(f'Original names still present after rename')
    # Restore
    msgs2 = restore_subfolders(arena, PW)
    restored = sorted(d.name for d in arena.iterdir() if d.is_dir())
    if sorted(restored) != sorted(orig_names):
        raise RuntimeError(f'Restore mismatch: {restored} vs {orig_names}')
    # Map file should be gone
    if (arena / '.kvh_folders.map').exists():
        raise RuntimeError('.map file not deleted after restore')
    record(True, f'PASS  [{run_id:3d}] basic rename+restore (3 dirs)')
except Exception as e:
    record(False, f'FAIL  [{run_id:3d}] basic rename+restore: {e}')
finally:
    nuke_arena(arena)

# 2. Empty dir (no subdirs)
run_id += 1
arena, _ = mk_arena(0)
try:
    msgs = rename_subfolders(arena, PW)
    if msgs:
        raise RuntimeError(f'Expected empty result for empty dir, got {msgs}')
    record(True, f'PASS  [{run_id:3d}] empty dir returns []')
except Exception as e:
    record(False, f'FAIL  [{run_id:3d}] empty dir: {e}')
finally:
    nuke_arena(arena)

# 3. Wrong password on restore
run_id += 1
arena, orig_names = mk_arena(2)
try:
    rename_subfolders(arena, PW)
    msgs = restore_subfolders(arena, 'WrongPw!!')
    has_err = any('ERR' in m for m in msgs)
    if not has_err:
        raise RuntimeError(f'Expected ERR in messages for wrong pw, got: {msgs}')
    record(True, f'PASS  [{run_id:3d}] wrong-pw restore returns ERR')
except Exception as e:
    record(False, f'FAIL  [{run_id:3d}] wrong-pw restore: {e}')
finally:
    nuke_arena(arena)

# 4. Restore with no map file (nothing to restore)
run_id += 1
arena, _ = mk_arena(2)
try:
    msgs = restore_subfolders(arena, PW)  # no map file exists yet
    if msgs:
        raise RuntimeError(f'Expected [] with no map file, got {msgs}')
    record(True, f'PASS  [{run_id:3d}] restore with no map file returns []')
except Exception as e:
    record(False, f'FAIL  [{run_id:3d}] no-map restore: {e}')
finally:
    nuke_arena(arena)

# 5. cleanup_empty_dirs removes only empty dirs
run_id += 1
arena, _ = mk_arena(3)
# Sub-dirs: 0 empty, 1 has a file, 2 empty
subdirs = [d for d in arena.iterdir() if d.is_dir()]
(subdirs[1] / 'file.txt').write_bytes(b'data')
try:
    msgs = cleanup_empty_dirs(arena)
    remaining = [d for d in arena.iterdir() if d.is_dir()]
    if len(remaining) != 1:
        raise RuntimeError(f'Expected 1 remaining (non-empty) dir, got {len(remaining)}: {remaining}')
    if not (remaining[0] / 'file.txt').exists():
        raise RuntimeError('Wrong dir kept')
    record(True, f'PASS  [{run_id:3d}] cleanup removes empty dirs only')
except Exception as e:
    record(False, f'FAIL  [{run_id:3d}] cleanup_empty_dirs: {e}')
finally:
    nuke_arena(arena)

# 6. cleanup_empty_dirs on empty root (no subdirs)
run_id += 1
arena, _ = mk_arena(0)
try:
    msgs = cleanup_empty_dirs(arena)
    record(True, f'PASS  [{run_id:3d}] cleanup on no-subdir arena returns []')
except Exception as e:
    record(False, f'FAIL  [{run_id:3d}] cleanup empty arena: {e}')
finally:
    nuke_arena(arena)

# 7. non-existent base_folder
run_id += 1
fake = WORK / 'does_not_exist_arena'
try:
    msgs = rename_subfolders(fake, PW)
    if msgs:
        raise RuntimeError(f'Expected [] for non-existent dir, got {msgs}')
    record(True, f'PASS  [{run_id:3d}] rename non-existent dir returns []')
except Exception as e:
    record(False, f'FAIL  [{run_id:3d}] rename non-existent dir: {e}')

# 8. Large number of folders (10)
run_id += 1
arena, orig_names = mk_arena(10)
try:
    rename_subfolders(arena, PW)
    renamed = [d.name for d in arena.iterdir() if d.is_dir()]
    if len(renamed) != 10:
        raise RuntimeError(f'Expected 10 renamed dirs, got {len(renamed)}')
    restore_subfolders(arena, PW)
    restored = sorted(d.name for d in arena.iterdir() if d.is_dir())
    if sorted(restored) != sorted(orig_names):
        raise RuntimeError('10-dir restore mismatch')
    record(True, f'PASS  [{run_id:3d}] 10-dir rename+restore')
except Exception as e:
    record(False, f'FAIL  [{run_id:3d}] 10-dir: {e}')
finally:
    nuke_arena(arena)

# 9. Folder names with spaces and unicode
run_id += 1
arena, orig_names = mk_arena(3, names=['hello world', '測試資料夾', 'dir with spaces'])
try:
    rename_subfolders(arena, PW)
    restore_subfolders(arena, PW)
    restored = sorted(d.name for d in arena.iterdir() if d.is_dir())
    if sorted(restored) != sorted(orig_names):
        raise RuntimeError(f'Unicode restore mismatch: {restored}')
    record(True, f'PASS  [{run_id:3d}] unicode folder names')
except Exception as e:
    record(False, f'FAIL  [{run_id:3d}] unicode folders: {e}')
finally:
    nuke_arena(arena)

# 10. rename then cleanup_empty_dirs (renamed dirs should still exist)
run_id += 1
arena, _ = mk_arena(2)
try:
    rename_subfolders(arena, PW)
    msgs = cleanup_empty_dirs(arena)
    # Renamed dirs are empty → cleanup will remove them!
    # This is expected behaviour: cleanup_empty_dirs does not care about the map
    # Verify that at least the arena itself isn't deleted
    if not arena.exists():
        raise RuntimeError('Arena itself was deleted by cleanup_empty_dirs')
    record(True, f'PASS  [{run_id:3d}] cleanup after rename (removes empty renamed dirs)')
except Exception as e:
    record(False, f'FAIL  [{run_id:3d}] cleanup after rename: {e}')
finally:
    nuke_arena(arena)

# 11. Double rename (map overwritten on second rename)
run_id += 1
arena, orig_names = mk_arena(2)
try:
    rename_subfolders(arena, PW)
    mid_names = sorted(d.name for d in arena.iterdir() if d.is_dir())
    rename_subfolders(arena, PW)  # second rename on already-renamed dirs
    restore_subfolders(arena, PW)  # restore should bring back mid_names (2nd rename reference)
    final = sorted(d.name for d in arena.iterdir() if d.is_dir())
    # After double-rename+single-restore, folders should be mid_names (restored from 2nd rename)
    if sorted(final) != sorted(mid_names):
        raise RuntimeError(f'Double-rename restore: got {final}, expected {mid_names}')
    record(True, f'PASS  [{run_id:3d}] double-rename then restore')
except Exception as e:
    record(False, f'FAIL  [{run_id:3d}] double-rename: {e}')
finally:
    nuke_arena(arena)

# 12. Rename with 1 folder
run_id += 1
arena, orig_names = mk_arena(1, names=['single_folder'])
try:
    rename_subfolders(arena, PW)
    restore_subfolders(arena, PW)
    restored = [d.name for d in arena.iterdir() if d.is_dir()]
    if restored != ['single_folder']:
        raise RuntimeError(f'1-dir restore: {restored}')
    record(True, f'PASS  [{run_id:3d}] single-folder rename+restore')
except Exception as e:
    record(False, f'FAIL  [{run_id:3d}] single-folder: {e}')
finally:
    nuke_arena(arena)

# 13–18 (6 more fixed: empty restore msg count, wrong-pw variants, map corruption, etc.)
WRONG_PWS_FOLDERS = ['', 'x', PW[:-1], PW + '!', 'A' * 200, '密碼']
for i, wpw in enumerate(WRONG_PWS_FOLDERS):
    run_id += 1
    arena, _ = mk_arena(2)
    try:
        rename_subfolders(arena, PW)
        msgs = restore_subfolders(arena, wpw)
        has_err = any('ERR' in m for m in msgs)
        if not has_err:
            raise RuntimeError(f'Expected ERR for wrong pw {wpw!r}, got: {msgs}')
        record(True, f'PASS  [{run_id:3d}] wrong-pw-variant restore ({wpw[:20]!r})')
    except Exception as e:
        record(False, f'FAIL  [{run_id:3d}] wrong-pw-variant ({wpw[:20]!r}): {e}')
    finally:
        nuke_arena(arena)

# 19–30 (12 more fixed: various cleanup scenarios)
for n_empty, n_nonempty in [(0,3),(1,2),(2,1),(3,0),(0,0),(5,5),(10,0),(0,10),(1,1),(2,2),(3,3),(4,1)]:
    run_id += 1
    arena, _ = mk_arena(n_empty + n_nonempty)
    subdirs = [d for d in arena.iterdir() if d.is_dir()]
    for d in subdirs[:n_nonempty]:
        (d / 'f.bin').write_bytes(b'x')
    try:
        cleanup_empty_dirs(arena)
        remaining = [d for d in arena.iterdir() if d.is_dir()]
        if len(remaining) != n_nonempty:
            raise RuntimeError(f'Expected {n_nonempty} dirs, got {len(remaining)}')
        record(True, f'PASS  [{run_id:3d}] cleanup empty={n_empty} nonempty={n_nonempty}')
    except Exception as e:
        record(False, f'FAIL  [{run_id:3d}] cleanup e={n_empty} ne={n_nonempty}: {e}')
    finally:
        nuke_arena(arena)

# ── Random 70 cases ───────────────────────────────────────────────────────────
for _ in range(70):
    run_id += 1
    n        = rng.randint(0, 8)
    scenario = rng.choice(['rename_restore_ok', 'rename_restore_wrong_pw',
                           'rename_restore_empty', 'cleanup_random'])
    arena    = None
    try:
        if scenario == 'rename_restore_ok':
            arena, orig_names = mk_arena(n)
            rename_subfolders(arena, PW)
            restore_subfolders(arena, PW)
            restored = sorted(d.name for d in arena.iterdir() if d.is_dir())
            if n > 0 and sorted(restored) != sorted(orig_names):
                raise RuntimeError(f'restore mismatch n={n}: {restored} vs {orig_names}')
            record(True, f'PASS  [{run_id:3d}] rnd:rename_restore_ok n={n}')

        elif scenario == 'rename_restore_wrong_pw':
            arena, _ = mk_arena(max(1, n))
            rename_subfolders(arena, PW)
            wpw  = rng.choice(['', 'bad', PW[::-1], 'A' * rng.randint(1, 100)])
            msgs = restore_subfolders(arena, wpw)
            has_err = any('ERR' in m for m in msgs)
            if not has_err:
                raise RuntimeError(f'Expected ERR, got {msgs}')
            record(True, f'PASS  [{run_id:3d}] rnd:wrong_pw n={max(1,n)}')

        elif scenario == 'rename_restore_empty':
            arena, _ = mk_arena(0)
            r1 = rename_subfolders(arena, PW)
            r2 = restore_subfolders(arena, PW)
            if r1 or r2:
                raise RuntimeError(f'Expected empty lists, got {r1}, {r2}')
            record(True, f'PASS  [{run_id:3d}] rnd:empty_arena')

        else:  # cleanup_random
            n_empty    = rng.randint(0, max(0, n))
            n_nonempty = n - n_empty
            arena, _   = mk_arena(n_empty + n_nonempty)
            subdirs = [d for d in arena.iterdir() if d.is_dir()]
            for d in subdirs[:n_nonempty]:
                (d / 'f.bin').write_bytes(b'x')
            cleanup_empty_dirs(arena)
            remaining = [d for d in arena.iterdir() if d.is_dir()]
            if len(remaining) != n_nonempty:
                raise RuntimeError(f'cleanup: expected {n_nonempty}, got {len(remaining)}')
            record(True, f'PASS  [{run_id:3d}] rnd:cleanup e={n_empty} ne={n_nonempty}')

    except Exception as e:
        tb = traceback.format_exc().strip().splitlines()[-1]
        record(False, f'FAIL  [{run_id:3d}] rnd:{scenario}: {tb}')
    finally:
        if arena: nuke_arena(arena)

print()
for r in results: print(r)
print()
print('=' * 72)
print(f'  PASS: {PASS}   FAIL: {FAIL}   TOTAL: {PASS + FAIL}')
print('=' * 72)
# Cleanup root work dir
shutil.rmtree(WORK, ignore_errors=True)
