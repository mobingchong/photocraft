//! UIKit platform services for the iOS port of PhotoCraft.
//!
//! The desktop app reaches for three crates that have no iOS implementation at all: `rfd`
//! (AppKit / GTK / Win32 file dialogs), `arboard` (the desktop clipboard) and `open` (hand a URL
//! to the desktop shell). This crate supplies the UIKit equivalents so the iOS build keeps every
//! feature those three power:
//!
//! | desktop       | iOS                              |
//! |---------------|----------------------------------|
//! | `rfd`         | `UIDocumentPickerViewController` |
//! | `arboard`     | `UIPasteboard.generalPasteboard` |
//! | `open::that`  | `-[UIApplication openURL:]`      |
//!
//! # Why this is a separate crate
//!
//! Objective-C interop is `unsafe` by nature, and this workspace sets `unsafe_code = "forbid"`
//! in `[workspace.lints.rust]`. `forbid` cannot be overridden by an inner `allow`, so the only
//! way to write Objective-C is a crate that sets its own lint level — which is exactly what
//! [`photocraft-tablet`] does for macOS pen input, and what this crate does for the iOS services.
//! `src/uikit.rs` is the only file that allows `unsafe`; everything else stays denied.
//!
//! On every target but iOS this crate compiles to nothing: `uikit.rs` is gated on
//! `target_os = "ios"`, and so are the functions below. The app crate can therefore depend on it
//! unconditionally.
//!
//! # Files: names, not paths
//!
//! An iOS app is sandboxed. There is no shared filesystem to hand a path back from, and the
//! picker returns a *security-scoped URL* that is valid only while its delegate callback runs. So
//! the two directions are answered differently:
//!
//! * **Open** answers with `FileDialogAnswer::Contents`: the file's name and bytes, read here
//!   while the scope is still open.
//! * **Save** answers with a path inside the app's own **Documents** directory. The app writes
//!   there through the ordinary `write` service — no callback-timing dance, and the same
//!   `write(path, bytes)` contract every other platform uses. Documents is the one directory the
//!   Files app shows for an app, so a saved document is immediately visible, shareable, and
//!   openable again (see `UIFileSharingEnabled` and `LSSupportsOpeningDocumentsInPlace` in the
//!   bundle's Info.plist).

#[cfg(target_os = "ios")]
mod uikit;

use std::path::PathBuf;

/// Where a save lands: `<container>/Documents`, the directory the iOS Files app exposes.
#[cfg(target_os = "ios")]
pub fn documents_dir() -> Option<PathBuf> {
    uikit::home_dir().map(|home| home.join("Documents"))
}

/// Where a save lands. `None` off iOS, where the desktop services handle files themselves.
#[cfg(not(target_os = "ios"))]
pub fn documents_dir() -> Option<PathBuf> {
    None
}

/// The path a save should write to, given the name the app suggested.
///
/// A bare name becomes a Documents path. A name that already carries a directory is left alone,
/// so a caller that knows where it wants the file keeps that choice.
#[cfg(target_os = "ios")]
pub fn save_path(suggested: &str) -> Option<PathBuf> {
    let dir = documents_dir()?;
    std::fs::create_dir_all(&dir).ok()?;
    let path = PathBuf::from(suggested);
    if path.components().count() > 1 {
        return Some(path);
    }
    Some(dir.join(path.file_name()?))
}

#[cfg(not(target_os = "ios"))]
pub fn save_path(_suggested: &str) -> Option<PathBuf> {
    None
}

/// Show an Open dialog, answering with the picked file's name and bytes.
///
/// A save needs no dialog on iOS (see the crate docs): it goes straight to [`save_path`], so a
/// `FileDialogRequest::Save` is answered immediately rather than presented to the user.
#[cfg(target_os = "ios")]
pub fn show_file_dialog(request: photocraft_ui_egui::FileDialogRequest, reply: photocraft_ui_egui::FileDialogReply) {
    use photocraft_ui_egui::{FileDialogAnswer, FileDialogRequest};
    match request {
        FileDialogRequest::Open { .. } => uikit::present_open(reply),
        FileDialogRequest::Save { suggested } => match save_path(&suggested) {
            Some(path) => reply.send(Some(FileDialogAnswer::SaveTo(path.to_string_lossy().into_owned()))),
            None => {
                log::error!("no writable Documents directory for {suggested}");
                reply.send(None);
            }
        },
    }
}

/// Off iOS there is no document picker; the desktop services own the dialog.
#[cfg(not(target_os = "ios"))]
pub fn show_file_dialog(_request: photocraft_ui_egui::FileDialogRequest, reply: photocraft_ui_egui::FileDialogReply) {
    reply.send(None);
}

/// The image on the system pasteboard, as RGBA8, or `None` when there is none.
///
/// UIKit hands back a `UIImage`, which may be backed by any of several pixel layouts. Rather than
/// walk CoreGraphics to find out which, ask UIKit for a PNG and run it through the project's own
/// `photocraft-codecs` decoder — that path already handles every imported file, so it cannot
/// disagree with the rest of the app about channel order.
#[cfg(target_os = "ios")]
pub fn clipboard_get_image() -> Option<(u32, u32, Vec<u8>)> {
    let png = uikit::pasteboard_png()?;
    let img = photocraft_codecs::decode(&png).ok()?;
    Some((img.width(), img.height(), img.to_rgba8()))
}

#[cfg(not(target_os = "ios"))]
pub fn clipboard_get_image() -> Option<(u32, u32, Vec<u8>)> {
    None
}

/// Put an RGBA8 image on the system pasteboard.
#[cfg(target_os = "ios")]
pub fn clipboard_set_image(width: u32, height: u32, rgba: &[u8]) -> Result<(), String> {
    // `UIImage` wants an encoded image; reuse the PNG encoder the export path already uses
    // instead of building a bitmap context by hand.
    let img = photocraft_codecs::Image::from_u8(width, height, photocraft_codecs::ChannelLayout::Rgba, rgba.to_vec()).map_err(|e| e.to_string())?;
    let png = photocraft_codecs::encode(&img, photocraft_codecs::Format::Png, &photocraft_codecs::EncodeOptions::default()).map_err(|e| e.to_string())?;
    uikit::pasteboard_set_png(&png)
}

#[cfg(not(target_os = "ios"))]
pub fn clipboard_set_image(_width: u32, _height: u32, _rgba: &[u8]) -> Result<(), String> {
    Err("the iOS clipboard services are not available on this platform".into())
}

/// Open a URL in the system browser (or whichever app claims its scheme).
#[cfg(target_os = "ios")]
pub fn open_url(url: &str) -> Result<(), String> {
    uikit::open_url(url)
}

#[cfg(not(target_os = "ios"))]
pub fn open_url(_url: &str) -> Result<(), String> {
    Err("the iOS URL service is not available on this platform".into())
}
