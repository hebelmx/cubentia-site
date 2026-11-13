#!/usr/bin/env python3
"""Check the static site for broken internal links and privacy or branding problems.

Standard library only. HTML validation is done separately by the Nu Html Checker
(see scripts/check.sh); this script covers the rest.

The site is plain static HTML and CSS with no JavaScript, so the checker is deny-by-default:
anything such a site does not need, and that could load a resource, navigate, run code or
inject text, is rejected outright rather than inspected.

* every internal link and fragment resolves to a file (and an id) under the site root;
* only allowlisted elements, attributes, <link rel> values, CSS at-rules and CSS functions
  are accepted; duplicate attributes, <base>, <meta http-equiv>, DTD internal subsets and
  processing instructions are rejected;
* no resource comes from another origin (src, srcset, href, action, formaction, xlink:href,
  CSS url(), image-set() and @import); data: URIs are allowed only for image/* media types;
  stylesheets must be .css files;
* CSS comments and escapes (CSS Syntax 4.3.7) are normalized before CSS is scanned;
* CSS cannot generate text: `content` is only none, normal or "", strings appear only in
  font-family/font and URL-like functions, and counters, quotes, reordering and bidi
  overrides are rejected;
* site/index.html and site/404.html must exist; only allowlisted file types may be published;
* root-relative paths stay inside the GitHub Pages project path /cubentia-site/;
* 404.html, which is served at any missing path, uses only absolute site paths;
* the word "Rubik" never appears in page text, attribute values, CSS or manifest strings
  (URL paths are exempt, URL hosts are not). Text is checked as one concatenated string,
  after removing invisible characters and combining marks, folding compatibility forms,
  case and common look-alike letters, and also reversed.

Usage: python3 scripts/check_site.py [SITE_DIR]   (default: site)
Prints each problem as "file: message" and exits 1 if there is any; prints "OK" otherwise.
"""

from __future__ import annotations

import itertools
import json
import os
import posixpath
import re
import sys
import unicodedata
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

PROJECT_PREFIX = "/cubentia-site/"
NOT_FOUND_PAGE = "404.html"

# -- allowlists ---------------------------------------------------------------

HTML_ELEMENTS = {
    "html", "head", "body", "title", "meta", "link", "style",
    "header", "footer", "main", "nav", "section", "article", "aside", "address", "hgroup",
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "span", "a", "br", "wbr", "hr",
    "ul", "ol", "li", "dl", "dt", "dd", "strong", "em", "b", "i", "u", "s", "small", "mark",
    "code", "pre", "kbd", "samp", "var", "sub", "sup", "abbr", "cite", "q", "blockquote",
    "time", "del", "ins", "figure", "figcaption", "img", "picture", "source",
    "details", "summary", "table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td",
    "colgroup", "col", "form", "label", "input", "button", "select", "option", "optgroup",
    "textarea", "fieldset", "legend",
}
SVG_ELEMENTS = {
    "svg", "g", "defs", "symbol", "use", "path", "rect", "circle", "ellipse", "line",
    "polyline", "polygon", "text", "tspan", "desc", "metadata", "lineargradient",
    "radialgradient", "stop", "clippath", "mask", "pattern", "image",
}
ALLOWED_ELEMENTS = HTML_ELEMENTS | SVG_ELEMENTS
# Reasons shown for elements that are commonly tried; anything else gets a generic message.
EMBED_ELEMENTS = {"iframe", "frame", "frameset", "object", "embed", "applet", "portal"}
SVG_ANIMATION_ELEMENTS = {"set", "animate", "animatemotion", "animatetransform", "animatecolor"}

GLOBAL_ATTRS = {"id", "class", "lang", "dir", "hidden", "title", "role", "tabindex", "style",
                "translate"}
ELEMENT_ATTRS = {
    "html": {"xmlns"},
    "meta": {"charset", "name", "content", "property"},
    "link": {"rel", "href", "type", "media", "sizes", "hreflang"},
    "style": {"media"},
    "a": {"href", "hreflang", "target", "rel", "type"},
    "img": {"src", "srcset", "sizes", "alt", "width", "height", "loading", "decoding"},
    "source": {"srcset", "sizes", "media", "type", "width", "height"},
    "time": {"datetime"}, "del": {"datetime", "cite"}, "ins": {"datetime", "cite"},
    "blockquote": {"cite"}, "q": {"cite"},
    "details": {"open"}, "th": {"colspan", "rowspan", "scope", "headers", "abbr"},
    "td": {"colspan", "rowspan", "headers"}, "col": {"span"}, "colgroup": {"span"},
    "form": {"action", "method", "autocomplete", "novalidate", "name"},
    "label": {"for"}, "fieldset": {"disabled", "name"},
    "input": {"type", "name", "value", "placeholder", "required", "disabled", "checked",
              "readonly", "autocomplete", "maxlength", "minlength", "min", "max", "step",
              "pattern", "size", "multiple", "formaction", "formmethod"},
    "button": {"type", "name", "value", "disabled", "formaction", "formmethod"},
    "select": {"name", "required", "disabled", "multiple", "size", "autocomplete"},
    "option": {"value", "selected", "disabled", "label"}, "optgroup": {"label", "disabled"},
    "textarea": {"name", "rows", "cols", "placeholder", "required", "disabled", "readonly",
                 "maxlength", "minlength", "autocomplete"},
}
SVG_ATTRS = {
    "xmlns", "xmlns:xlink", "viewbox", "preserveaspectratio", "width", "height", "x", "y",
    "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "d", "points", "transform",
    "fill", "fill-rule", "fill-opacity", "stroke", "stroke-width", "stroke-linecap",
    "stroke-linejoin", "stroke-opacity", "stroke-dasharray", "stroke-dashoffset",
    "stroke-miterlimit", "opacity", "clip-path", "clip-rule", "mask", "focusable", "version",
    "offset", "stop-color", "stop-opacity", "gradientunits", "gradienttransform", "fx", "fy",
    "patternunits", "patterncontentunits", "patterntransform", "maskunits",
    "maskcontentunits", "clippathunits", "href", "xlink:href", "font-size", "font-family",
    "font-weight", "text-anchor", "dominant-baseline", "dx", "dy", "vector-effect",
    "shape-rendering", "aria-hidden",
}
# SVG presentation attributes that take a CSS value and may contain url(): checked as CSS.
SVG_CSS_ATTRS = {"fill", "stroke", "clip-path", "mask", "font-family"}
# Attributes whose value is a URL: exempt from the wording rule, checked as links/resources.
URL_ATTRS = {"href", "src", "srcset", "action", "formaction", "xlink:href", "cite"}
LINK_RELS = {"stylesheet", "icon", "canonical", "manifest"}

CSS_AT_RULES = {"media", "supports", "font-face", "import", "keyframes", "-webkit-keyframes",
                "layer", "container", "charset"}
CSS_FUNCTIONS = {
    "url", "image-set", "-webkit-image-set", "type", "format", "local", "tech",
    "var", "env", "calc", "min", "max", "clamp", "round", "abs",
    "rgb", "rgba", "hsl", "hsla", "hwb", "lab", "lch", "oklab", "oklch", "color", "color-mix",
    "light-dark", "linear-gradient", "radial-gradient", "conic-gradient",
    "repeating-linear-gradient", "repeating-radial-gradient", "repeating-conic-gradient",
    "translate", "translatex", "translatey", "translate3d", "rotate", "scale", "scalex",
    "scaley", "skew", "skewx", "skewy", "matrix", "cubic-bezier", "steps",
    "repeat", "minmax", "fit-content", "blur", "brightness", "contrast", "drop-shadow",
    "grayscale", "invert", "opacity", "saturate", "sepia", "hue-rotate",
    "not", "is", "where", "has", "nth-child", "nth-last-child", "nth-of-type",
    "nth-last-of-type", "lang", "dir", "selector",
}
# Properties that may hold strings (font names). All other strings are rejected.
CSS_STRING_PROPERTIES = {"font-family", "font"}
# Functions whose string argument is a URL, MIME type or font name, not page text.
CSS_STRING_FUNCTIONS = re.compile(
    r"""(?:url|format|local|tech|type)\(\s*(?:"[^"\n]*"|'[^'\n]*')\s*\)""", re.I)
# Properties whose url() is painted as an image; elsewhere (filter, mask, clip-path, cursor)
# url() can reference an SVG document.
CSS_DATA_URL_PROPERTIES = {"background", "background-image", "border-image", "border-image-source",
                           "list-style", "list-style-image", "mask-image", "-webkit-mask-image"}
CSS_CONTENT_VALUES = {"none", "normal", '""', "''"}
CSS_LIST_STYLE_WORDS = {"none", "disc", "circle", "square", "decimal", "inside", "outside"}
CSS_REJECTED_PROPERTIES = {
    "counter-reset": "CSS counters generate text",
    "counter-set": "CSS counters generate text",
    "counter-increment": "CSS counters generate text",
    "order": "visual reordering can assemble words",
    "-webkit-box-ordinal-group": "visual reordering can assemble words",
    "-webkit-box-direction": "visual reordering can assemble words",
    "string-set": "CSS strings generate text",
}
CSS_KEYWORD_PROPERTIES = {
    "unicode-bidi": {"normal", "isolate", "embed", "plaintext"},
    "direction": {"ltr"},
    "quotes": {"none", "auto"},
    "text-overflow": {"clip", "ellipsis"},
    "hyphenate-character": {"auto"},
    "-webkit-hyphenate-character": {"auto"},
    "text-emphasis": {"none"},
    "text-emphasis-style": {"none"},
    "-webkit-text-emphasis": {"none"},
    "-webkit-text-emphasis-style": {"none"},
}

ALLOWED_EXTENSIONS = {
    "", ".html", ".htm", ".xhtml", ".svg", ".css", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".avif", ".ico", ".woff", ".woff2", ".webmanifest", ".json", ".txt",
}
JSON_EXTENSIONS = (".webmanifest", ".json")
PAGE_EXTENSIONS = (".html", ".htm", ".xhtml", ".svg")
SCRIPT_SCHEMES = ("javascript", "vbscript")
REQUIRED_PAGES = ("index.html", NOT_FOUND_PAGE)

# -- text normalization -------------------------------------------------------

# A URL's host is checked; only its path and query are exempt from the wording rule.
URL_WITH_SCHEME = re.compile(r"https?://([^\s/?#]*)\S*", re.I)
URL_BARE = re.compile(r"\b((?:[\w-]+\.)+[a-z]{2,})/\S*", re.I)
RUBIK = re.compile(r"rubik")
SPLIT_WINDOW = 12  # text nodes that may lie between two pieces of a split word
PERMUTE_WINDOW = 6  # text nodes searched for pieces of the word in any order
# Latin look-alikes (after casefold) for the letters of the word.
CONFUSABLES = str.maketrans({
    "\u0433": "r", "\u0280": "r", "\u13a1": "r", "\u1d26": "r", "\uab71": "r",
    "\u03c5": "u", "\u057d": "u", "\u028b": "u", "\u1d1c": "u", "\u0446": "u",
    "\u044c": "b", "\u0432": "b", "\u03b2": "b", "\u0185": "b", "\u0299": "b", "\u13cf": "b",
    "\u0456": "i", "\u0131": "i", "\u03b9": "i", "\u04cf": "i", "\u0269": "i", "\u026a": "i",
    "\u0457": "i", "\u04c0": "i", "\u2170": "i",
    "\u043a": "k", "\u03ba": "k", "\u0138": "k", "\u049b": "k", "\u049d": "k", "\u1d0b": "k",
    "\u212a": "k",
})
BIDI_OVERRIDES = re.compile("[\u202a-\u202e\u2066-\u2069]")
SWALLOWED_MARKUP = re.compile(r"<[a-zA-Z/!?]")
RAW_TEXT_ELEMENTS = {"title", "textarea"}

# -- CSS ----------------------------------------------------------------------

CSS_COMMENT = re.compile(r"/\*.*?(?:\*/|\Z)", re.S)
CSS_URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)\s]*))\s*\)""", re.I)
CSS_IMPORT = re.compile(r"""@import\s+(?:url\(\s*(?:"([^"]*)"|'([^']*)'|([^)\s]*))\s*\)|"([^"]*)"|'([^']*)')""",
                        re.I)
CSS_IMAGE_SET = re.compile(r"(?:-webkit-)?image-set\(", re.I)
CSS_STRING = re.compile(r""""([^"\n]*)"?|'([^'\n]*)'?""")
CSS_TYPE = re.compile(r"""type\(\s*(?:"[^"]*"|'[^']*')\s*\)""", re.I)
CSS_FUNCTION_NAME = re.compile(r"((?:[\w-]|\\[0-9a-f]{6})+)\(", re.I)
CSS_AT_RULE = re.compile(r"@((?:[\w-]|\\[0-9a-f]{6})+)", re.I)
CSS_IMPORTANT = re.compile(r"!\s*important\s*$", re.I)
# CSS escape: hex digits plus one optional whitespace (CRLF counts as one), or any one character.
CSS_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})(?:\r\n|[ \t\n\r\f])?|(\r\n|[\s\S])|$)")
# Characters left escaped so that decoding never changes where strings, url() or tokens end.
CSS_STRUCTURAL = set("\"'()\\;{}:@") | set(" \t\n\r\f")

META_REFRESH_URL = re.compile(r"""^\s*\d*(?:\.\d*)?\s*[;,]?\s*(?:url\s*=\s*)?['"]?([^'"]*)""", re.I)


def normalize_css(css: str) -> str:
    """Strip comments, then decode CSS escapes (CSS Syntax 4.3.7) so u\\72l( reads as url(."""

    def decode(match: re.Match[str]) -> str:
        hex_digits, literal = match.group(1), match.group(2)
        if hex_digits is not None:
            code = int(hex_digits, 16)
            char = "\ufffd" if code == 0 or 0xD800 <= code <= 0xDFFF or code > 0x10FFFF else chr(code)
        elif literal is None:
            return "\ufffd"  # backslash at end of input
        elif literal in ("\n", "\r", "\f", "\r\n"):
            return ""  # escaped newline: line continuation, not a character
        else:
            char = literal
        # Re-escape structural characters as six hex digits: no trailing whitespace to split on.
        return f"\\{ord(char):06x}" if char in CSS_STRUCTURAL else char

    return CSS_ESCAPE.sub(decode, CSS_COMMENT.sub("", css))


def unescape_structural(text: str) -> str:
    """Decode the six-digit escapes normalize_css leaves for structural characters."""
    return re.sub(r"\\([0-9a-f]{6})", lambda m: chr(int(m.group(1), 16)), text)


def fold(text: str) -> str:
    """Reduce text to the letters a reader sees: no invisibles or marks, one case, no look-alikes."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if c != "\u034f" and unicodedata.category(c) not in ("Cf", "Mn", "Me"))
    text = unicodedata.normalize("NFKD", text.casefold()).translate(CONFUSABLES)
    return "".join(c for c in text if unicodedata.category(c) not in ("Cf", "Mn", "Me"))


def strip_invisible(text: str) -> str:
    """Remove characters that render as nothing: format (Cf) characters and U+034F."""
    return "".join(c for c in text if c != "\u034f" and unicodedata.category(c) != "Cf")


def mask_urls(text: str) -> str:
    """Drop invisible characters, then replace each URL with its host (paths are exempt)."""
    text = strip_invisible(text)
    text = URL_WITH_SCHEME.sub(lambda m: f" {m.group(1)} ", text)
    return URL_BARE.sub(lambda m: f" {m.group(1)} ", text)


def contains_rubik(text: str, reverse: bool = False) -> bool:
    folded = fold(mask_urls(text))
    return bool(RUBIK.search(folded) or (reverse and RUBIK.search(folded[::-1])))


def word_split_across(nodes: list[str]) -> bool:
    """True if consecutive pieces of text nodes spell the word, even when whole nodes between
    the pieces are skipped: those nodes may be hidden by CSS or the hidden attribute.

    nodes are already folded. A whitespace-only node renders as a space, so it ends a match.
    """
    word = "rubik"
    partial: dict[int, int] = {}  # letters matched so far -> index of the node that matched last
    for index, text in enumerate(nodes):
        if not text.strip():
            partial = {}
            continue
        grown: dict[int, int] = {}

        def keep(length: int, at: int) -> None:
            grown[length] = max(grown.get(length, -1), at)

        for length, at in partial.items():
            if index - at > SPLIT_WINDOW:
                continue
            rest = word[length:]
            if text.startswith(rest):
                return True
            if rest.startswith(text):
                keep(length + len(text), index)  # the whole node is the next piece
            keep(length, at)  # or the node is skipped
        for length in range(1, len(word)):
            if text.endswith(word[:length]):
                keep(length, index)
        partial = grown
    return False


def word_permuted_across(nodes: list[str]) -> bool:
    """True if some nodes close together, placed in any order (floats, grid, positioning can
    do that), spell the word. nodes are already folded; whitespace-only nodes are ignored."""
    # A piece longer than 9 letters can only lend its first or last 4 letters to the word.
    pieces = [n if len(n) <= 9 else f"{n[:4]}\x00{n[-4:]}" for n in (m.strip() for m in nodes) if n]
    for start in range(len(pieces)):
        window = pieces[start:start + PERMUTE_WINDOW]
        for size in range(2, min(len(window), 5) + 1):
            for chosen in itertools.permutations(window, size):
                if chosen[0] != window[0] and window[0] not in chosen:
                    continue  # this subset is tried again from a later start
                if "rubik" in "".join(chosen):
                    return True
    return False


def normalize_url(url: str) -> str:
    """Parse a URL the way a browser does: trim C0/space, drop tab/newline, treat \\ as /."""
    url = url.strip("".join(chr(c) for c in range(0x21)))
    return re.sub(r"[\t\n\r]", "", url).replace("\\", "/")


def is_script_url(value: str) -> bool:
    squeezed = re.sub(r"[\x00-\x20]", "", value).lower()
    return squeezed.startswith(tuple(f"{s}:" for s in SCRIPT_SCHEMES))


def data_url_allowed(url: str) -> bool:
    """data: URLs are fine only as images: a data: stylesheet or document can fetch more."""
    media_type = url.strip()[len("data:"):].split(",", 1)[0].split(";", 1)[0]
    return media_type.strip().lower().startswith("image/")


def srcset_urls(value: str) -> list[str]:
    """URLs of a srcset, parsed as the HTML standard does (commas inside URLs are kept)."""
    urls, i, n = [], 0, len(value)
    while i < n:
        while i < n and (value[i].isspace() or value[i] == ","):
            i += 1
        start = i
        while i < n and not value[i].isspace():
            i += 1
        url = value[start:i]
        if not url:
            break
        if url.endswith(","):
            url = url.rstrip(",")
        else:
            depth = 0  # skip descriptors up to the next comma outside parentheses
            while i < n and (value[i] != "," or depth):
                depth += {"(": 1, ")": -1}.get(value[i], 0)
                i += 1
        urls.append(url)
    return urls


def css_image_set_strings(css: str) -> list[str]:
    """Quoted URL strings inside image-set( ... ) and -webkit-image-set( ... )."""
    found: list[str] = []
    for match in CSS_IMAGE_SET.finditer(css):
        depth, end = 1, match.end()
        while end < len(css) and depth:
            depth += {"(": 1, ")": -1}.get(css[end], 0)
            end += 1
        body = css[match.end():end - 1]
        body = CSS_URL.sub(" ", body)  # url(...) entries are checked separately
        body = CSS_TYPE.sub(" ", body)  # type("image/avif") is a MIME type, not a URL
        found += [next((g for g in m.groups() if g is not None), "") for m in CSS_STRING.finditer(body)]
    return found


def remove_image_sets(css: str) -> str:
    out, pos = [], 0
    for match in CSS_IMAGE_SET.finditer(css):
        if match.start() < pos:
            continue
        depth, end = 1, match.end()
        while end < len(css) and depth:
            depth += {"(": 1, ")": -1}.get(css[end], 0)
            end += 1
        out.append(css[pos:match.start()] + " ")
        pos = end
    return "".join(out) + css[pos:]


def css_segments(css: str) -> list[tuple[str, str]]:
    """Split CSS at ; { } outside strings and parentheses: (text, terminator) pairs."""
    segments: list[tuple[str, str]] = []
    buf: list[str] = []
    quote, depth, i = "", 0, 0
    while i < len(css):
        c = css[i]
        if quote:
            buf.append(c)
            if c == "\\" and i + 1 < len(css):
                buf.append(css[i + 1])
                i += 1
            elif c in (quote, "\n"):
                quote = ""
        elif c in "\"'":
            quote = c
            buf.append(c)
        elif c == "(":
            depth += 1
            buf.append(c)
        elif c == ")":
            depth = max(0, depth - 1)
            buf.append(c)
        elif c in ";{}" and not depth:
            segments.append(("".join(buf).strip(), c))
            buf = []
        else:
            buf.append(c)
        i += 1
    segments.append(("".join(buf).strip(), ""))
    return [(text, end) for text, end in segments if text]


def without_strings(css: str) -> str:
    return CSS_STRING.sub('""', css)


def css_problems(raw_css: str) -> tuple[list[str], list[str], list[str]]:
    """Return (problems, resource URLs, @import URLs) for a stylesheet or declaration list."""
    css = normalize_css(raw_css)
    problems: list[str] = []
    urls = [next(g for g in m.groups() if g is not None) for m in CSS_URL.finditer(css)]
    imports = [next(g for g in m.groups() if g is not None) for m in CSS_IMPORT.finditer(css)]
    urls += [u for u in imports if u not in urls]
    urls += css_image_set_strings(css)
    # Inside a URL, the structural characters left escaped by normalize_css are plain text.
    urls = [unescape_structural(u) for u in urls]
    imports = [unescape_structural(u) for u in imports]

    if "<" in css:
        problems.append("'<' in CSS is not allowed (markup inside <style> can escape it)")
    bare = without_strings(CSS_URL.sub("url()", css))
    for name in sorted({m.group(1).lower() for m in CSS_FUNCTION_NAME.finditer(bare)} - CSS_FUNCTIONS):
        problems.append(f"CSS function {name}() is not allowed")
    for name in sorted({m.group(1).lower() for m in CSS_AT_RULE.finditer(bare)} - CSS_AT_RULES):
        problems.append(f"CSS at-rule @{name} is not allowed")
    worded = CSS_IMPORT.sub(" ", CSS_URL.sub(" ", css))
    if contains_rubik(worded):
        problems.append('"Rubik" in CSS')

    for text, end in css_segments(css):
        if end == "{" or text.startswith("@"):
            continue  # selectors and at-rule preludes: checked by the scans above
        prop, sep, value = text.partition(":")
        if not sep:
            continue
        prop = prop.strip().lower()
        value = CSS_IMPORTANT.sub("", value).strip()
        words = value.lower().split()
        for url in CSS_URL.finditer(value):
            target = next(g for g in url.groups() if g is not None).strip().lower()
            if target.startswith("data:") and prop not in CSS_DATA_URL_PROPERTIES:
                problems.append(f"data: URL in CSS {prop} is not allowed: only image properties may inline")
        if prop == "content" and " ".join(words) not in CSS_CONTENT_VALUES:
            problems.append(f"CSS content: {value[:40]!r} is not allowed: generated text is not checked")
        elif prop in CSS_REJECTED_PROPERTIES:
            problems.append(f"CSS property {prop} is not allowed: {CSS_REJECTED_PROPERTIES[prop]}")
        elif prop in CSS_KEYWORD_PROPERTIES and not set(words) <= CSS_KEYWORD_PROPERTIES[prop]:
            problems.append(f"CSS {prop}: {value[:40]!r} is not allowed")
        elif prop in ("list-style", "list-style-type") and \
                not set(CSS_URL.sub("", value).lower().split()) <= CSS_LIST_STYLE_WORDS:
            problems.append(f"CSS {prop}: {value[:40]!r} is not allowed: list markers generate text")
        elif prop in ("flex-direction", "flex-flow", "flex-wrap", "flex") and "reverse" in value.lower():
            problems.append(f"CSS {prop}: {value[:40]!r} is not allowed: visual reordering")
        # Custom properties may hold font stacks; every property that renders a string is
        # keyword-restricted above, so a var() cannot smuggle a string into page text.
        if prop not in CSS_STRING_PROPERTIES and prop != "content" and not prop.startswith("--"):
            rest = remove_image_sets(CSS_STRING_FUNCTIONS.sub(" ", value))
            if CSS_STRING.search(rest):
                problems.append(f"CSS string in {prop} is not allowed: only font names may be strings")
    return problems, urls, imports


# -- HTML ---------------------------------------------------------------------


class PageParser(HTMLParser):
    """Collects ids, links, resources, inline CSS, text and forbidden constructs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[str] = []  # <a>/<area> href, canonical: navigation
        self.resources: list[str] = []  # fetched by the browser: must be same-origin
        self.stylesheets: list[str] = []  # <link rel=stylesheet>: must be .css files
        self.css_blocks: list[str] = []
        self.problems: list[str] = []
        self._style_buffer: list[str] | None = None
        self._raw_text_element = ""  # <title> or <textarea>: content is text, never markup
        # Visible text, URLs masked per text node, joined with no separator on close().
        self._text_parts: list[str] = []

    def close(self) -> None:
        super().close()
        if self._style_buffer is not None:
            self.css_blocks.append("".join(self._style_buffer))
            self._style_buffer = None
        text = "".join(self._text_parts)
        if contains_rubik(text, reverse=True):
            folded = fold(text)
            match = RUBIK.search(folded) or RUBIK.search(folded[::-1])
            snippet = " ".join(folded[max(0, match.start() - 30):match.end() + 30].split()) if match else ""
            self.problems.append(f'"Rubik" in page text: {snippet!r}')
            return
        # Pieces separated by hidden nodes, or placed in reverse order (SVG x, CSS layout),
        # or each written backwards (bidi, mirroring).
        nodes = [fold(part) for part in self._text_parts]
        for variant in (nodes, nodes[::-1], [n[::-1] for n in nodes[::-1]]):
            if word_split_across(variant):
                self.problems.append('"Rubik" in page text, split across elements')
                return
        if word_permuted_across(nodes):
            self.problems.append('"Rubik" in page text, split across nearby elements in another order')

    # Declarations and processing instructions.

    def handle_decl(self, decl: str) -> None:
        if decl.strip().lower() != "doctype html":
            self.problems.append(f"<!{decl[:40]}> is not allowed: only <!doctype html>")

    def handle_pi(self, data: str) -> None:
        if not re.match(r"xml\s", data, re.I) or "stylesheet" in data.lower():
            self.problems.append(f"processing instruction <?{data[:40]}> is not allowed")

    def unknown_decl(self, data: str) -> None:
        if data.upper().startswith("CDATA["):
            self.handle_data(data[len("CDATA["):])  # CDATA is text in SVG and foreign content
        else:
            self.problems.append(f"<![{data[:40]}> is not allowed")

    # Elements.

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs)
        if tag == "style":
            self._style_buffer = []
        elif tag in RAW_TEXT_ELEMENTS:
            self._raw_text_element = tag

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs)

    def _start(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values: dict[str, str] = {}
        for name, value in attrs:
            if name in values:
                self.problems.append(f"duplicate attribute {name} on <{tag}> is not allowed")
            values.setdefault(name, value or "")

        if tag == "script":
            self.problems.append("<script> is not allowed")
        elif tag in EMBED_ELEMENTS:
            self.problems.append(f"<{tag}> embeds are not allowed")
        elif tag in SVG_ANIMATION_ELEMENTS:
            self.problems.append(f"SVG animation <{tag}> is not allowed")
        elif tag == "base":
            self.problems.append("<base> is not allowed: it changes how every URL resolves")
        elif tag not in ALLOWED_ELEMENTS:
            self.problems.append(f"<{tag}> is not allowed")
        allowed_attrs = GLOBAL_ATTRS | ELEMENT_ATTRS.get(tag, set())
        if tag in SVG_ELEMENTS:
            allowed_attrs = allowed_attrs | SVG_ATTRS

        for name, value in values.items():
            if name.startswith("on"):
                self.problems.append(f'event-handler attribute {name}="..." on <{tag}> is not allowed')
            elif name == "attributename":
                self.problems.append(f"attributeName on <{tag}> is not allowed (SVG animation)")
            elif name == "srcdoc":
                self.problems.append(f"srcdoc on <{tag}> is not allowed")
            elif name == "ping":
                self.problems.append(f"ping on <{tag}> is not allowed")
            elif name not in allowed_attrs and not name.startswith(("aria-", "data-")):
                self.problems.append(f"attribute {name} on <{tag}> is not allowed")
            if BIDI_OVERRIDES.search(value):
                self.problems.append(f"bidi control characters in {name} of <{tag}> are not allowed")
            if name == "id" and value:
                self.ids.add(value)
            if name == "style":
                self.css_blocks.append(value)
            elif tag in SVG_ELEMENTS and name in SVG_CSS_ATTRS:
                self.css_blocks.append(f"{name}: {value}")
            if name not in URL_ATTRS and name != "style":
                if is_script_url(value):
                    self.problems.append(f"script URL in {name} of <{tag}> (javascript:/vbscript:) is not allowed")
                if contains_rubik(value):
                    self.problems.append(f'"Rubik" in {name} of <{tag}>')

        if tag == "meta" and "http-equiv" in values:
            equiv = values["http-equiv"].strip().lower()
            self.problems.append(f'<meta http-equiv="{equiv}"> is not allowed')
            if equiv == "refresh":
                match = META_REFRESH_URL.match(values.get("content", ""))
                if match and match.group(1).strip():
                    self.resources.append(match.group(1).strip())

        rels = values.get("rel", "").lower().split() if tag == "link" else []
        if tag == "link":
            if not rels or not set(rels) <= LINK_RELS:
                self.problems.append(f'<link rel="{values.get("rel", "")}"> is not allowed: '
                                     f'only {", ".join(sorted(LINK_RELS))}')
        if "href" in values:
            if tag in {"a", "area"} or rels == ["canonical"]:
                self.links.append(values["href"])
            else:  # <link>, <use>, <image>, ...: fetched by the browser
                self.resources.append(values["href"])
                if "stylesheet" in rels:
                    self.stylesheets.append(values["href"])
        for name in ("src", "action", "formaction", "xlink:href", "poster", "data", "background"):
            if name in values:
                self.resources.append(values[name])
        if "ping" in values:
            self.resources.extend(values["ping"].split())
        for name in ("srcset", "imagesrcset"):
            if name in values:
                self.resources.extend(srcset_urls(values[name]))
        if tag == "img" and values.get("alt"):
            self._text_parts.append(mask_urls(values["alt"]))  # shown when the image fails
        if tag == "input":
            for name in ("value", "placeholder"):
                if values.get(name):
                    self._text_parts.append(mask_urls(values[name]))  # shown in the field
        if tag in {"text", "tspan"}:
            for name in ("x", "y", "dx", "dy"):
                if len(re.split(r"[\s,]+", values.get(name, "").strip())) > 1:
                    self.problems.append(f"per-glyph {name} on <{tag}> is not allowed: it can reorder letters")
        if tag == "meta" and "charset" in values and values["charset"].strip().lower() != "utf-8":
            self.problems.append(f'<meta charset="{values["charset"]}"> is not allowed: the checker reads UTF-8')
        data_ok = {"img": {"src", "srcset"}, "source": {"srcset"}, "image": {"href", "xlink:href"}}
        if tag == "link" and rels == ["icon"]:
            data_ok["link"] = {"href"}
        for name, value in values.items():
            if name in URL_ATTRS | {"imagesrcset", "poster", "data", "background"} and \
                    normalize_url(value).lower().startswith("data:") and name not in data_ok.get(tag, set()):
                self.problems.append(f"data: URL in {name} of <{tag}> is not allowed: only images may be inline")

    def handle_endtag(self, tag: str) -> None:
        if tag == "style" and self._style_buffer is not None:
            self.css_blocks.append("".join(self._style_buffer))
            self._style_buffer = None
        if tag == self._raw_text_element:
            self._raw_text_element = ""

    def handle_data(self, data: str) -> None:
        if self._style_buffer is not None:
            self._style_buffer.append(data)
            return
        if BIDI_OVERRIDES.search(data):
            self.problems.append("bidi control characters in page text are not allowed")
        if self._raw_text_element and SWALLOWED_MARKUP.search(data):
            # Inside <svg> a browser parses this markup as elements, and <img> breaks out.
            self.problems.append(f"markup inside a text-only element is not allowed: {data[:40]!r}")
        self._text_parts.append(mask_urls(data))


# -- site ---------------------------------------------------------------------


class SiteChecker:
    def __init__(self, site_dir: str) -> None:
        self.site_dir = os.path.abspath(site_dir)
        self.display_root = site_dir.rstrip("/\\") or "."
        self.problems: list[str] = []
        self._pages: dict[str, PageParser] = {}

    # -- helpers -----------------------------------------------------------

    def _rel(self, path: str) -> str:
        return os.path.relpath(path, self.site_dir).replace(os.sep, "/")

    def _report(self, rel_file: str, message: str) -> None:
        self.problems.append(f"{posixpath.join(self.display_root, rel_file)}: {message}")

    def _page(self, rel_file: str) -> PageParser | None:
        if rel_file not in self._pages:
            path = os.path.join(self.site_dir, *rel_file.split("/"))
            try:
                with open(path, encoding="utf-8") as handle:
                    parser = PageParser()
                    parser.feed(handle.read())
                    parser.close()
            except (OSError, UnicodeDecodeError):
                return None
            self._pages[rel_file] = parser
        return self._pages[rel_file]

    def _resolve(self, rel_file: str, url: str, kind: str) -> str | None:
        """Return the target's site-relative file path, or None after reporting a problem.

        Returns "" for URLs that need no file check (external navigation, fragment-only).
        """
        if is_script_url(url):
            self._report(rel_file, f"script URL {url!r} (javascript:/vbscript:) is not allowed")
            return None
        clean = normalize_url(url)
        parts = urlsplit(clean)
        if parts.scheme or clean.startswith("//"):
            if kind == "link":
                return ""  # external navigation is allowed
            if parts.scheme.lower() == "data":
                if data_url_allowed(clean) and kind != "stylesheet":
                    return ""  # an inline image is not fetched from another origin
                self._report(rel_file, f"non-image data: {kind} {url[:60]!r} is not allowed: "
                                       f"it can load third-party resources")
                return None
            self._report(rel_file, f"third-party {kind} {url!r}: resources must be same-origin")
            return None
        path = unquote(parts.path)
        if not path:
            return rel_file  # fragment-only or query-only: the same page
        if path.startswith("/"):
            if path == PROJECT_PREFIX.rstrip("/"):
                path = PROJECT_PREFIX
            if not path.startswith(PROJECT_PREFIX):
                self._report(
                    rel_file,
                    f"root-relative path {url!r} escapes the project path {PROJECT_PREFIX}",
                )
                return None
            target = path[len(PROJECT_PREFIX):]
        else:
            if posixpath.basename(rel_file) == NOT_FOUND_PAGE and posixpath.dirname(rel_file) == "":
                self._report(
                    rel_file,
                    f"relative path {url!r} breaks when {NOT_FOUND_PAGE} is served at a missing "
                    f"path; use {PROJECT_PREFIX}...",
                )
                return None
            target = posixpath.join(posixpath.dirname(rel_file), path)
        trailing_slash = target.endswith("/") or target == ""
        target = posixpath.normpath(target) if target else "."
        if target == ".." or target.startswith("../"):
            self._report(rel_file, f"{kind} {url!r} points outside the site root")
            return None
        full = os.path.join(self.site_dir, *[p for p in target.split("/") if p not in ("", ".")])
        if os.path.isdir(full):
            target = posixpath.normpath(posixpath.join(target, "index.html"))
            full = os.path.join(full, "index.html")
        elif trailing_slash:
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            self._report(rel_file, f"broken {kind} {url!r}: {self._rel(full)} does not exist")
            return None
        if kind == "stylesheet" and not target.lower().endswith(".css"):
            self._report(rel_file, f"stylesheet {url!r} must be a .css file (others are not checked)")
            return None
        return target

    def _check_fragment(self, rel_file: str, url: str, target: str) -> None:
        fragment = unquote(urlsplit(normalize_url(url)).fragment)
        if not fragment or not target.lower().endswith(PAGE_EXTENSIONS):
            return
        page = self._page(target)
        if page is None or fragment not in page.ids:
            self._report(rel_file, f"broken fragment {url!r}: no id {fragment!r} in {target}")

    def _check_css(self, rel_file: str, css: str) -> None:
        problems, urls, imports = css_problems(css)
        for message in problems:
            self._report(rel_file, message)
        for url in urls:
            url = url.strip()
            if not url or url.startswith("#"):
                continue
            self._resolve(rel_file, url, "stylesheet" if url in imports else "CSS resource")

    def _check_json(self, rel_file: str, text: str) -> None:
        try:
            data = json.loads(text)
        except ValueError:
            self._report(rel_file, "is not valid JSON")
            return
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                stack.extend(item.keys())
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, str):
                clean = normalize_url(item)
                if urlsplit(clean).scheme.lower() not in ("", "data") or clean.startswith("//"):
                    self._report(rel_file, f"third-party URL {item[:60]!r}: resources must be same-origin")
                if contains_rubik(item):
                    self._report(rel_file, f'"Rubik" in {item[:60]!r}')

    # -- entry point -------------------------------------------------------

    def run(self) -> list[str]:
        if not os.path.isdir(self.site_dir):
            self.problems.append(f"{self.display_root}: site directory does not exist")
            return self.problems
        for required in REQUIRED_PAGES:
            if not os.path.isfile(os.path.join(self.site_dir, required)):
                self._report(required, "required page does not exist")
        if self.problems:
            return self.problems
        files: list[str] = []
        for root, dirs, names in os.walk(self.site_dir):
            dirs.sort()
            for name in sorted(names):
                files.append(self._rel(os.path.join(root, name)))
        for rel_file in files:
            lower = rel_file.lower()
            if os.path.splitext(lower)[1] not in ALLOWED_EXTENSIONS:
                self._report(rel_file, "file type is not allowed on the site")
            if lower.endswith(PAGE_EXTENSIONS):
                page = self._page(rel_file)
                if page is None:
                    self._report(rel_file, "cannot be read as UTF-8")
                    continue
                for message in page.problems:
                    self._report(rel_file, message)
                for url in page.links:
                    target = self._resolve(rel_file, url, "link")
                    if target:
                        self._check_fragment(rel_file, url, target)
                for url in page.resources:
                    kind = "stylesheet" if url in page.stylesheets else "resource"
                    self._resolve(rel_file, url, kind)
                for css in page.css_blocks:
                    self._check_css(rel_file, css)
            elif lower.endswith((".css",) + JSON_EXTENSIONS):
                try:
                    with open(os.path.join(self.site_dir, rel_file), encoding="utf-8") as handle:
                        text = handle.read()
                except (OSError, UnicodeDecodeError):
                    self._report(rel_file, "cannot be read as UTF-8")
                    continue
                if lower.endswith(".css"):
                    self._check_css(rel_file, text)
                else:
                    self._check_json(rel_file, text)
        return self.problems


def check_site(site_dir: str) -> list[str]:
    return SiteChecker(site_dir).run()


def main(argv: list[str]) -> int:
    site_dir = argv[1] if len(argv) > 1 else "site"
    problems = check_site(site_dir)
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} problem(s) found", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
