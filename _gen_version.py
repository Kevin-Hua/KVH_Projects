#!/usr/bin/env python
"""Read _APP_VERSION and _APP_NAME from <project>/<project>.py and patch <project>/version_info.txt in-place.

Usage:
    python _gen_version.py <ProjectFolder>

Example:
    python _gen_version.py TDX_Billboard
"""
import re
import sys
from pathlib import Path

if len(sys.argv) < 2:
    sys.exit("Usage: python _gen_version.py <ProjectFolder>")

PROJECT      = sys.argv[1]
HERE         = Path(__file__).parent
PROJECT_DIR  = HERE / PROJECT
APP_PY       = PROJECT_DIR / f"{PROJECT}.py"
VERSION_INFO = PROJECT_DIR / "version_info.txt"

if not APP_PY.exists():
    sys.exit(f"ERROR: {APP_PY} not found")

src = APP_PY.read_text(encoding="utf-8")

# ── Read _APP_VERSION ──────────────────────────────────────────────────────────
m = re.search(r'^_APP_VERSION\s*=\s*["\']([^"\']+)["\']', src, re.MULTILINE)
if not m:
    sys.exit(f"ERROR: _APP_VERSION not found in {APP_PY.name}")
ver_str = m.group(1)

# ── Read _APP_NAME ─────────────────────────────────────────────────────────────
n = re.search(r'^_APP_NAME\s*=\s*["\']([^"\']+)["\']', src, re.MULTILINE)
app_name = n.group(1) if n else PROJECT

# ── Build version tuple ────────────────────────────────────────────────────────
parts = ver_str.split(".")
while len(parts) < 4:
    parts.append("0")
parts = parts[:4]
try:
    tup = tuple(int(x) for x in parts)
except ValueError:
    sys.exit(f"ERROR: non-numeric version parts in '{ver_str}'")

tup_str    = f"({tup[0]}, {tup[1]}, {tup[2]}, {tup[3]})"
ver_dotted = ".".join(str(x) for x in tup)

print(f"Project: {PROJECT}")
print(f"App    : {app_name}")
print(f"Version: {ver_str}  →  {tup_str}  /  {ver_dotted}")

# ── Patch version_info.txt (create skeleton if missing) ───────────────────────
if not VERSION_INFO.exists():
    VERSION_INFO.write_text(f'''VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(0, 0, 0, 0),
    prodvers=(0, 0, 0, 0),
    mask=0x3f, flags=0x0, OS=0x4, fileType=0x1, subtype=0x0, date=(0,0),
  ),
  kids=[
    StringFileInfo([StringTable(u\'040404b0\', [
      StringStruct(u\'CompanyName\',      u\'KVH\'),
      StringStruct(u\'FileDescription\',  u\'\'),
      StringStruct(u\'FileVersion\',      u\'0.0.0.0\'),
      StringStruct(u\'InternalName\',     u\'\'),
      StringStruct(u\'LegalCopyright\',   u\'\\u00a9 2026 KVH\'),
      StringStruct(u\'OriginalFilename\', u\'\'),
      StringStruct(u\'ProductName\',      u\'\'),
      StringStruct(u\'ProductVersion\',   u\'0.0.0.0\'),
    ])])
  , VarFileInfo([VarStruct(u\'Translation\', [0x0404, 0x04b0])])]
)
''', encoding="utf-8")
    print("  created skeleton version_info.txt")

text = VERSION_INFO.read_text(encoding="utf-8")

text = re.sub(r'filevers\s*=\s*\([^)]+\)', f"filevers={tup_str}", text)
text = re.sub(r'prodvers\s*=\s*\([^)]+\)', f"prodvers={tup_str}", text)
text = re.sub(r"(u'FileVersion',\s*u')[^']*(')",
              lambda mo: mo.group(1) + ver_dotted + mo.group(2), text)
text = re.sub(r"(u'ProductVersion',\s*u')[^']*(')",
              lambda mo: mo.group(1) + ver_dotted + mo.group(2), text)
text = re.sub(r"(u'FileDescription',\s*u')[^']*(')",
              lambda mo: mo.group(1) + app_name + mo.group(2), text)
text = re.sub(r"(u'InternalName',\s*u')[^']*(')",
              lambda mo: mo.group(1) + app_name + mo.group(2), text)
text = re.sub(r"(u'OriginalFilename',\s*u')[^']*(')",
              lambda mo: mo.group(1) + app_name + ".exe" + mo.group(2), text)
text = re.sub(r"(u'ProductName',\s*u')[^']*(')",
              lambda mo: mo.group(1) + app_name + mo.group(2), text)

VERSION_INFO.write_text(text, encoding="utf-8")
print(f"Patched: {VERSION_INFO}")
