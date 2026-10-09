"""Generate a full iOS AppIcon set from upstream's 1024 px master.

iOS wants plain PNGs in the bundle root plus a CFBundleIcons block in Info.plist
(for an unsigned, sideloaded .ipa; a signed App Store build would use a compiled
Assets.car instead).

Why the desktop master cannot be used as-is
-------------------------------------------
Upstream's `photocraft-1024.png` is the macOS render: the artwork sits on Apple's
824/1024 body grid (a 99 px transparent border all round) and the blue tile has
its own rounded corners (rx=112 on a 512-unit viewBox, ~22%). Both are right for a
`.icns` and wrong for iOS:

  * iOS applies its own superellipse ("squircle") mask. A pre-rounded tile plus
    the system mask leaves a white sliver at each corner, because the two curves
    do not coincide.
  * iOS icons must be full bleed: the artwork's edge is the icon's edge. The
    padding makes the portrait look shrunken inside the mask.
  * iOS icons must have **no alpha channel**: App Store validation rejects it and
    a sideloaded icon with alpha can show a black or speckled fringe.

So: trim the transparent border, then paint the tile's own blue into the corner
gaps so the tile becomes a full-bleed square. The result is exactly what a
full-bleed SVG render would produce, without needing an SVG rasteriser.

Naming and compression
----------------------
Each file is named for its pixel size, because iOS resolves icons through the
`CFBundleIconFiles` name list and duplicate names would silently overwrite. Several
idiom slots share one pixel size (iPhone 40pt@2x and iPad 40pt@2x are both 80 px),
hence keying by pixels rather than by the idiom/size/scale triple.

The artwork is flat line art (one blue, one cream, antialiasing between), so a
256-colour quantisation cuts the 1024 px file from ~1.3 MB to ~0.5 MB with no
visible change.

Every number this script relies on is measured from the source and printed, so the
padding and corner assumptions stay visible instead of buried.
"""

import pathlib

from PIL import Image

SRC = pathlib.Path("photocraft/assets/app-icon/photocraft-1024.png")
OUT = pathlib.Path("assets/ios-appicon")

# Every distinct pixel size iOS asks for. (pixel_size, which slots use it)
SPECS = [
    (20, "iPad 20pt @1x"),
    (29, "iPad 29pt @1x, iPhone Settings @1x"),
    (40, "iPad 20pt @2x, iPhone 20pt @2x, iPad 40pt @1x"),
    (58, "iPhone 29pt @2x"),
    (60, "iPhone 20pt @3x"),
    (76, "iPad 76pt @1x"),
    (80, "iPhone 40pt @2x, iPad 40pt @2x"),
    (87, "iPhone 29pt @3x"),
    (120, "iPhone 40pt @3x, iPhone 60pt @2x"),
    (152, "iPad 76pt @2x"),
    (167, "iPad 83.5pt @2x"),
    (180, "iPhone 60pt @3x"),
    (1024, "App Store / marketing"),
]

src = Image.open(SRC).convert("RGBA")
print("source: %s  %dx%d  %s" % (SRC, *src.size, src.mode))

# ---- 1. locate the artwork via the alpha channel --------------------------
bbox = src.getchannel("A").getbbox()
l, t, r, b = bbox
print("alpha bbox: %s  (margins l=%d t=%d r=%d b=%d)" % (bbox, l, t, src.width - r, src.height - b))

# ---- 2. trim the macOS padding, square it up ------------------------------
trimmed = src.crop(bbox)
w, h = trimmed.size
side = max(w, h)
square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
square.paste(trimmed, ((side - w) // 2, (side - h) // 2))
print("trimmed %dx%d -> %dx%d" % (w, h, side, side))

# ---- 3. find the tile's fill colour (just inside the left edge, mid-height)
tile = square.getpixel((side // 50, side // 2))
print("tile fill colour sampled mid-left: %s" % (tile,))
tile_rgb = tile[:3]

# ---- 4. fill the rounded-corner gaps so the tile is full bleed ------------
# Walk each corner and paint tile colour into everything transparent. The rounded
# corner is a quarter-disc of radius ~22% of the side, so this closes exactly the
# gap between the tile's rounding and a square - which is what iOS then masks.
px = square.load()
filled = 0
for y in range(side):
    for x in range(side):
        if px[x, y][3] == 0:
            px[x, y] = (tile_rgb[0], tile_rgb[1], tile_rgb[2], 255)
            filled += 1
print("corner fill: repainted %d transparent px (%.2f%% of the tile)" % (filled, 100.0 * filled / (side * side)))

# ---- 5. flatten onto opaque white (belt and braces; iOS icons must have no alpha)
flat = Image.new("RGB", square.size, (255, 255, 255))
flat.paste(square, mask=square.getchannel("A"))

OUT.mkdir(parents=True, exist_ok=True)
for old in OUT.glob("*.png"):
    old.unlink()

written = []
for pixels, used_by in SPECS:
    name = "AppIcon%dx%d.png" % (pixels, pixels)
    img = flat.resize((pixels, pixels), Image.LANCZOS)
    # Quantise: flat artwork, so 256 colours is lossless to the eye and much smaller.
    img = img.quantize(colors=256, method=Image.MEDIANCUT)
    dest = OUT / name
    img.save(dest, "PNG", optimize=True)
    written.append((name, pixels, dest.stat().st_size, used_by))

print("\nwrote %d icons to %s" % (len(written), OUT))
for name, pixels, nbytes, used_by in written:
    print("  %-22s %4dpx %7d B   %s" % (name, pixels, nbytes, used_by))
print("\ntotal %d B" % sum(w[2] for w in written))
