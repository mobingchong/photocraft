//! The UIKit calls, in the one file of this crate that allows `unsafe`.
//!
//! Everything here is Objective-C interop: presenting a document picker, reading the pasteboard
//! and opening a URL. The crate's `Cargo.toml` sets `unsafe_code = "deny"` (overriding the
//! workspace's `forbid`, which an inner `allow` could not do); the `allow` on this module is the
//! narrowest scope that still lets objc2 work.

#![allow(unsafe_code)]

use std::cell::RefCell;
use std::path::PathBuf;

use objc2::rc::Retained;
use objc2::runtime::ProtocolObject;
use objc2::{define_class, msg_send, MainThreadOnly};
use objc2_foundation::{NSArray, NSData, NSHomeDirectory, NSObject, NSObjectProtocol, NSString, NSURL};
use objc2_ui_kit::{UIApplication, UIDocumentPickerDelegate, UIDocumentPickerViewController, UIImage, UIPasteboard, UIViewController};
use photocraft_ui_egui::{FileDialogAnswer, FileDialogReply};

thread_local! {
    /// The open dialog in flight. UIKit calls the delegate on the main thread, so a thread-local
    /// is enough and no lock is needed. The UI layer shows one dialog at a time — `ask_file`
    /// refuses a second while the first is up — so one slot suffices.
    static REPLY: RefCell<Option<FileDialogReply>> = const { RefCell::new(None) };

    /// Keeps the delegate alive: the picker holds its delegate weakly.
    static DELEGATE: RefCell<Option<Retained<PickerDelegate>>> = const { RefCell::new(None) };
}

/// The picker's delegate.
///
/// `UIDocumentPickerDelegate` is `MainThreadOnly`, which is what makes the thread-local hand-off
/// above sound: every callback arrives on the thread the app's event loop runs on.
pub struct PickerDelegateIvars {}

define_class!(
    // SAFETY: NSObject has no subclassing requirements, and the protocol is `MainThreadOnly`, so
    // UIKit only ever calls these on the main thread.
    //
    // `thread_kind` is not optional here: `UIDocumentPickerDelegate` requires
    // `ClassType::ThreadKind == dyn MainThreadOnly`, so a class without it does not satisfy the
    // protocol at all.
    #[unsafe(super(NSObject))]
    #[thread_kind = MainThreadOnly]
    #[name = "PhotoCraftDocumentPicker"]
    #[ivars = PickerDelegateIvars]
    struct PickerDelegate;

    unsafe impl NSObjectProtocol for PickerDelegate {}

    unsafe impl UIDocumentPickerDelegate for PickerDelegate {
        #[unsafe(method(documentPicker:didPickDocumentsAtURLs:))]
        fn did_pick(&self, _picker: &UIDocumentPickerViewController, urls: &NSArray<NSURL>) {
            let answer = urls.iter().next().and_then(|url| read_url(&url)).map(|(name, bytes)| FileDialogAnswer::Contents(name, bytes));
            answer_call(answer);
        }

        #[unsafe(method(documentPickerWasCancelled:))]
        fn did_cancel(&self, _picker: &UIDocumentPickerViewController) {
            answer_call(None);
        }
    }
);

impl PickerDelegate {
    /// A `MainThreadOnly` class allocates through a `MainThreadMarker`, not `alloc()`.
    fn new(mtm: objc2::MainThreadMarker) -> Retained<Self> {
        let this = Self::alloc(mtm).set_ivars(PickerDelegateIvars {});
        unsafe { msg_send![super(this), init] }
    }
}

/// Answer the app and drop the delegate, whichever way the dialog ended.
fn answer_call(value: Option<FileDialogAnswer>) {
    let reply = REPLY.with(|slot| slot.borrow_mut().take());
    DELEGATE.with(|slot| slot.borrow_mut().take());
    if let Some(reply) = reply {
        reply.send(value);
    }
}

/// Read a security-scoped URL while its scope is still open.
fn read_url(url: &NSURL) -> Option<(String, Vec<u8>)> {
    let name = url.lastPathComponent()?.to_string();
    let path = url.path()?.to_string();
    // The picker grants access to a URL it just handed back, and only while this callback runs.
    // For a file already inside the app's container this is a no-op that returns false.
    let scoped = unsafe { url.startAccessingSecurityScopedResource() };
    let bytes = std::fs::read(&path).ok();
    if scoped {
        unsafe { url.stopAccessingSecurityScopedResource() };
    }
    Some((name, bytes?))
}

/// The app container's home directory, from `NSHomeDirectory()`.
pub fn home_dir() -> Option<PathBuf> {
    Some(PathBuf::from(NSHomeDirectory().to_string()))
}

/// Present the document picker for an Open request.
pub fn present_open(reply: FileDialogReply) {
    let Some(mtm) = objc2::MainThreadMarker::new() else {
        // Every caller runs in the UI's frame callback, so this only trips if something is badly
        // wrong; answering Cancel beats a dialog that never appears and leaves the app waiting.
        log::error!("the file dialog must be shown from the main thread");
        reply.send(None);
        return;
    };

    let delegate = PickerDelegate::new(mtm);
    // The class is `MainThreadOnly`, so it allocates through the marker rather than plain `alloc()`.
    let picker = UIDocumentPickerViewController::initForOpeningContentTypes_asCopy(
        UIDocumentPickerViewController::alloc(mtm),
        &content_types(),
        true,
    );
    picker.setDelegate(Some(ProtocolObject::from_ref(&*delegate)));
    // One file at a time: the UI's dialog queue serialises requests anyway, and an asynchronous
    // multi-select would have to buffer an unbounded number of security scopes.
    picker.setAllowsMultipleSelection(false);

    let Some(root) = top_view_controller(mtm) else {
        log::error!("no view controller to present the file dialog from");
        return;
    };
    REPLY.with(|slot| *slot.borrow_mut() = Some(reply));
    DELEGATE.with(|slot| *slot.borrow_mut() = Some(delegate));
    // `presentViewController:animated:completion:` is the objc2 binding only under the `block2`
    // feature, which this crate does not enable; a direct `msg_send!` reaches the same selector
    // without pulling a block ABI in for a completion handler that would be empty anyway.
    let _: () = unsafe {
        msg_send![
            &*root,
            presentViewController: &*picker,
            animated: true,
            completion: Option::<&ProtocolObject<dyn objc2_foundation::NSObjectProtocol>>::None,
        ]
    };
}

/// The document types the picker offers.
///
/// `public.image` is the union of everything an image editor opens by default, and `public.data`
/// keeps "any file" reachable; the explicit extensions add the formats with no image UTI,
/// notably Photoshop's `.psd`. Anything still unlisted (.abr, .aco, .pcraft) arrives as a dynamic
/// type through `public.data`, which is why that entry is present rather than last.
fn content_types() -> Retained<NSArray<objc2_uniform_type_identifiers::UTType>> {
    use objc2_uniform_type_identifiers::UTType;
    let identifiers = ["public.image", "com.adobe.photoshop-image", "public.data"];
    let extensions = [
        "psd", "psb", "png", "jpg", "jpeg", "tif", "tiff", "webp", "gif", "bmp", "heic", "heif", "dng", "svg", "tga", "exr", "qoi", "pcraft",
    ];
    let mut types: Vec<Retained<UTType>> = Vec::with_capacity(identifiers.len() + extensions.len());
    for id in identifiers {
        if let Some(t) = UTType::typeWithIdentifier(&NSString::from_str(id)) {
            types.push(t);
        }
    }
    for ext in extensions {
        if let Some(t) = UTType::typeWithFilenameExtension(&NSString::from_str(ext)) {
            types.push(t);
        }
    }
    NSArray::from_retained_slice(&types)
}

/// The topmost view controller: the key window's root, following any presented sheets.
fn top_view_controller(mtm: objc2::MainThreadMarker) -> Option<Retained<UIViewController>> {
    let app = UIApplication::sharedApplication(mtm);
    // `keyWindow` is deprecated for multi-scene apps, but this port is a single-scene app: eframe
    // owns the one window, so the app-wide key window is exactly the one the UI is drawn in.
    #[allow(deprecated)]
    let window = app.keyWindow()?;
    let mut controller = window.rootViewController();
    while let Some(presented) = controller.as_ref().and_then(|c| c.presentedViewController()) {
        controller = Some(presented);
    }
    controller
}

/// The pasteboard image re-encoded as PNG, so the app's own decoder can read it.
pub fn pasteboard_png() -> Option<Vec<u8>> {
    let board = UIPasteboard::generalPasteboard();
    if !unsafe { board.hasImages() } {
        return None;
    }
    let image = unsafe { board.image() }?;
    // `PNGData` has no objc2 binding (the property is `nullable NSData *`), so ask for it directly.
    let data: Option<Retained<NSData>> = unsafe { msg_send![&*image, PNGData] };
    Some(data?.to_vec())
}

pub fn pasteboard_set_png(png: &[u8]) -> Result<(), String> {
    let data = NSData::with_bytes(png);
    // Plain `imageWithData:` rather than `imageWithData:scale:`: the latter is gated behind
    // `objc2-core-foundation` for its `CGFloat` argument, and scale 1.0 is what the default
    // initialiser already assumes for a bitmap this app produced itself.
    let image = UIImage::imageWithData(&data).ok_or_else(|| "UIImage rejected the PNG".to_string())?;
    let board = UIPasteboard::generalPasteboard();
    unsafe { board.setImage(Some(&image)) };
    Ok(())
}

pub fn open_url(url: &str) -> Result<(), String> {
    let mtm = objc2::MainThreadMarker::new().ok_or("opening a URL must happen on the main thread")?;
    let nsurl = NSURL::URLWithString(&NSString::from_str(url)).ok_or_else(|| format!("not a URL: {url}"))?;
    let app = UIApplication::sharedApplication(mtm);
    // `openURL:` is deprecated by UIKit in favour of the options/completion form, but it is the
    // only synchronous one, and the completion form would need a block ABI binding for a result
    // this code has nowhere to report asynchronously. objc2 already exposes it as a safe call.
    if app.openURL(&nsurl) {
        Ok(())
    } else {
        Err(format!("iOS declined to open {url}"))
    }
}
