#!/usr/bin/env python3
"""Widen craft-fonts from Japanese-only to also carry a Chinese UI face.

Upstream already has the whole embedding channel:

  * crates/text/build.rs reads $CRAFT_FONTS_DIR/fonts/manifest.txt and emits one
    `CraftFont { family, style, scripts, bytes: include_bytes!(..) }` per line
    (manifest columns: family | style | file | scripts, scripts = ISO 15924).
  * crates/ui-egui/src/cjk_fonts.rs registers those bytes lazily, from static
    memory, before it ever touches the filesystem.

but two spots hard-code Japanese:

  1. photocraft_text::craft_fonts::CraftFont knows only `is_japanese()`, and the
     only selector is `japanese_for_ui()`.
  2. cjk_fonts::craft_embedded() returns fonts for CjkScript::Japanese and
     Vec::new() for everything else - so a manifest entry tagged `Hans` was
     parsed and embedded but never selected.

This script adds the Chinese equivalents. It does not touch build.rs or the
manifest format: `Hans`/`Hant` were already legitimate script tags there.

Run from the checkout root:  python3 patch-fonts.py
"""

import pathlib
import sys

CRAFT = pathlib.Path("crates/text/src/craft_fonts.rs")
CJK = pathlib.Path("crates/ui-egui/src/cjk_fonts.rs")

edits = 0


def sub(text, old, new, where):
    """Replace `old` with `new`, once, and count it."""
    global edits
    if old not in text:
        print("MISS (%s): %r" % (where, old[:70]))
        return text
    edits += 1
    return text.replace(old, new, 1)


# ---------------------------------------------------- crates/text/craft_fonts.rs
c = CRAFT.read_text()

c = sub(
    c,
    """/// The preferred UI family for Japanese.
pub const UI_JAPANESE_FAMILY: &str = "BIZ UDPGothic";

impl CraftFont {
    /// True for a font meant for Japanese text.
    pub fn is_japanese(&self) -> bool {
        self.scripts.contains(&"Jpan")
    }

    /// True for a Mincho (serif) face.
    pub fn is_mincho(&self) -> bool {
        self.family.contains("Mincho")
    }
}
""",
    """/// The preferred UI family for Japanese.
pub const UI_JAPANESE_FAMILY: &str = "BIZ UDPGothic";

/// The preferred UI family for Chinese (Simplified and Traditional).
pub const UI_CHINESE_FAMILY: &str = "Noto Sans SC";

impl CraftFont {
    /// True for a font meant for Japanese text.
    pub fn is_japanese(&self) -> bool {
        self.scripts.contains(&"Jpan")
    }

    /// True for a font meant for Simplified Chinese text.
    pub fn is_simplified_chinese(&self) -> bool {
        self.scripts.contains(&"Hans")
    }

    /// True for a font meant for Traditional Chinese text.
    pub fn is_traditional_chinese(&self) -> bool {
        self.scripts.contains(&"Hant")
    }

    /// True for a font meant for either Chinese script (a Han face serves both).
    pub fn is_chinese(&self) -> bool {
        self.is_simplified_chinese() || self.is_traditional_chinese()
    }

    /// True for a Mincho (serif) face.
    pub fn is_mincho(&self) -> bool {
        self.family.contains("Mincho")
    }
}

/// The Chinese craft fonts in UI preference order: Noto Sans SC Regular first, then its other
/// styles, then the rest in manifest order. Empty unless the build embedded a `Hans`/`Hant` font.
pub fn chinese_for_ui() -> Vec<&'static CraftFont> {
    let mut v: Vec<&CraftFont> = CRAFT_FONTS.iter().filter(|f| f.is_chinese()).collect();
    v.sort_by_key(|f| (f.family != UI_CHINESE_FAMILY, f.style != "Regular"));
    v
}
""",
    "chinese_for_ui",
)

# Module header: it says "Today it carries the Japanese fonts".
c = sub(
    c,
    "//! (see `build.rs`); every user of it must work when it is empty. Today it carries the Japanese\n"
    "//! fonts: BIZ UDPGothic (UI) and Shippori Mincho / BIZ UDMincho (serif document text). The web\n"
    "//! build (wasm32) embeds none of them: they don't fit its size cap (see `build.rs`).\n",
    "//! (see `build.rs`); every user of it must work when it is empty. It carries the Japanese fonts\n"
    "//! (BIZ UDPGothic for UI, Shippori Mincho / BIZ UDMincho for serif document text) and may also\n"
    "//! carry a Simplified/Traditional Chinese UI face (Noto Sans SC). The web build (wasm32) embeds\n"
    "//! none of them: they don't fit its size cap (see `build.rs`).\n",
    "module header",
)

# Tests at the end of the file.
c = sub(
    c,
    """        assert_eq!((first.family, first.style), (UI_JAPANESE_FAMILY, "Regular"));
        assert!(v.iter().all(|f| f.is_japanese() && !f.bytes.is_empty()));
    }
}""",
    """        assert_eq!((first.family, first.style), (UI_JAPANESE_FAMILY, "Regular"));
        assert!(v.iter().all(|f| f.is_japanese() && !f.bytes.is_empty()));
    }

    #[test]
    fn ui_order_prefers_noto_sans_sc_regular() {
        let v = chinese_for_ui();
        let Some(first) = v.first() else {
            eprintln!("skipping: built without a Chinese craft font (no Hans/Hant in the manifest)");
            return;
        };
        assert_eq!((first.family, first.style), (UI_CHINESE_FAMILY, "Regular"));
        assert!(v.iter().all(|f| f.is_chinese() && !f.bytes.is_empty()));
    }

    #[test]
    fn japanese_and_chinese_selections_are_disjoint() {
        for f in CRAFT_FONTS {
            assert!(!(f.is_japanese() && f.is_chinese()), "{} is tagged for both locales", f.family);
        }
    }
}""",
    "craft_fonts tests",
)

CRAFT.write_text(c)
print("patched", CRAFT)


# ------------------------------------------------ crates/ui-egui/cjk_fonts.rs
u = CJK.read_text()

u = sub(
    u,
    """/// craft-fonts' Japanese fonts for the Japanese script, UI face first (empty without craft-fonts).
pub fn craft_embedded(script: CjkScript) -> Vec<&'static CraftFont> {
    if script == CjkScript::Japanese { craft_fonts::japanese_for_ui() } else { Vec::new() }
}""",
    """/// craft-fonts' embedded fonts for a script, UI face first (empty without craft-fonts).
///
/// Japanese takes the Japanese faces; both Chinese scripts take the Chinese face (a Han font
/// serves Simplified and Traditional alike). Korean has no craft font — it falls back to the
/// system path as before.
pub fn craft_embedded(script: CjkScript) -> Vec<&'static CraftFont> {
    match script {
        CjkScript::Japanese => craft_fonts::japanese_for_ui(),
        CjkScript::SimplifiedChinese | CjkScript::TraditionalChinese => craft_fonts::chinese_for_ui(),
        CjkScript::Korean => Vec::new(),
    }
}""",
    "craft_embedded",
)

u = sub(
    u,
    """//! Builds made with the optional craft-fonts input (`CRAFT_FONTS_DIR`,
//! [`photocraft_text::craft_fonts`]) carry Japanese fonts (BIZ UDPGothic first): they are tried
//! before the system Japanese fonts, in the Japanese slot of the same locale order. Without
//! craft-fonts (and on the web, which never embeds them) nothing changes.""",
    """//! Builds made with the optional craft-fonts input (`CRAFT_FONTS_DIR`,
//! [`photocraft_text::craft_fonts`]) carry Japanese fonts (BIZ UDPGothic first) and, when the
//! manifest tags a face `Hans`/`Hant`, a Chinese one (Noto Sans SC): they are tried before the
//! system fonts, in the matching slot of the same locale order. Without craft-fonts (and on the
//! web, which never embeds them) nothing changes.""",
    "module header",
)

u = sub(
    u,
    """        if craft_fonts::CRAFT_FONTS.is_empty() {
            assert!(craft_embedded(CjkScript::Japanese).is_empty());
        }
    }""",
    """        if craft_fonts::CRAFT_FONTS.is_empty() {
            assert!(craft_embedded(CjkScript::Japanese).is_empty());
            assert!(craft_embedded(CjkScript::SimplifiedChinese).is_empty());
        }
    }

    /// The Chinese locale's Han slot consumes the embedded Chinese face: Simplified and
    /// Traditional labels both render with no system font available (the iOS case).
    #[test]
    fn craft_fonts_render_chinese_without_system_fonts() {
        let Some(ui_font) = craft_fonts::chinese_for_ui().first().copied() else {
            eprintln!("skipping: built without a Chinese craft font (no Hans/Hant in the manifest)");
            return;
        };
        for locale in [
            (|| Some("zh-hans".into())) as fn() -> Option<String>,
            || Some("zh-hant".into()),
            || Some("en_US".into()),
            || None,
        ] {
            let ctx = egui::Context::default();
            crate::theme::install_fonts_with(&ctx, craft_only(locale));
            for text in ["图层", "打开文件", "圖層", "濾鏡", "取消"] {
                assert!(render(&ctx, text), "tofu in {text} ({:?})", locale());
            }
            let fonts = ctx.fonts(|f| f.definitions().clone());
            let name = format!("{FONT_PREFIX}-0");
            assert!(fonts.font_data.get(&name).is_some_and(|d| std::ptr::eq(d.font.as_ref().as_ptr(), ui_font.bytes.as_ptr())), "Noto Sans SC Regular first");
            for (fam, stack) in &fonts.families {
                assert_eq!(stack.last(), Some(&name), "{fam:?}: appended last so Latin keeps Inter");
                assert!(stack.first().is_some_and(|f| f != &name), "{fam:?}");
            }
        }
    }

    /// Korean has no craft font: a Korean label must not accidentally be served by the Chinese
    /// face, and with nothing else available it stays tofu without panicking.
    #[test]
    fn craft_fonts_do_not_pretend_to_cover_korean() {
        if craft_fonts::chinese_for_ui().is_empty() {
            eprintln!("skipping: built without a Chinese craft font");
            return;
        }
        assert!(craft_embedded(CjkScript::Korean).is_empty());
        let ctx = egui::Context::default();
        crate::theme::install_fonts_with(&ctx, craft_only(|| Some("ko".into())));
        assert!(!render(&ctx, "카드 배경"));
    }""",
    "cjk_fonts tests",
)

CJK.write_text(u)
print("patched", CJK)

print("edits applied:", edits)
if edits < 5:
    print("WARNING: fewer edits than expected - the upstream source may have moved.")
    sys.exit(1)
