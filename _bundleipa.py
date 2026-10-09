"""Rebuild a .ipa in place: swap in the new Info.plist and add the icon PNGs.

Used to verify the bundle layout locally before committing to a 12-minute CI run,
and by the workflow as the bundling step.

Usage:
    python _bundleipa.py <binary> <icon_dir> <out_ipa> [version]

Layout produced:
    Payload/PhotoCraft.app/PhotoCraft          the arm64 Mach-O
    Payload/PhotoCraft.app/Info.plist          with CFBundleIcons
    Payload/PhotoCraft.app/AppIcon*.png        the 13 icon renders
"""

import pathlib
import plistlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

BIN = pathlib.Path(sys.argv[1])
ICONS = pathlib.Path(sys.argv[2])
OUT = pathlib.Path(sys.argv[3])
VERSION = sys.argv[4] if len(sys.argv) > 4 else "0.5.0"

HERE = pathlib.Path(__file__).resolve().parent
MKPLIST = HERE / "_mkplist.py"
PYTHON = sys.executable

if not BIN.is_file():
    print("no binary at %s" % BIN)
    raise SystemExit(1)
if not ICONS.is_dir():
    print("no icon dir at %s" % ICONS)
    raise SystemExit(1)

tmp = pathlib.Path(tempfile.mkdtemp(prefix="photocraft-bundle-"))
app = tmp / "Payload" / "PhotoCraft.app"
app.mkdir(parents=True)

# --- executable ------------------------------------------------------------
shutil.copy2(BIN, app / "PhotoCraft")
(app / "PhotoCraft").chmod(0o755)

# --- Info.plist ------------------------------------------------------------
plist_path = tmp / "Info.plist"
subprocess.run(
    [PYTHON, str(MKPLIST), str(ICONS), str(plist_path), VERSION],
    check=True,
)

# plistlib round-trip: proves the file parses and normalises it to the binary-ish
# XML iOS expects (and keeps the encoding sane).
data = plistlib.loads(plist_path.read_bytes())
plist_path.write_bytes(plistlib.dumps(data, sort_keys=False))
shutil.copy2(plist_path, app / "Info.plist")
print("Info.plist: %d B, %d keys" % ((app / "Info.plist").stat().st_size, len(data)))

# --- icons -----------------------------------------------------------------
icons = sorted(ICONS.glob("AppIcon*.png"))
for p in icons:
    shutil.copy2(p, app / p.name)
print("icons copied: %d (%d B)" % (len(icons), sum(p.stat().st_size for p in icons)))

# Cross-check: every name in CFBundleIcons must exist in the bundle root. A typo
# here is exactly what produces a blank icon at install time.
declared = set(data["CFBundleIcons"]["CFBundlePrimaryIcon"]["CFBundleIconFiles"])
present = {p.stem for p in icons}
missing = sorted(declared - present)
if missing:
    print("ERROR: declared but not bundled: %s" % ", ".join(missing))
    raise SystemExit(1)
print("all %d declared icon names are present" % len(declared))

# --- zip -------------------------------------------------------------------
if OUT.exists():
    OUT.unlink()
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(app.rglob("*")):
        if p.is_file():
            z.write(p, str(p.relative_to(tmp)).replace("\\", "/"))
    # Directory entries, so the archive looks like Apple's own.
    z.writestr("Payload/", b"")
    z.writestr("Payload/PhotoCraft.app/", b"")

print("\nwrote %s (%d B)" % (OUT, OUT.stat().st_size))
with zipfile.ZipFile(OUT) as z:
    for n in z.namelist():
        print("  %-38s %10d" % (n, z.getinfo(n).file_size))

shutil.rmtree(tmp, ignore_errors=True)
