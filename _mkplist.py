"""Build the iOS Info.plist for the .ipa, with a real icon declaration.

The first build shipped only CFBundleName/Executable/Identifier and had no
CFBundleIcons block, so iOS showed the app with a blank default tile. This
generates the plist with:

  * CFBundleIcons      - primary icon + the iPhone/iPad icon file name lists
  * CFBundleIcons~ipad - the iPad-specific list
  * CFBundleIconFiles  - the legacy key older iOS reads
  * CFBundleIdentifier aligned with upstream (ai.storyteller.photocraft) instead
    of the earlier ad-hoc app.photocraft.ios

Icon names are the pixel-size names _mkicons.py writes; iOS matches them against
files in the bundle root. Only the names that actually exist are listed, so a
missing render fails loudly here rather than silently at install time.

Usage: python _mkplist.py <icon_dir> <out_plist> [version]
"""

import pathlib
import sys

ICON_DIR = pathlib.Path(sys.argv[1])
OUT = pathlib.Path(sys.argv[2])
VERSION = sys.argv[3] if len(sys.argv) > 3 else "0.5.0"

BUNDLE_ID = "ai.storyteller.photocraft"  # upstream's identifier, not an invented one
EXECUTABLE = "PhotoCraft"
MIN_OS = "16.0"

# iPhone slots: (point size, scale)
IPHONE = [(20, 2), (20, 3), (29, 2), (29, 3), (40, 2), (40, 3), (60, 2), (60, 3)]
IPAD = [(20, 1), (20, 2), (29, 1), (29, 2), (40, 1), (40, 2), (76, 1), (76, 2), (83.5, 2)]


def icon_name(points, scale):
    px = int(round(points * scale))
    return "AppIcon%dx%d" % (px, px)


def check(pairs, label):
    """Keep only slots whose PNG exists; report the ones that do not."""
    keep, missing = [], []
    for points, scale in pairs:
        n = icon_name(points, scale)
        (keep if (ICON_DIR / (n + ".png")).is_file() else missing).append((points, scale, n))
    if missing:
        print("%s: MISSING %s" % (label, ", ".join(m[2] for m in missing)))
    print("%s: %d slots present" % (label, len(keep)))
    return keep


iphone = check(IPHONE, "iphone")
ipad = check(IPAD, "ipad")

# The full set of names for CFBundleIconFiles (iOS scans the bundle root for these).
all_names = []
for points, scale in sorted(set(IPHONE + IPAD)):
    n = icon_name(points, scale)
    if (ICON_DIR / (n + ".png")).is_file() and n not in all_names:
        all_names.append(n)
# 1024 marketing icon is not a CFBundleIconFiles entry, but the name is harmless there.
if (ICON_DIR / "AppIcon1024x1024.png").is_file():
    all_names.append("AppIcon1024x1024")


def icon_entries(pairs, key_points, key_scale):
    rows = []
    for points, scale in pairs:
        rows.append(
            "        <dict><key>%s</key><string>%g</string><key>%s</key><string>%dx</string>"
            "<key>CFBundleIconFiles</key><array><string>%s</string></array></dict>"
            % (key_points, points, key_scale, scale, icon_name(points, scale))
        )
    return "\n".join(rows)


plist = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>PhotoCraft</string>
  <key>CFBundleDisplayName</key>
  <string>PhotoCraft</string>
  <key>CFBundleExecutable</key>
  <string>{executable}</string>
  <key>CFBundleIdentifier</key>
  <string>{bundle_id}</string>
  <key>CFBundleVersion</key>
  <string>{version}</string>
  <key>CFBundleShortVersionString</key>
  <string>{version}</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleSignature</key>
  <string>????</string>
  <key>CFBundleSupportedPlatforms</key>
  <array><string>iPhoneOS</string></array>
  <key>MinimumOSVersion</key>
  <string>{min_os}</string>
  <key>LSRequiresIPhoneOS</key>
  <true/>
  <key>LSApplicationCategoryType</key>
  <string>public.app-category.graphics-design</string>
  <key>UIDeviceFamily</key>
  <array><integer>1</integer><integer>2</integer></array>
  <key>UIRequiredDeviceCapabilities</key>
  <array><string>arm64</string></array>
  <key>UILaunchScreen</key>
  <dict/>
  <key>UIStatusBarHidden</key>
  <false/>
  <key>UISupportedInterfaceOrientations</key>
  <array>
    <string>UIInterfaceOrientationPortrait</string>
    <string>UIInterfaceOrientationLandscapeLeft</string>
    <string>UIInterfaceOrientationLandscapeRight</string>
  </array>
  <key>UISupportedInterfaceOrientations~ipad</key>
  <array>
    <string>UIInterfaceOrientationPortrait</string>
    <string>UIInterfaceOrientationPortraitUpsideDown</string>
    <string>UIInterfaceOrientationLandscapeLeft</string>
    <string>UIInterfaceOrientationLandscapeRight</string>
  </array>

  <!-- Saves land in the app's Documents directory (see the ios-services crate: iOS is
       sandboxed, so a save has no shared path to write to and the picker's export mode would
       have to write during its own callback). These two keys are what make that directory
       visible in the Files app, so a saved document can be moved, shared, or opened again. -->
  <key>UIFileSharingEnabled</key>
  <true/>
  <key>LSSupportsOpeningDocumentsInPlace</key>
  <true/>

  <!-- The document types the app handles, so "Open in PhotoCraft" appears for them and the
       picker can offer the app as a destination. UTI strings; the ones with no system UTI use
       their extension as a dynamic type. -->
  <key>CFBundleDocumentTypes</key>
  <array>
    <dict>
      <key>CFBundleTypeName</key>
      <string>PhotoCraft Document</string>
      <key>CFBundleTypeRole</key>
      <string>Editor</string>
      <key>LSHandlerRank</key>
      <string>Owner</string>
      <key>LSItemContentTypes</key>
      <array>
        <string>com.adobe.photoshop-image</string>
        <string>public.image</string>
        <string>public.svg-image</string>
      </array>
    </dict>
    <dict>
      <key>CFBundleTypeName</key>
      <string>Image</string>
      <key>CFBundleTypeRole</key>
      <string>Editor</string>
      <key>LSHandlerRank</key>
      <string>Alternate</string>
      <key>LSItemContentTypes</key>
      <array>
        <string>public.png</string>
        <string>public.jpeg</string>
        <string>public.tiff</string>
        <string>org.webmproject.webp</string>
        <string>com.compuserve.gif</string>
        <string>com.microsoft.bmp</string>
      </array>
    </dict>
  </array>

  <!-- App icons. iOS resolves these names against files in the bundle root. -->
  <!-- Deliberately NO CFBundleIconName here: that key points at a compiled asset
       catalog (Assets.car), which this build does not produce. Setting it would
       make iOS look for a catalog that is not in the bundle and fall back to the
       blank default icon, which is the bug this whole change fixes. -->
  <key>CFBundleIcons</key>
  <dict>
    <key>CFBundlePrimaryIcon</key>
    <dict>
      <key>CFBundleIconFiles</key>
      <array>
{icon_files}
      </array>
    </dict>
    <key>CFBundleAlternateIcons</key>
    <dict/>
  </dict>
  <key>CFBundleIcons~ipad</key>
  <dict>
    <key>CFBundlePrimaryIcon</key>
    <dict>
      <key>CFBundleIconFiles</key>
      <array>
{icon_files}
      </array>
    </dict>
    <key>CFBundleAlternateIcons</key>
    <dict/>
  </dict>
  <key>CFBundleIconFiles</key>
  <array>
{icon_files}
  </array>
</dict>
</plist>
""".format(
    executable=EXECUTABLE,
    bundle_id=BUNDLE_ID,
    version=VERSION,
    min_os=MIN_OS,
    icon_files="\n".join("      <string>%s</string>" % n for n in all_names),
)

OUT.write_text(plist, encoding="utf-8")
print("\nwrote %s (%d B)" % (OUT, OUT.stat().st_size))
print("icons declared: %s" % ", ".join(all_names))
