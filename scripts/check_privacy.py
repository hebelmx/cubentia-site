#!/usr/bin/env python3
"""Check the privacy policy page against the app's privacy facts and the policy record.

Standard library only. Three inputs:

* the facts file, docs/privacy-facts.json from the app repository (Story 13.3). Every key
  except schemaVersion is a fact, and schemaVersion must be 1;
* the page, site/privacy/index.html. A statement is an element with data-fact="<key>",
  data-value="<value>" and non-empty text. <value> is the raw string for a JSON string and
  compact JSON otherwise (false, [], ...). data-policy markers name the publisher, the Play
  developer, the contact email and the effective date;
* the policy record, privacy-policy.json (repository root, not published). It holds the
  publisher, Play developer, contact email, effective date and textSha256, a hash of the
  whitespace-normalized text of the whole page (head, header, nav, main, footer, and any text
  after </body>), every element's tag and attributes in order, the (data-fact, data-value)
  pairs in order, the <title>, the meta description, every <a href> target, and the path and
  full text of every stylesheet the page loads (<link rel=stylesheet>, then @import), so a
  stylesheet change is a policy change and needs a later effective date.

Each supported fact value has one approved wording (APPROVED_WORDING). A statement's text must
equal the wording for its value; a value with no approved wording fails, naming the fact. A
statement must be a visible <p> or <li> inside <main>: no hiding attributes on it or its
ancestors, and only attribute-free <code>, <em> or <strong> inside it. Every stylesheet the
page loads and every style="" attribute may use only allowlisted CSS (CSS_PROPERTIES and
CSS_TOKENS), so no rule can hide, shrink, move away or recolor a statement out of sight.
Every text color (the ink tokens and the browser's default text colors) must reach
MIN_TEXT_CONTRAST (4.5:1, WCAG 2.2 AA for normal text; the check cannot tell large text apart)
against every background, and every color token used in a border or outline must reach
MIN_NON_TEXT_CONTRAST (3:1, WCAG 1.4.11), in both schemes.

The check fails when a fact has no statement with an equal value, the page states a fact the
file lacks, statements conflict, are empty, hidden or worded otherwise than approved, a fact
value has no approved wording, a marker is missing or wrong, the page holds any email other
than the contact, or the policy text changed without a new, recorded effective date.

Usage:
  python3 scripts/check_privacy.py --facts FILE [--page PAGE] [--record RECORD] [--base REV]
  python3 scripts/check_privacy.py --update [--page PAGE] [--record RECORD]

--base REV compares with a trusted previous git revision (CI passes the pull request's base
commit, or HEAD^1 on main): if the page's policy text hash differs from the hash of REV's page,
the effective date must be strictly later than REV's recorded effectiveDate. A REV without a
policy record (the first publish) is allowed and noted; any other failure to read REV fails.

--update rewrites effectiveDate and textSha256 from the page, and refuses unless the page's
date is later than the recorded one. Without a record it creates one, reading the publisher,
Play developer and contact from the page.

Prints each problem as "file: message" and exits 1 if there is any; prints "OK" otherwise.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import html
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PAGE = os.path.join(ROOT, "site", "privacy", "index.html")
DEFAULT_RECORD = os.path.join(ROOT, "privacy-policy.json")

SCHEMA_VERSION = 1
RECORD_FIELDS = ("publisher", "playDeveloper", "contactEmail", "effectiveDate", "textSha256")
VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                 "param", "source", "track", "wbr"}
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
MAILTO = re.compile(r"mailto:([^\s\"'<>?]*)", re.I)
SHA256 = re.compile(r"[0-9a-f]{64}")
PACKAGE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")

# The one sentence each supported (fact, data-value) must produce, whitespace-normalized.
# A value missing here has no approved wording yet: write it here (and on the page) first.
APPROVED_WORDING: dict[str, dict[str, str]] = {
    "dataLeavesDevice": {"false": "Cubentia collects no personal data and shares none. "
                                  "Nothing you do in the app leaves your device."},
    "account": {"false": "There is no account and no sign-in."},
    "ads": {"false": "There are no ads."},
    "analytics": {"false": "There is no analytics and no usage tracking."},
    "crashReporting": {"false": "There is no crash reporting."},
    "denylistedSdks": {"[]": "The app contains no advertising, analytics or crash-reporting "
                             "libraries."},
    "internetPermission": {"false": "The app has no internet permission, so it cannot send "
                                    "anything over the network."},
    "runtimePermissions": {"[]": "The app asks for no runtime permission. It does not use the "
                                 "camera, microphone, location, contacts or your files."},
    "backupAllowed": {"false": "The app does not allow backup, so your progress is not copied "
                               "to a cloud backup or to another device."},
    "storage": {"app-private": "Your progress is stored only on your device, in storage that "
                               "only the app can read. Uninstalling the app removes it."},
}
# Facts whose value is a package name, stated in this template.
TEMPLATE_WORDING = {
    "applicationId": "The app this policy covers has the Android package name {value}.",
}
STATEMENT_TAGS = {"p", "li"}
STATEMENT_ATTRS = {"data-fact", "data-value"}
STATEMENT_CHILD_TAGS = {"code", "em", "strong"}
STATEMENT_ANCESTOR_TAGS = {"html", "body", "main", "section", "article", "div", "ul", "ol"}
STATEMENT_ANCESTOR_ATTRS = {"id", "lang", "dir"}

# -- CSS the policy page loads: an allowlist, so that no rule can hide or remove a statement --
#
# Every declaration in every stylesheet the page loads (<link rel=stylesheet>, then @import),
# and in every style="" attribute, must be one of CSS_PROPERTIES with a value its rule accepts,
# whatever the selector: no display:none, visibility, opacity, positioning, transform, clip,
# width/height, overflow, text-indent, content or tiny font sizes. Colors are only the tokens
# below, which are defined on :root (and overridden only in @media (prefers-color-scheme:
# dark)). In both schemes, every text color (ink tokens and the browser's default text colors)
# must reach MIN_TEXT_CONTRAST against every background (surface tokens and the browser's
# canvas), and every token used in a border or outline must reach MIN_NON_TEXT_CONTRAST.
# No escapes, !important, nested rules or other at-rules.
CSS_TOKENS = {
    "--surface": "surface", "--surface-raised": "surface",
    "--ink": "ink", "--ink-muted": "ink", "--accent": "ink",
    "--font-sans": "font", "--measure": "measure", "--gutter": "length",
}
# Browser default text and canvas colors, per scheme: always checked.
UA_COLORS = {
    "light": {"ink": ["#000000"], "surface": ["#ffffff"]},
    "dark": {"ink": ["#ffffff"], "surface": ["#121212"]},
}
# Browser default link colors (link, visited, active), per scheme. Author styles override them
# whatever the specificity, so they are checked only when no rule with selector exactly "a"
# (outside the dark media block) sets color: that rule decides every link's color.
UA_LINK_COLORS = {
    "light": ["#0000ee", "#551a8b", "#ee0000"],
    "dark": ["#9e9eff", "#d0adf0", "#ff9e9e"],
}
MIN_TEXT_CONTRAST = 4.5
MIN_NON_TEXT_CONTRAST = 3.0
FONT_FAMILIES = {"ibm plex sans", "system-ui", "-apple-system", "segoe ui", "roboto",
                 "helvetica neue", "arial", "sans-serif"}
DARK_MEDIA = "(prefers-color-scheme: dark)"
STYLESHEET_HREF = re.compile(r"(?:\.\.?/)*(?:[A-Za-z0-9_-][A-Za-z0-9._-]*/)*[A-Za-z0-9_-][A-Za-z0-9._-]*\.css")
CSS_IMPORT_VALUE = re.compile(r"""(?:"([^"]*)"|'([^']*)'|url\(\s*(?:"([^"]*)"|'([^']*)'|([^"'()\s]*))\s*\))""",
                              re.I)
CSS_VAR = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)\s*\)", re.I)
CSS_LENGTH = re.compile(r"(\d+(?:\.\d+)?|\.\d+)(px|rem)?", re.I)
HEX_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")


def css_length(word: str, low: float, high: float, zero_ok: bool = True) -> bool:
    """A px or rem length (or a bare 0) within [low, high] rem."""
    match = CSS_LENGTH.fullmatch(word)
    if not match:
        return False
    number, unit = float(match.group(1)), (match.group(2) or "").lower()
    if not unit:
        return zero_ok and number == 0 and low <= 0
    rem = number / 16 if unit == "px" else number
    return low <= rem <= high


def css_var(word: str, roles: set[str]) -> bool:
    match = CSS_VAR.fullmatch(word)
    return bool(match) and CSS_TOKENS.get(match.group(1)) in roles


def css_words(value: str, check, low: int = 1, high: int = 4) -> bool:
    words = value.split()
    return low <= len(words) <= high and all(check(w) for w in words)


def css_box(word: str) -> bool:
    return css_length(word, 0, 4) or css_var(word, {"length"})


def css_line(value: str) -> bool:
    """border/outline: width up to 4px, solid or none, a color token."""
    def part(word: str) -> bool:
        return (css_length(word, 0, 0.25) or word.lower() in ("solid", "none")
                or css_var(word, {"ink", "surface"}))
    return css_words(value, part, 1, 3)


def css_line_height(value: str) -> bool:
    if value.lower() == "normal":
        return True
    try:
        return re.fullmatch(r"\d+(?:\.\d+)?", value) is not None and 1 <= float(value) <= 3
    except ValueError:
        return False


def keywords(*allowed: str):
    return lambda value: value.lower() in allowed


CSS_PROPERTIES = {
    "color": lambda v: css_var(v, {"ink"}),
    "background": lambda v: css_var(v, {"surface"}),
    "background-color": lambda v: css_var(v, {"surface"}),
    "border": css_line,
    "outline": css_line,
    "outline-offset": lambda v: css_length(v, 0, 0.25),
    "border-radius": lambda v: css_words(v, lambda w: css_length(w, 0, 1)),
    "margin": lambda v: css_words(v, lambda w: css_box(w) or w.lower() == "auto"),
    "padding": lambda v: css_words(v, css_box),
    "max-width": lambda v: css_var(v, {"measure"}),
    "font-family": lambda v: css_var(v, {"font"}),
    "font-size": lambda v: css_length(v, 0.875, 4, zero_ok=False),
    "font-weight": keywords("normal", "bold", "400", "500", "600", "700"),
    "line-height": css_line_height,
    "overflow-wrap": keywords("normal", "break-word", "anywhere"),
    "text-decoration": keywords("none", "underline"),
    "box-sizing": keywords("border-box", "content-box"),
    "color-scheme": lambda v: " ".join(v.lower().split()) == "light dark",
    "text-size-adjust": keywords("100%", "auto"),
    "-webkit-text-size-adjust": keywords("100%", "auto"),
    "display": keywords("block", "inline", "inline-block", "list-item", "flow-root"),
}
for _side in ("top", "right", "bottom", "left"):
    CSS_PROPERTIES[f"margin-{_side}"] = lambda v: css_box(v) or v.lower() == "auto"
    CSS_PROPERTIES[f"padding-{_side}"] = css_box


def css_token_ok(name: str, value: str) -> bool:
    role = CSS_TOKENS[name]
    if role in ("ink", "surface"):
        return HEX_COLOR.fullmatch(value) is not None
    if role == "length":
        return css_length(value, 0, 4)
    if role == "measure":
        return css_length(value, 20, 80, zero_ok=False)
    families = [f.strip() for f in value.split(",")]
    return all(re.sub(r"""^(["'])(.*)\1$""", r"\2", f).lower() in FONT_FAMILIES for f in families)


def strip_css_comments(css: str) -> tuple[str, str | None]:
    """(css without comments, problem). Comments become a space; strings are kept intact."""
    out: list[str] = []
    quote, i = "", 0
    while i < len(css):
        c = css[i]
        if c == "\\":
            return "", "CSS escapes (\\) are not allowed"
        if quote:
            if c in "\r\n\f":
                return "", "unterminated CSS string"
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif css.startswith("/*", i):
            end = css.find("*/", i + 2)
            if end < 0:
                return "", "unterminated CSS comment"
            out.append(" ")
            i = end + 2
            continue
        out.append(c)
        i += 1
    if quote:
        return "", "unterminated CSS string"
    text = "".join(out)
    if any(not (" " <= ch <= "~" or ch in "\t\n\r\f") for ch in text):
        return "", "CSS may hold only printable ASCII outside comments"
    return text, None


def css_scan(css: str, pos: int, stops: str) -> int:
    """Index of the first stop character at or after pos outside strings and parentheses, or -1."""
    quote, depth = "", 0
    for i in range(pos, len(css)):
        c = css[i]
        if quote:
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth < 0:
                return -1
        elif not depth and c in stops:
            return i
    return -1


def css_block_end(css: str, pos: int) -> int:
    """Index of the } that closes the block opened just before pos, or -1."""
    depth = 0
    while True:
        end = css_scan(css, pos, "{}")
        if end < 0:
            return -1
        depth += 1 if css[end] == "{" else -1
        if depth < 0:
            return end
        pos = end + 1


def css_declarations(block: str) -> tuple[list[tuple[str, str]], list[str]]:
    """(name, value) pairs of a declaration block; property names are lower-cased."""
    found: list[tuple[str, str]] = []
    problems: list[str] = []
    pos = 0
    while pos <= len(block):
        end = css_scan(block, pos, ";{}")
        if end >= 0 and block[end] != ";":
            return found, problems + ["nested CSS rules or stray braces are not allowed"]
        if end < 0 and css_scan(block + ";", pos, ";") != len(block):
            return found, problems + ["unbalanced parentheses in CSS"]
        piece = block[pos:len(block) if end < 0 else end].strip()
        pos = len(block) + 1 if end < 0 else end + 1
        if not piece:
            continue
        name, sep, value = piece.partition(":")
        name, value = name.strip(), " ".join(value.split())
        if not sep or not re.fullmatch(r"--[A-Za-z0-9_-]+|-?[A-Za-z][A-Za-z-]*", name):
            problems.append(f"CSS declaration {piece[:40]!r} is not allowed")
            continue
        if "!" in value:
            problems.append(f"CSS {name}: !important is not allowed")
            continue
        found.append((name if name.startswith("--") else name.lower(), value))
    return found, problems


class Stylesheet:
    """The parts of one stylesheet the policy check needs: imports and rules in order."""

    def __init__(self, css: str) -> None:
        self.imports: list[str] = []
        # (dark-only?, selector, declarations) in source order.
        self.rules: list[tuple[bool, str, list[tuple[str, str]]]] = []
        self.problems: list[str] = []
        text, problem = strip_css_comments(css)
        if problem:
            self.problems.append(problem)
            return
        self._parse(text, 0, len(text), dark=False)

    def _parse(self, text: str, pos: int, stop: int, dark: bool) -> None:
        while True:
            while pos < stop and text[pos].isspace():
                pos += 1
            if pos >= stop:
                return
            if text[pos] == "@":
                name = re.match(r"@([A-Za-z-]*)", text[pos:]).group(1).lower()
                end = css_scan(text[:stop], pos, ";{}")
                if name == "import" and not dark and end >= 0 and text[end] == ";":
                    match = CSS_IMPORT_VALUE.fullmatch(text[pos + len("@import"):end].strip())
                    if not match:
                        self.problems.append("@import must name only one stylesheet URL")
                        return
                    self.imports.append(next(g for g in match.groups() if g is not None))
                    pos = end + 1
                    continue
                if name == "media" and not dark and end >= 0 and text[end] == "{":
                    prelude = " ".join(text[pos + len("@media"):end].lower().split())
                    close = css_block_end(text[:stop], end + 1)
                    if prelude.replace(": ", ":") != DARK_MEDIA.replace(": ", ":"):
                        self.problems.append(f"@media {prelude[:40]!r} is not allowed: only "
                                             f"@media {DARK_MEDIA}")
                        return
                    if close < 0:
                        self.problems.append("unterminated @media block")
                        return
                    self._parse(text, end + 1, close, dark=True)
                    pos = close + 1
                    continue
                self.problems.append(f"CSS at-rule @{name} is not allowed here")
                return
            open_ = css_scan(text[:stop], pos, "{};")
            if open_ < 0 or text[open_] != "{":
                self.problems.append(f"CSS {text[pos:pos + 40].strip()!r} is not a rule")
                return
            close = css_scan(text[:stop], open_ + 1, "{}")
            if close < 0 or text[close] != "}":
                self.problems.append("nested CSS rules or unterminated blocks are not allowed")
                return
            declarations, problems = css_declarations(text[open_ + 1:close])
            self.problems += problems
            self.rules.append((dark, " ".join(text[pos:open_].split()), declarations))
            pos = close + 1


def css_declaration_problem(name: str, value: str, selector: str | None) -> str | None:
    """Why a declaration may not be on the policy page, or None. selector is None for style=""."""
    if name.startswith("--"):
        if name not in CSS_TOKENS:
            return f"CSS custom property {name} is not allowed"
        if selector != ":root":
            return f"CSS custom property {name} may be set only on :root"
        if not css_token_ok(name, value):
            return f"CSS {name}: {value[:40]!r} is not allowed"
        return None
    check = CSS_PROPERTIES.get(name)
    if check is None:
        return f"CSS property {name} is not allowed on the policy page (it could hide statements)"
    if not check(value):
        return f"CSS {name}: {value[:40]!r} is not allowed on the policy page"
    return None


def relative_luminance(color: str) -> float:
    digits = color[1:]
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    channels = [int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def stylesheet_href_problem(href: str) -> str | None:
    if STYLESHEET_HREF.fullmatch(href) is None:
        return (f"stylesheet {href!r} must be a relative path to a .css file "
                f"(letters, digits, . _ - and /)")
    return None


def page_stylesheets(page: "Page", page_path: str, read) -> tuple[list[tuple[str, str, str | None]],
                                                                   list[str]]:
    """Every stylesheet the page loads, in cascade order: [(key, path, content or None)], problems.

    key is the path relative to the page's directory. read(path) returns the text or None.
    Imports come before the sheet that imports them.
    """
    sheets: list[tuple[str, str, str | None]] = []
    problems: list[str] = []
    page_dir = os.path.dirname(os.path.abspath(page_path))
    seen: set[str] = set()

    def visit(href: str, from_dir: str, where: str, depth: int) -> None:
        problem = stylesheet_href_problem(href)
        if problem:
            problems.append(f"{where}: {problem}")
            return
        path = os.path.normpath(os.path.join(from_dir, href))
        key = os.path.relpath(path, page_dir).replace(os.sep, "/")
        if key in seen:
            problems.append(f"{where}: stylesheet {key} is loaded more than once")
            return
        if depth > 4:
            problems.append(f"{where}: stylesheet imports are nested too deeply")
            return
        seen.add(key)
        content = read(path)
        if content is None:
            problems.append(f"{shown(path)}: cannot read the stylesheet the policy page loads")
            sheets.append((key, path, None))
            return
        for imported in Stylesheet(content).imports:
            visit(imported, os.path.dirname(path), shown(path), depth + 1)
        sheets.append((key, path, content))

    for element in page.elements:
        rels = element.attrs.get("rel", "").lower().split()
        if element.tag == "link" and "stylesheet" in rels:
            visit(element.attrs.get("href", ""), page_dir, shown(page_path), 0)
    return sheets, problems


def read_text_file(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def check_styles(page_path: str, page: "Page", sheets: list[tuple[str, str, str | None]],
                 problems: list[str]) -> None:
    """Every stylesheet and style="" the page uses holds only allowlisted CSS (CSS_PROPERTIES)."""
    if any(e.tag == "base" for e in page.elements):
        problems.append(f"{page_path}: the policy page may not have a <base> element")
    tokens: dict[str, dict[str, str]] = {"light": {}, "dark": {}}
    line_tokens: set[str] = set()
    scheme_set = False
    links_colored = False
    for _, path, content in sheets:
        if content is None:
            continue
        where = shown(path)
        sheet = Stylesheet(content)
        problems += [f"{where}: {p}" for p in sheet.problems]
        for dark, selector, declarations in sheet.rules:
            for name, value in declarations:
                if name in ("border", "outline"):
                    line_tokens.update(m.group(1) for m in CSS_VAR.finditer(value))
                problem = css_declaration_problem(name, value, selector)
                if problem:
                    problems.append(f"{where}: {problem} (in {selector[:40]!r})")
                elif name == "color-scheme" and selector == ":root" and not dark:
                    scheme_set = True
                elif name == "color" and selector == "a" and not dark:
                    links_colored = True
                elif name.startswith("--"):
                    for scheme in (("dark",) if dark else ("light", "dark")):
                        tokens[scheme][name] = value
    for element in page.elements:
        if "style" not in element.attrs:
            continue
        text, problem = strip_css_comments(element.attrs["style"])
        declarations, more = css_declarations(text) if not problem else ([], [problem])
        for name, value in declarations:
            if name in ("border", "outline"):
                line_tokens.update(m.group(1) for m in CSS_VAR.finditer(value))
        for problem in more + [p for p in (css_declaration_problem(n, v, None)
                                           for n, v in declarations) if p]:
            problems.append(f"{page_path}: style attribute on <{element.tag}>: {problem}")
    if sheets and not scheme_set:
        problems.append(f"{page_path}: the stylesheet must set color-scheme: light dark on :root "
                        f"(the browser's own colors must follow the color tokens' scheme)")
    # An unset token makes color or background fall back to the browser's own (UA_COLORS).
    for scheme, defined in tokens.items():
        colors = {"ink": list(UA_COLORS[scheme]["ink"]), "surface": list(UA_COLORS[scheme]["surface"])}
        if not links_colored:
            colors["ink"] += UA_LINK_COLORS[scheme]
        for name, role in CSS_TOKENS.items():
            if role in colors and name in defined:
                colors[role].append(defined[name])
        for ink in colors["ink"]:
            for surface in colors["surface"]:
                ratio = contrast(ink, surface)
                if ratio < MIN_TEXT_CONTRAST:
                    problems.append(f"{page_path}: in the {scheme} scheme, text color {ink} on "
                                    f"background {surface} has contrast {ratio:.2f} "
                                    f"(needs {MIN_TEXT_CONTRAST}:1 for text)")
        for name in sorted(line_tokens):
            line = defined.get(name)
            if line is None or CSS_TOKENS.get(name) not in ("ink", "surface"):
                continue
            for surface in colors["surface"]:
                ratio = contrast(line, surface)
                if ratio < MIN_NON_TEXT_CONTRAST:
                    problems.append(f"{page_path}: in the {scheme} scheme, border or outline color "
                                    f"{name} ({line}) on background {surface} has contrast "
                                    f"{ratio:.2f} (needs {MIN_NON_TEXT_CONTRAST:g}:1 for "
                                    f"non-text)")


def shown(path: str) -> str:
    """A path as printed: relative to the working directory when it lies under it."""
    relative = os.path.relpath(path)
    return path if relative.startswith("..") else relative


def normalize(text: str) -> str:
    return " ".join(text.split())


def fact_value(value: object) -> str:
    """The data-value spelling of a fact: the raw string, or compact JSON."""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def parse_date(value: str) -> datetime.date | None:
    if not DATE.fullmatch(value):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


# -- page ---------------------------------------------------------------------

class Element:
    def __init__(self, tag: str, attrs: dict[str, str], start: int) -> None:
        self.tag = tag
        self.attrs = attrs
        self.start = start
        self.text = ""
        self.parent: Element | None = None

    def ancestors(self) -> list["Element"]:
        chain, node = [], self.parent
        while node is not None:
            chain.append(node)
            node = node.parent
        return chain


class PolicyParser(HTMLParser):
    """Collects every element with its text, in document order, using an open-element stack."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[Element] = []
        self.elements: list[Element] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, {k: (v if v is not None else "") for k, v in reversed(attrs)},
                          len(self.parts))
        element.parent = self.stack[-1] if self.stack else None
        self.elements.append(element)
        if tag in VOID_ELEMENTS:
            return
        self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_ELEMENTS:
            self._close(self.stack.pop())

    def handle_endtag(self, tag: str) -> None:
        if not any(open_element.tag == tag for open_element in self.stack):
            return
        while self.stack:
            element = self.stack.pop()
            self._close(element)
            if element.tag == tag:
                return

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def close(self) -> None:
        super().close()
        while self.stack:
            self._close(self.stack.pop())

    def _close(self, element: Element) -> None:
        element.text = normalize("".join(self.parts[element.start:]))


class Page:
    def __init__(self, source: str) -> None:
        parser = PolicyParser()
        parser.feed(source)
        parser.close()
        self.source = source
        self.elements = parser.elements
        self.all_text = normalize("".join(parser.parts))
        body = self.first("body")
        self.text = body.text if body else normalize("".join(parser.parts))
        self.statements = [(e.attrs["data-fact"], e.attrs.get("data-value"), e.text, e)
                           for e in self.elements if "data-fact" in e.attrs]

    def first(self, tag: str) -> Element | None:
        return next((e for e in self.elements if e.tag == tag), None)

    def markers(self, name: str) -> list[Element]:
        return [e for e in self.elements if e.attrs.get("data-policy") == name]

    def text_sha256(self, stylesheets: list[tuple[str, str, str | None]]) -> str | None:
        """The policy hash. stylesheets is page_stylesheets(...)[0]: their text counts too."""
        main = [e for e in self.elements if e.tag == "main"]
        if len(main) != 1:
            return None
        # Every reader-visible word counts, wherever it is (head, header, nav, main, footer, or
        # after </body>), and so does every attribute, which can show text or hide it.
        title = self.first("title")
        descriptions = [e.attrs.get("content", "") for e in self.elements
                        if e.tag == "meta" and e.attrs.get("name", "").lower() == "description"]
        hrefs = [e.attrs["href"] for e in self.elements if e.tag == "a" and "href" in e.attrs]
        elements = [[e.tag, [[k, normalize(v)] for k, v in reversed(list(e.attrs.items()))]]
                    for e in self.elements]
        payload = json.dumps({"text": self.all_text,
                              "elements": elements,
                              "facts": [[key, value] for key, value, _, _ in self.statements],
                              "title": title.text if title else "",
                              "description": [normalize(d) for d in descriptions],
                              "hrefs": hrefs,
                              "stylesheets": [[key, content] for key, _, content in stylesheets]},
                             ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def effective_date(self) -> tuple[str | None, str | None]:
        """(date, problem) from the single data-policy="effective-date" marker."""
        markers = self.markers("effective-date")
        if not markers:
            return None, 'missing effective date marker (data-policy="effective-date")'
        if len(markers) > 1:
            return None, "more than one effective date marker"
        marker = markers[0]
        if marker.tag != "time":
            return None, 'effective date marker must be a <time datetime="YYYY-MM-DD">'
        value = marker.attrs.get("datetime", "")
        if parse_date(value) is None:
            return None, f"effective date marker has an invalid datetime {value!r} (want YYYY-MM-DD)"
        if not marker.text:
            return None, "effective date marker has no text"
        date = parse_date(value)
        shown_date = f"{date.day} {date.strftime('%B %Y')}"
        if marker.text != shown_date:
            return None, (f"effective date marker reads {marker.text!r} but its datetime is "
                          f"{value} (expected the text {shown_date!r})")
        return value, None

    def single_text(self, name: str) -> tuple[str | None, str | None]:
        markers = self.markers(name)
        if not markers:
            return None, f'missing {name} marker (data-policy="{name}")'
        texts = {m.text for m in markers}
        if len(texts) > 1 or "" in texts:
            return None, f"{name} markers are empty or disagree"
        return texts.pop(), None


def policy_sha256(page_path: str) -> str | None:
    """The policy hash of the page file and the stylesheets it loads, read from disk."""
    page = Page(read_text_file(page_path) or "")
    return page.text_sha256(page_stylesheets(page, page_path, read_text_file)[0])


def load_page(path: str, problems: list[str]) -> Page | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return Page(handle.read())
    except OSError as error:
        problems.append(f"{path}: cannot read the page ({error.strerror or error})")
    except UnicodeDecodeError:
        problems.append(f"{path}: the page is not UTF-8")
    return None


# -- facts and record -----------------------------------------------------------

def load_json(path: str, what: str, problems: list[str]) -> dict | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        problems.append(f"{path}: {what} not found")
        return None
    except OSError as error:
        problems.append(f"{path}: cannot read the {what} ({error.strerror or error})")
        return None
    except (ValueError, UnicodeDecodeError) as error:
        problems.append(f"{path}: the {what} is not valid JSON ({error})")
        return None
    if not isinstance(data, dict):
        problems.append(f"{path}: the {what} must be a JSON object")
        return None
    return data


def load_facts(path: str, problems: list[str]) -> dict[str, str] | None:
    data = load_json(path, "facts file", problems)
    if data is None:
        return None
    version = data.get("schemaVersion")
    if type(version) is not int or version != SCHEMA_VERSION:
        problems.append(f"{path}: unsupported schemaVersion {version!r} (expected {SCHEMA_VERSION})")
        return None
    return {key: fact_value(value) for key, value in data.items() if key != "schemaVersion"}


def load_record(path: str, problems: list[str]) -> dict | None:
    if not os.path.exists(path):
        problems.append(f"{path}: policy record not found; create it with check_privacy.py --update")
        return None
    data = load_json(path, "policy record", problems)
    if data is None:
        return None
    bad = [field for field in RECORD_FIELDS
           if not isinstance(data.get(field), str) or not data[field].strip()]
    if bad:
        problems.append(f"{path}: policy record is missing or has empty fields: {', '.join(bad)}")
        return None
    if parse_date(data["effectiveDate"]) is None:
        problems.append(f"{path}: policy record has an invalid effectiveDate {data['effectiveDate']!r}")
        return None
    if not SHA256.fullmatch(data["textSha256"]):
        problems.append(f"{path}: policy record has an invalid textSha256")
        return None
    return data


# -- checks ---------------------------------------------------------------------

def check_facts(page_path: str, page: Page, facts: dict[str, str], problems: list[str]) -> None:
    stated: dict[str, set[str]] = {}
    for key, value, text, _ in page.statements:
        if not key:
            problems.append(f"{page_path}: a data-fact statement has an empty key")
            continue
        if value is None:
            problems.append(f"{page_path}: statement for fact '{key}' has no data-value")
            continue
        if not text:
            problems.append(f"{page_path}: statement for fact '{key}' has no text")
        stated.setdefault(key, set()).add(value)
    for key, values in stated.items():
        if len(values) > 1:
            problems.append(f"{page_path}: conflicting statements for fact '{key}': "
                            + ", ".join(sorted(values)))
        if key not in facts:
            problems.append(f"{page_path}: page states fact '{key}' that the facts file lacks")
    for key, expected in facts.items():
        values = stated.get(key)
        if not values:
            problems.append(f"{page_path}: no statement for fact '{key}' "
                            f'(add data-fact="{key}" data-value="{html.escape(expected)}")')
        elif expected not in values or len(values) > 1:
            problems.append(f"{page_path}: fact '{key}': facts file says {expected!r}, "
                            f"page says {', '.join(repr(v) for v in sorted(values))}")
    unworded = {(key, value) for key, value in facts.items() if approved_wording(key, value) is None}
    for key, value, text, element in page.statements:
        if not key or value is None:
            continue
        wording = approved_wording(key, value)
        if wording is None:
            unworded.add((key, value))
        elif text and text != wording:
            problems.append(f"{page_path}: statement for fact '{key}' = {value!r} reads {text!r}; "
                            f"the approved wording is {wording!r}")
        problem = statement_visibility(element, page)
        if problem:
            problems.append(f"{page_path}: statement for fact '{key}' {problem}")
    if any(e.tag == "style" for e in page.elements):
        problems.append(f"{page_path}: the policy page may not have a <style> element "
                        f"(it could hide statements); use the shared stylesheet")
    for key, value in sorted(unworded):
        problems.append(f"{page_path}: fact '{key}' = {value!r} has no approved wording yet; "
                        f"add it to APPROVED_WORDING in scripts/check_privacy.py")


def approved_wording(key: str, value: str) -> str | None:
    if key in TEMPLATE_WORDING:
        if not PACKAGE_NAME.fullmatch(value):
            return None
        return TEMPLATE_WORDING[key].format(value=value)
    return APPROVED_WORDING.get(key, {}).get(value)


def statement_visibility(element: Element, page: Page) -> str | None:
    """Why a statement may not be what a reader sees, or None."""
    if element.tag not in STATEMENT_TAGS:
        return f"must be a <p> or <li>, not <{element.tag}>"
    extra = sorted(set(element.attrs) - STATEMENT_ATTRS)
    if extra:
        return f"has attributes that may change what is shown: {', '.join(extra)}"
    ancestors = element.ancestors()
    if not any(a.tag == "main" for a in ancestors):
        return "must be inside <main>"
    for ancestor in ancestors:
        if ancestor.tag not in STATEMENT_ANCESTOR_TAGS:
            return f"is inside <{ancestor.tag}>, which may hide it"
        extra = sorted(set(ancestor.attrs) - STATEMENT_ANCESTOR_ATTRS)
        if extra:
            return (f"is inside <{ancestor.tag}> with attributes that may hide it: "
                    f"{', '.join(extra)}")
    for other in page.elements:
        if element in other.ancestors():
            if other.tag not in STATEMENT_CHILD_TAGS:
                return f"contains <{other.tag}>; only <code>, <em> and <strong> are allowed"
            if other.attrs:
                return f"contains <{other.tag}> with attributes: {', '.join(sorted(other.attrs))}"
    return None


def check_markers(page_path: str, page: Page, record: dict, problems: list[str]) -> None:
    publisher, problem = page.single_text("publisher")
    if problem:
        problems.append(f"{page_path}: {problem}")
    elif publisher != record["publisher"]:
        problems.append(f"{page_path}: publisher marker reads {publisher!r}, "
                        f"the record says {record['publisher']!r}")
    developer, problem = page.single_text("play-developer")
    if problem:
        problems.append(f"{page_path}: {problem}")
    elif developer != record["playDeveloper"]:
        problems.append(f"{page_path}: play-developer marker reads {developer!r}, "
                        f"the record says {record['playDeveloper']!r}")
    phrase = f"{record['publisher']} (published on Google Play as {record['playDeveloper']})"
    if phrase not in page.text:
        problems.append(f"{page_path}: page text must contain {phrase!r}")

    email = record["contactEmail"]
    contacts = page.markers("contact")
    if not contacts:
        problems.append(f'{page_path}: missing contact marker (data-policy="contact")')
    for contact in contacts:
        if contact.tag != "a":
            problems.append(f"{page_path}: contact marker must be an <a> element")
        elif contact.text != email:
            problems.append(f"{page_path}: contact marker reads {contact.text!r}, "
                            f"the record says {email!r}")
        elif contact.attrs.get("href") != f"mailto:{email}":
            problems.append(f"{page_path}: contact marker href is {contact.attrs.get('href')!r}, "
                            f"expected 'mailto:{email}'")

    _, problem = page.effective_date()
    if problem:
        problems.append(f"{page_path}: {problem}")


def check_single_email(page_path: str, page: Page, email: str, problems: list[str]) -> None:
    """The contact address is the only email, and the only mailto: target, anywhere in the page."""
    decoded = html.unescape(page.source)
    haystack = decoded + "\n" + unquote(decoded)
    others = sorted({found for found in EMAIL.findall(haystack) if found.lower() != email.lower()})
    targets = sorted({target for target in MAILTO.findall(haystack)
                      if unquote(target).lower() != email.lower()})
    if others or targets:
        found = ", ".join(sorted(set(others) | {f"mailto:{t}" for t in targets}))
        problems.append(f"{page_path}: the contact {email} must be the only email on the page "
                        f"(found {found})")


def check_record(page_path: str, record_path: str, page: Page, sheets: list, record: dict,
                 problems: list[str]) -> None:
    date, problem = page.effective_date()
    digest = page.text_sha256(sheets)
    if digest is None:
        problems.append(f"{page_path}: the page must have exactly one <main>")
        return
    if problem:
        return  # reported by check_markers
    recorded = record["effectiveDate"]
    if date < recorded:
        problems.append(f"{page_path}: effective date {date} is earlier than the recorded {recorded}")
    elif date > recorded:
        problems.append(f"{record_path}: effective date {date} is not recorded (record has "
                        f"{recorded}); run python3 scripts/check_privacy.py --update")
    elif digest != record["textSha256"]:
        problems.append(f"{page_path}: policy text changed without a new effective date "
                        f"(still {date}); bump the date, then run check_privacy.py --update")


class PreviousRevisionError(Exception):
    """The previous revision could not be read for a reason other than "no record there"."""


def git(repo: str, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        raise PreviousRevisionError(f"cannot run git ({error})") from error


def read_previous(base: str, path: str) -> tuple[str | None, str]:
    """(content, path in tree) of path at revision base; content is None when base lacks it.

    Raises PreviousRevisionError when base cannot be resolved (unknown, not fetched, a
    shallow clone without it), path is outside a git work tree, or the blob cannot be read.
    """
    if not base or base.startswith("-"):
        raise PreviousRevisionError(f"invalid base revision {base!r}")
    directory = os.path.dirname(os.path.abspath(path))
    top = git(directory, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise PreviousRevisionError(f"{shown(path)} is not in a git work tree")
    toplevel = os.path.realpath(top.stdout.decode("utf-8").strip())
    relative = os.path.relpath(os.path.realpath(path), toplevel)
    if relative.startswith(".."):
        raise PreviousRevisionError(f"{shown(path)} is outside the git work tree {toplevel}")
    tree_path = relative.replace(os.sep, "/")
    commit = git(toplevel, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    if commit.returncode != 0:
        raise PreviousRevisionError(f"base revision {base!r} is not available (not fetched, "
                                    f"or a shallow clone without it)")
    sha = commit.stdout.decode("ascii").strip()
    listing = git(toplevel, "ls-tree", "-z", sha, "--", tree_path)
    if listing.returncode != 0:
        raise PreviousRevisionError(f"cannot list {tree_path} at {base}")
    if not listing.stdout:
        return None, tree_path
    blob = git(toplevel, "cat-file", "blob", f"{sha}:{tree_path}")
    if blob.returncode != 0:
        raise PreviousRevisionError(f"cannot read {tree_path} at {base}")
    try:
        return blob.stdout.decode("utf-8"), tree_path
    except UnicodeDecodeError as error:
        raise PreviousRevisionError(f"{tree_path} at {base} is not UTF-8") from error


def check_previous(page_path: str, record_path: str, page: Page, sheets: list, base: str,
                   problems: list[str], notes: list[str]) -> None:
    """If the policy text differs from base's, the effective date must be later than base's.

    The previous hash is computed from base's page with this checker, so the committed
    textSha256 in the same change is never trusted. Only a base without a policy record (the
    first publish) is allowed without comparison; every other read failure fails.
    """
    try:
        record_source, record_tree = read_previous(base, record_path)
        if record_source is None:
            notes.append(f"{record_path}: {base} has no {record_tree} yet; first publish of the "
                         f"policy, so there is no previous effective date to compare")
            return
        page_source, page_tree = read_previous(base, page_path)
    except PreviousRevisionError as error:
        problems.append(f"{record_path}: cannot read the previous revision: {error}")
        return
    where = f"{record_tree} at {base}"
    try:
        previous = json.loads(record_source)
    except ValueError as error:
        problems.append(f"{record_path}: the previous record ({where}) is not valid JSON ({error})")
        return
    previous_date = previous.get("effectiveDate") if isinstance(previous, dict) else None
    if not isinstance(previous_date, str) or parse_date(previous_date) is None:
        problems.append(f"{record_path}: the previous record ({where}) has no valid effectiveDate")
        return
    if page_source is None:
        problems.append(f"{page_path}: {base} has a policy record but no {page_tree}")
        return
    previous_page = Page(page_source)

    def read_base(path: str) -> str | None:
        return read_previous(base, path)[0]

    try:
        previous_sheets, _ = page_stylesheets(previous_page, page_path, read_base)
    except PreviousRevisionError as error:
        problems.append(f"{record_path}: cannot read the previous revision's stylesheets: {error}")
        return
    previous_digest = previous_page.text_sha256(previous_sheets)
    if previous_digest is None:
        problems.append(f"{page_path}: the previous page ({page_tree} at {base}) has no single <main>")
        return
    date, problem = page.effective_date()
    digest = page.text_sha256(sheets)
    if problem or digest is None:
        return  # reported by check_markers / check_record
    if digest != previous_digest and date <= previous_date:
        problems.append(f"{page_path}: policy text changed since {base} without a later effective "
                        f"date (still {date}, previous {previous_date}); bump the date, then run "
                        f"check_privacy.py --update")
    elif date < previous_date:
        problems.append(f"{page_path}: effective date {date} is earlier than {previous_date} at {base}")


def check_privacy(facts_path: str, page_path: str = DEFAULT_PAGE,
                  record_path: str = DEFAULT_RECORD, base: str | None = None,
                  notes: list[str] | None = None) -> list[str]:
    """Problems found. With base, also compare with that revision (see check_previous)."""
    problems: list[str] = []
    notes = [] if notes is None else notes
    facts_path, page_path, record_path = shown(facts_path), shown(page_path), shown(record_path)
    facts = load_facts(facts_path, problems)
    page = load_page(page_path, problems)
    record = load_record(record_path, problems)
    if page is None:
        return problems
    sheets, sheet_problems = page_stylesheets(page, page_path, read_text_file)
    problems += sheet_problems
    if facts is not None:
        check_facts(page_path, page, facts, problems)
    check_styles(page_path, page, sheets, problems)
    if record is not None:
        check_markers(page_path, page, record, problems)
        check_single_email(page_path, page, record["contactEmail"], problems)
        check_record(page_path, record_path, page, sheets, record, problems)
    if base is not None:
        check_previous(page_path, record_path, page, sheets, base, problems, notes)
    return problems


def update_record(page_path: str = DEFAULT_PAGE,
                  record_path: str = DEFAULT_RECORD) -> tuple[bool, list[str]]:
    """Rewrite or create the record from the page. Returns (ok, messages)."""
    problems: list[str] = []
    page_path, record_path = shown(page_path), shown(record_path)
    page = load_page(page_path, problems)
    if page is None:
        return False, problems
    date, problem = page.effective_date()
    sheets, problems = page_stylesheets(page, page_path, read_text_file)
    digest = page.text_sha256(sheets)
    if problem:
        problems.append(f"{page_path}: {problem}")
    if digest is None:
        problems.append(f"{page_path}: the page must have exactly one <main>")
    if problems:
        return False, problems

    if os.path.exists(record_path):
        record = load_record(record_path, problems)
        if record is None:
            return False, problems
        if date <= record["effectiveDate"]:
            return False, [f"{record_path}: refusing to update: the page's effective date {date} "
                           f"is not later than the recorded {record['effectiveDate']}"]
        record["effectiveDate"] = date
        record["textSha256"] = digest
        action = "updated"
    else:
        publisher, p1 = page.single_text("publisher")
        developer, p2 = page.single_text("play-developer")
        contacts = page.markers("contact")
        p3 = None if contacts else 'missing contact marker (data-policy="contact")'
        problems += [f"{page_path}: {p}" for p in (p1, p2, p3) if p]
        if problems:
            return False, problems
        record = {"publisher": publisher, "playDeveloper": developer,
                  "contactEmail": contacts[0].text, "effectiveDate": date, "textSha256": digest}
        action = "created"
    with open(record_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return True, [f"{record_path}: {action} (effectiveDate {date}, textSha256 {digest})"]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--facts", help="the app's docs/privacy-facts.json")
    parser.add_argument("--page", default=DEFAULT_PAGE, help="the policy page")
    parser.add_argument("--record", default=DEFAULT_RECORD, help="the policy record")
    parser.add_argument("--base", help="a git revision to compare with (the PR base or HEAD^1): "
                             "if the policy text differs from it, the effective date must be "
                             "later than its record's; fails if it cannot be read")
    parser.add_argument("--update", action="store_true",
                        help="rewrite the record's effectiveDate and textSha256 from the page")
    args = parser.parse_args(argv)
    if args.update:
        ok, messages = update_record(args.page, args.record)
        print("\n".join(messages))
        return 0 if ok else 1
    if not args.facts:
        parser.error("--facts is required unless --update is given")
    notes: list[str] = []
    problems = check_privacy(args.facts, args.page, args.record, args.base, notes)
    if notes:
        print("\n".join(notes))
    if problems:
        print("\n".join(problems))
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
