#!/usr/bin/env python3
"""Probe C4: drop the desktop-only services so the iOS target can build.

The app crate reaches for three crates that have no iOS implementation at all:

  rfd      - native file open/save dialogs (AppKit / GTK / Win32)
  arboard  - system clipboard
  open     - hand a URL to the desktop shell

All three are used from apps/photocraft/src/services.rs and nowhere else, and
each one feeds an `Option<Box<dyn Fn..>>` field of photocraft_ui_egui::Services
that falls back through `..Default::default()`. So on iOS we drop the dependency
*and* the field: the UI then sees "no file dialog, no clipboard", which is
exactly what a first port looks like. Nothing else in the app is touched.

Run from the checkout root:  python3 patch-ios.py
"""

import pathlib
import sys

CARGO = pathlib.Path("apps/photocraft/Cargo.toml")
SRV = pathlib.Path("apps/photocraft/src/services.rs")
GATE = '#[cfg(not(target_os = "ios"))]\n'

edits = 0


def sub(text, old, new, where):
    """Replace `old` with `new`, once, and count it."""
    global edits
    if old not in text:
        print("MISS (%s): %r" % (where, old[:70]))
        return text
    edits += 1
    return text.replace(old, new, 1)


def gate(text, needle, where):
    """Put an iOS cfg gate in front of `needle` (indentation comes with it)."""
    assert "\n" not in needle, needle
    return sub(text, needle, GATE + needle, where)


# ---------------------------------------------------------------- Cargo.toml
toml = CARGO.read_text()

# 1. eframe: the wayland/x11 features pull Linux-only socket code (wayland-backend).
toml = sub(
    toml,
    'features = ["default_fonts", "wgpu", "accesskit", "wayland", "x11", "persistence"]',
    'features = ["default_fonts", "wgpu", "accesskit", "persistence"]',
    "eframe features",
)

# 2. rfd / arboard / open: desktop-only, keep them off iOS. `ron` stays, it is
#    portable and reads the eframe window layout.
toml = sub(
    toml,
    '''[target.'cfg(not(target_arch = "wasm32"))'.dependencies]
rfd = { workspace = true }
arboard = { version = "3.6", features = ["wayland-data-control"] }
open = "5"
''',
    '''[target.'cfg(all(not(target_arch = "wasm32"), not(target_os = "ios")))'.dependencies]
rfd = { workspace = true }
arboard = { version = "3.6" }
open = "5"

[target.'cfg(not(target_arch = "wasm32"))'.dependencies]
''',
    "rfd/arboard/open target gate",
)

# 3. photocraft-tablet: src/tablet.rs is already cfg(any(macos, linux, test)),
#    so the crate itself is dead weight on iOS.
toml = sub(
    toml,
    """# Pen tablet pressure/tilt on macOS and X11 (the workspace's one isolated unsafe helper crate).
photocraft-tablet = { workspace = true }
""",
    "",
    "photocraft-tablet",
)
toml = toml.rstrip("\n") + """

# Pen tablet pressure/tilt on macOS and X11. iOS is not included: src/tablet.rs is
# already gated on cfg(any(target_os = "macos", target_os = "linux", test)).
[target.'cfg(any(target_os = "macos", target_os = "linux", target_os = "windows"))'.dependencies]
photocraft-tablet = { workspace = true }
"""

CARGO.write_text(toml)
print("patched", CARGO)

# --------------------------------------------------------------- services.rs
s = SRV.read_text()

# File dialog helpers (rfd).
s = gate(s, "fn show_file_dialog(request: FileDialogRequest", "show_file_dialog")
s = gate(s, "fn path_of(file: &rfd::FileHandle) -> String {", "path_of")
s = gate(s, "fn block_on<T>(future: impl Future<Output = T>) -> T {", "block_on")
# Clipboard paste of copied *files* (only reachable through arboard).
s = gate(s, "fn image_from_files(paths: &[PathBuf]) -> Option<(u32, u32, Vec<u8>)> {", "image_from_files")

# The arboard clipboard handle itself.
s = gate(s, "    let clip: Rc<RefCell<Option<arboard::Clipboard>>> = Rc::default();", "clip handle")

# Services fields. A cfg attribute on a struct-expression field is legal Rust, and
# every one of these falls back to Default::default() when it is compiled out.
s = gate(s, "        file_dialog: Some(Box::new(show_file_dialog)),", "field file_dialog")
s = gate(
    s,
    '        open_url: Some(Box::new(|url: &str| open::that(url).map_err(|e| e.to_string()))),',
    "field open_url",
)
s = gate(s, "        clipboard_set_image: Some({", "field clipboard_set_image")
s = gate(s, "        clipboard_get_image: Some({", "field clipboard_get_image")

SRV.write_text(s)
print("patched", SRV)
print("edits applied:", edits)
if edits < 9:
    print("WARNING: fewer edits than expected - the upstream source may have moved.")
