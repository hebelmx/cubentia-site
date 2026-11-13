#!/usr/bin/env python3
"""Check the static site for broken internal links and privacy or branding problems.

Standard library only. HTML validation is done separately by the Nu Html Checker
(see scripts/check.sh); this script covers the rest:

* every internal link and fragment resolves to a file (and an id) under the site root;
* no resource comes from another origin (src, srcset, <link href>, ping, action, formaction,
  xlink:href, meta refresh, CSS url(), image-set() and @import); data: URIs are allowed;
* no <script>, no on* event-handler attribute, no javascript:/vbscript: URL, no cookie meta;
* .html, .htm, .xhtml and .svg files (any case) all get these page checks;
* root-relative paths stay inside the GitHub Pages project path /cubentia-site/;
* 404.html, which is served at any missing path, uses only absolute site paths;
* the word "Rubik" never appears in page text (URLs are exempt).

Usage: python3 scripts/check_site.py [SITE_DIR]   (default: site)
Prints each problem as "file: message" and exits 1 if there is any; prints "OK" otherwise.
"""

from __future__ import annotations

import os
import posixpath
import re
import sys
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

PROJECT_PREFIX = "/cubentia-site/"
NOT_FOUND_PAGE = "404.html"

# Attributes whose value is a resource the browser fetches on its own.
RESOURCE_ATTRS = {"src", "poster", "data", "background", "action", "formaction", "xlink:href"}
# Attributes whose value is human-readable page text.
TEXT_ATTRS = {
    "title", "alt", "aria-label", "placeholder", "aria-description",
    "value", "label", "aria-roledescription", "abbr",
}
# Elements whose content is not page text.
NON_TEXT_ELEMENTS = {"style", "script"}

URL_IN_TEXT = re.compile(r"(?:[a-z][a-z0-9+.-]*:)?//\S+|\b[\w.-]+\.[a-z]{2,}/\S*", re.I)
RUBIK = re.compile(r"rubik", re.I)
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
CSS_URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)\s]*))\s*\)""", re.I)
CSS_IMPORT_STRING = re.compile(r"""@import\s+(?:"([^"]*)"|'([^']*)')""", re.I)
CSS_IMAGE_SET = re.compile(r"(?:-webkit-)?image-set\(", re.I)
CSS_STRING = re.compile(r""""([^"]*)"|'([^']*)'""")
CSS_TYPE = re.compile(r"""type\(\s*(?:"[^"]*"|'[^']*')\s*\)""", re.I)
META_REFRESH_URL = re.compile(r"""^\s*\d*(?:\.\d*)?\s*[;,]?\s*(?:url\s*=\s*)?['"]?([^'"]*)""", re.I)
PAGE_EXTENSIONS = (".html", ".htm", ".xhtml", ".svg")
SCRIPT_SCHEMES = ("javascript", "vbscript")


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
        found += [next(g for g in m.groups() if g is not None) for m in CSS_STRING.finditer(body)]
    return found


def contains_rubik(text: str) -> bool:
    return bool(RUBIK.search(URL_IN_TEXT.sub(" ", text)))


class PageParser(HTMLParser):
    """Collects ids, links, resources, inline CSS, text and forbidden constructs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[str] = []  # <a>/<area> href: navigation
        self.resources: list[str] = []  # fetched by the browser: must be same-origin
        self.css_blocks: list[str] = []
        self.problems: list[str] = []
        self._skip_depth = 0
        self._style_buffer: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs)
        if tag in NON_TEXT_ELEMENTS:
            self._skip_depth += 1
            if tag == "style":
                self._style_buffer = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs)

    def _start(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: (value or "") for name, value in attrs}
        if tag == "script":
            self.problems.append("<script> is not allowed")
        if tag in {"iframe", "embed", "object", "frame"}:
            self.problems.append(f"<{tag}> embeds are not allowed")
        for name, value in values.items():
            if name.startswith("on"):
                self.problems.append(f'event-handler attribute {name}="..." on <{tag}> is not allowed')
            if name == "id" and value:
                self.ids.add(value)
            if name in TEXT_ATTRS and contains_rubik(value):
                self.problems.append(f'"Rubik" in {name} of <{tag}>')
            if name == "style" and value:
                self.css_blocks.append(value)
        if tag == "a" and "name" in values and values["name"]:
            self.ids.add(values["name"])
        if tag == "meta":
            if values.get("http-equiv", "").strip().lower() == "set-cookie":
                self.problems.append('<meta http-equiv="set-cookie"> is not allowed')
            if values.get("http-equiv", "").strip().lower() == "refresh":
                match = META_REFRESH_URL.match(values.get("content", ""))
                if match and match.group(1).strip():
                    self.resources.append(match.group(1).strip())
            if contains_rubik(values.get("content", "")):
                self.problems.append('"Rubik" in content of <meta>')
        if tag in {"a", "area"} and "href" in values:
            self.links.append(values["href"])
        elif "href" in values:  # <link>, <base>, ...: fetched or affects fetching
            self.resources.append(values["href"])
        for name in RESOURCE_ATTRS & values.keys():
            self.resources.append(values[name])
        if "ping" in values:
            self.resources.extend(values["ping"].split())
        for name in ("srcset", "imagesrcset"):
            if name in values:
                for candidate in values[name].split(","):
                    candidate = candidate.strip()
                    if candidate:
                        self.resources.append(candidate.split()[0])

    def handle_endtag(self, tag: str) -> None:
        if tag in NON_TEXT_ELEMENTS and self._skip_depth:
            self._skip_depth -= 1
            if tag == "style" and self._style_buffer is not None:
                self.css_blocks.append("".join(self._style_buffer))
                self._style_buffer = None

    def handle_data(self, data: str) -> None:
        if self._style_buffer is not None:
            self._style_buffer.append(data)
            return
        if self._skip_depth:
            return
        if contains_rubik(data):
            self.problems.append(f'"Rubik" in page text: {data.strip()[:60]!r}')


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
        parts = urlsplit(url.strip())
        raw = url.strip().lower()
        if raw.startswith(tuple(f"{s}:" for s in SCRIPT_SCHEMES)) or parts.scheme.lower() in SCRIPT_SCHEMES:
            self._report(rel_file, f"script URL {url!r} (javascript:/vbscript:) is not allowed")
            return None
        if parts.scheme or url.strip().startswith("//"):
            if kind == "link":
                return ""  # external navigation is allowed
            if parts.scheme.lower() == "data":
                return ""  # inline data is not fetched from another origin
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
        return target

    def _check_fragment(self, rel_file: str, url: str, target: str) -> None:
        fragment = unquote(urlsplit(url.strip()).fragment)
        if not fragment or not target.lower().endswith(PAGE_EXTENSIONS):
            return
        page = self._page(target)
        if page is None or fragment not in page.ids:
            self._report(rel_file, f"broken fragment {url!r}: no id {fragment!r} in {target}")

    def _check_css(self, rel_file: str, css: str) -> None:
        css = CSS_COMMENT.sub("", css)
        urls = [next(g for g in m.groups() if g is not None) for m in CSS_URL.finditer(css)]
        urls += [next(g for g in m.groups() if g is not None) for m in CSS_IMPORT_STRING.finditer(css)]
        urls += css_image_set_strings(css)
        for url in urls:
            url = url.strip()
            if not url or url.startswith("#"):
                continue
            if url.lower().startswith("data:"):
                continue  # inline data is not fetched from another origin
            self._resolve(rel_file, url, "CSS resource")

    # -- entry point -------------------------------------------------------

    def run(self) -> list[str]:
        if not os.path.isdir(self.site_dir):
            self.problems.append(f"{self.display_root}: site directory does not exist")
            return self.problems
        files: list[str] = []
        for root, dirs, names in os.walk(self.site_dir):
            dirs.sort()
            for name in sorted(names):
                files.append(self._rel(os.path.join(root, name)))
        for rel_file in files:
            if rel_file.lower().endswith(PAGE_EXTENSIONS):
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
                    self._resolve(rel_file, url, "resource")
                for css in page.css_blocks:
                    self._check_css(rel_file, css)
            elif rel_file.lower().endswith(".css"):
                try:
                    with open(os.path.join(self.site_dir, rel_file), encoding="utf-8") as handle:
                        css = handle.read()
                except (OSError, UnicodeDecodeError):
                    self._report(rel_file, "cannot be read as UTF-8")
                    continue
                self._check_css(rel_file, css)
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
