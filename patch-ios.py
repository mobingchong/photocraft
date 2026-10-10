#!/usr/bin/env python3
"""Port PhotoCraft to aarch64-apple-ios with every feature the desktop build has.

This is the single patch the CI applies after cloning upstream. It has three jobs.

1. Desktop-only dependencies
   --------------------------
   `eframe`'s `wayland`/`x11` features pull Linux socket code that does not build for iOS.
   `photocraft-tablet` (macOS/X11 pen input) is likewise not iOS's. Both are dropped from the
   iOS dependency set. Note what is *not* dropped: the app crate keeps `rfd`, `arboard` and
   `open` out of the iOS build too, but only because `ios_services.rs` supplies UIKit
   equivalents for all three (job 2) - the features they power stay.

2. Platform services
   -------------------
   `apps/photocraft/src/services.rs` gains an iOS `native()` beside the desktop one. The two
   build the same `Services` struct field for field; only the four platform-backed fields differ:

       field                  desktop                 iOS
       ---------------------- ----------------------- -------------------------------
       file_dialog            rfd                     UIDocumentPickerViewController
       clipboard_set/get      arboard                 UIPasteboard
       open_url               open::that              -[UIApplication openURL:]

   The Objective-C lives in a new `crates/ios-services`, which is the second isolated
   `unsafe` helper crate (after `photocraft-tablet`). The workspace sets
   `unsafe_code = "forbid"` in `[workspace.lints.rust]`, and `forbid` cannot be overridden by
   an inner `allow` — so Objective-C has to live in a crate that sets its own lint level.
   `crates/ios-services/src/uikit.rs` is the only file that allows `unsafe`.

   The desktop helpers those fields pull in (`show_file_dialog`, `path_of`, `block_on`,
   `image_from_files`, the `arboard` handle) are gated behind `cfg(not(target_os = "ios"))`.

3. Bundled fonts
   ----------------
   Handled by patch-fonts.py, which the CI runs next.

Run from the checkout root:  python3 patch-ios.py
"""

import pathlib
import sys

CARGO = pathlib.Path("apps/photocraft/Cargo.toml")
MAIN = pathlib.Path("apps/photocraft/src/main.rs")
SRV = pathlib.Path("apps/photocraft/src/services.rs")
WORKSPACE = pathlib.Path("Cargo.toml")
IOS_CRATE = pathlib.Path("crates/ios-services")

DESKTOP_GATE = '#[cfg(not(target_os = "ios"))]\n'

edits = 0


def sub(text, old, new, where):
    """Replace `old` with `new`, once, and count it. A missing anchor is reported, not fatal:
    a re-run on an already-patched tree must not corrupt anything."""
    global edits
    if old not in text:
        print("  MISS (%s)" % where)
        return text
    edits += 1
    return text.replace(old, new, 1)


def gate(text, needle, where):
    """Prefix `needle` with the iOS exclusion, unless it already carries one.

    The gate goes immediately before `needle`, which for a whole function means before its `fn`
    line (a cfg attribute cannot sit inside a function body). For an inner statement it means
    before that statement - also legal, and what the `let clip` case needs.
    """
    assert "\n" not in needle, needle
    line = text.find(needle)
    if line < 0:
        print("  MISS (%s)" % where)
        return text
    # Already gated? Look at the line before.
    start = text.rfind("\n", 0, line)
    prev_start = text.rfind("\n", 0, start) + 1
    if text[prev_start:start].strip() == '#[cfg(not(target_os = "ios"))]':
        return text
    return sub(text, needle, DESKTOP_GATE + needle, where)


# ============================================================== 1. Cargo.toml
toml = CARGO.read_text()

toml = sub(
    toml,
    'features = ["default_fonts", "wgpu", "accesskit", "wayland", "x11", "persistence"]',
    'features = ["default_fonts", "wgpu", "accesskit", "persistence"]',
    "eframe: drop wayland/x11",
)

# rfd / arboard / open: desktop implementations, kept out of the iOS build. `ron` stays (it reads
# eframe's window layout and is portable).
toml = sub(
    toml,
    """[target.'cfg(not(target_arch = "wasm32"))'.dependencies]
rfd = { workspace = true }
arboard = { version = "3.6", features = ["wayland-data-control"] }
open = "5"
""",
    """[target.'cfg(all(not(target_arch = "wasm32"), not(target_os = "ios")))'.dependencies]
rfd = { workspace = true }
arboard = { version = "3.6" }
open = "5"

[target.'cfg(not(target_arch = "wasm32"))'.dependencies]
""",
    "rfd/arboard/open: keep off iOS",
)

# photocraft-tablet: src/tablet.rs is already cfg(any(macos, linux, test)), so the crate is dead
# weight on iOS. It starts in the shared `[dependencies]` table, which is compiled on every
# platform, so it moves to a target-specific table rather than being removed.
TABLET_BLOCK = """# Pen tablet pressure/tilt on macOS and X11. iOS is excluded: src/tablet.rs is already gated on
# cfg(any(target_os = "macos", target_os = "linux", test)).
[target.'cfg(any(target_os = "macos", target_os = "linux", target_os = "windows"))'.dependencies]
photocraft-tablet = { workspace = true }
"""
toml = sub(
    toml,
    """# Pen tablet pressure/tilt on macOS and X11 (the workspace's one isolated unsafe helper crate).
photocraft-tablet = { workspace = true }
""",
    "",
    "photocraft-tablet: drop from the shared table",
)
if "cfg(any(target_os = \"macos\", target_os = \"linux\", target_os = \"windows\"))" not in toml:
    toml = toml.rstrip("\n") + "\n\n" + TABLET_BLOCK
    edits += 1

# The iOS platform services live in their own crate (see section 2 below); the app just depends
# on it. Compiling it on every target is fine - it is `#[cfg(target_os = "ios")]` inside.
if "photocraft-ios-services" not in toml:
    toml = sub(
        toml,
        "photocraft-automation = { workspace = true }",
        "photocraft-automation = { workspace = true }\n"
        "# The iOS platform services (document picker, pasteboard, URL opener). An empty crate\n"
        "# everywhere but iOS, so this dependency costs nothing on the desktop targets.\n"
        "photocraft-ios-services = { workspace = true }",
        "add photocraft-ios-services dependency",
    )

# The macOS block pins objc2 itself; no iOS-specific objc2 deps are needed in the app crate.

CARGO.write_text(toml)
print("patched", CARGO)

# ====================================================== 2. crates/ios-services
# The crate is copied in from this repository's build inputs (probe/ios-services/), the same way
# craft-fonts is. It carries its own manifest, lib.rs and uikit.rs.
if not (IOS_CRATE / "Cargo.toml").is_file():
    print("MISS: crates/ios-services/ was not copied into the checkout")
    sys.exit(1)

# The workspace must know about it and expose it as a dependency, or `{ workspace = true }` in
# the app's manifest has nothing to resolve to.
ws = WORKSPACE.read_text()
if "photocraft-ios-services" not in ws:
    ws = sub(
        ws,
        "photocraft-automation = { path = \"crates/automation\" }",
        "photocraft-automation = { path = \"crates/automation\" }\n"
        "photocraft-ios-services = { path = \"crates/ios-services\" }",
        "workspace dependency: photocraft-ios-services",
    )
    WORKSPACE.write_text(ws)
    print("patched", WORKSPACE)
else:
    print("already patched", WORKSPACE)

# ============================================================ 3. services.rs
s = SRV.read_text()

# The desktop-only helpers the three crates feed. Same four functions the first port removed -
# but now only the helper is gated, not the service it backs.
#
# Order matters: `native` is gated first, while its body is still untouched, because gating the
# `let clip` line inserts an attribute in the middle of that body and would break the anchor.
s = sub(
    s,
    "pub fn native(automation: Option<photocraft_automation::AuthorizedWorkspace>) -> Services {\n    let clip:",
    "/// The desktop services: rfd dialogs, the arboard clipboard, `open` for URLs.\n"
    "#[cfg(not(target_os = \"ios\"))]\n"
    "pub fn native(automation: Option<photocraft_automation::AuthorizedWorkspace>) -> Services {\n    let clip:",
    "gate the desktop native()",
)

s = gate(s, "fn show_file_dialog(request: FileDialogRequest", "show_file_dialog")
s = gate(s, "fn path_of(file: &rfd::FileHandle) -> String {", "path_of")
s = gate(s, "fn block_on<T>(future: impl Future<Output = T>) -> T {", "block_on")
s = gate(s, "fn image_from_files(paths: &[PathBuf]) -> Option<(u32, u32, Vec<u8>)> {", "image_from_files")
s = gate(s, "    let clip: Rc<RefCell<Option<arboard::Clipboard>>> = Rc::default();", "the arboard handle")

marker = "\n/// Flat-image import via photocraft-codecs (kept for reference/tests; the app uses photocraft-io)."
IOS_NATIVE_MARKER = "/// The iOS services: the same `Services` struct as the desktop build, with the four"
IOS_NATIVE = '''
/// The iOS services: the same `Services` struct as the desktop build, with the four
/// platform-backed fields supplied by [`crate::ios_services`] (UIKit) instead of rfd/arboard/open.
///
/// Everything else - import, export, prefs, recovery autosave, automation - is identical, so
/// every feature the desktop build has is present here too.
#[cfg(target_os = "ios")]
pub fn native(automation: Option<photocraft_automation::AuthorizedWorkspace>) -> Services {
    let automation_read = automation.clone().map(|workspace| {
        Box::new(move |path: &str| {
            let bytes = workspace.read(path).map_err(|error| error.to_string())?;
            let name = Path::new(path).file_name().and_then(|name| name.to_str()).unwrap_or(path).to_string();
            Ok((name, bytes))
        }) as photocraft_ui_egui::AutomationReadFn
    });
    let automation_write = automation.clone().map(|workspace| {
        Box::new(move |path: &str, bytes: &[u8]| workspace.write(path, bytes).map_err(|error| error.to_string())) as photocraft_ui_egui::AutomationWriteFn
    });
    let step: fn(&str, &serde_json::Value) -> photocraft_engine::Result<()> = photocraft_automation::workspace::authorize_desktop_engine_step;
    let automation_authorize = automation.is_some().then_some(step);
    let automation_command = automation.map(|_| {
        Box::new(|id: &str, params: &serde_json::Value| {
            photocraft_automation::workspace::authorize_desktop_engine_command(id, params).map_err(|error| error.to_string())
        }) as photocraft_ui_egui::AutomationCommandFn
    });
    Services {
        import: Some(Box::new(|name: &str, bytes: &[u8]| {
            crate::crash_guard::guard("Open", || photocraft_io::import(name, bytes).map(|r| (r.document, r.warnings)).map_err(|e| e.to_string()))
        })),
        export: Some(Box::new(|doc: &Document, path: &str, settings: &photocraft_ui_egui::ExportSettings| {
            let mut opts = photocraft_io::ExportOptions::default();
            if let Some(q) = settings.jpeg_quality {
                opts.encode.jpeg_quality = q;
            }
            opts.encode.webp_lossless = settings.webp_lossless;
            if let Some(q) = settings.webp_quality {
                opts.encode.webp_quality = q;
            }
            opts.tiff_layers = settings.tiff_layers;
            opts.xmp = if settings.xmp_all { photocraft_io::XmpEmbed::All } else { photocraft_io::XmpEmbed::None };
            crate::crash_guard::guard("Export", || photocraft_io::export(doc, path, &opts).map(|r| (r.bytes, r.warnings)).map_err(|e| e.to_string()))
        })),
        // The system document picker for Open; a Save needs no dialog, so it is answered at once
        // with a path under the app's Documents directory (see photocraft-ios-services).
        file_dialog: Some(Box::new(|request, _parent, reply| photocraft_ios_services::show_file_dialog(request, reply))),
        write: Some(Box::new(|path: &str, bytes: &[u8]| write_atomic(Path::new(path), bytes))),
        automation_read,
        automation_write,
        automation_command,
        automation_authorize,
        encode_png: Some(Box::new(|w, h, rgba| {
            let img = Image::from_u8(w, h, ChannelLayout::Rgba, rgba.to_vec()).map_err(|e| e.to_string())?;
            photocraft_codecs::encode(&img, photocraft_codecs::Format::Png, &EncodeOptions::default()).map_err(|e| e.to_string())
        })),
        inbox: None,
        open_url: Some(Box::new(|url: &str| photocraft_ios_services::open_url(url))),
        clipboard_set_image: Some(Box::new(|w: u32, h: u32, px: &[u8]| photocraft_ios_services::clipboard_set_image(w, h, px))),
        clipboard_get_image: Some(Box::new(photocraft_ios_services::clipboard_get_image)),
        load_prefs: Some(Box::new(|| std::fs::read_to_string(prefs_file()?).ok())),
        save_prefs: Some(Box::new(|text: &str| write_atomic(&prefs_file().ok_or("no config directory")?, text.as_bytes()))),
        append_text: Some(Box::new(|path: &str, text: &str| {
            use std::io::Write;
            let mut f = std::fs::OpenOptions::new().create(true).append(true).open(path).map_err(|e| e.to_string())?;
            f.write_all(text.as_bytes()).map_err(|e| e.to_string())
        })),
        // Set by main once the Apple-event handlers are connected (macOS).
        os_events: None,
        // Set by main, which starts loading the store before the window opens.
        preset_store: None,
        is_wayland: false,
        ..recovery_services(recovery_dir())
    }
}
'''

if IOS_NATIVE_MARKER not in s:
    s = sub(s, marker, IOS_NATIVE + marker, "append the iOS native()")
else:
    print("  already present (iOS native)")

SRV.write_text(s)
print("patched", SRV)

print("edits applied:", edits)
if edits == 0:
    print("WARNING: nothing changed - the tree is already patched, or upstream moved.")
    sys.exit(1)
if edits < 5:
    print("WARNING: fewer edits than expected - upstream may have moved.")
    sys.exit(1)
