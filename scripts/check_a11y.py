#!/usr/bin/env python3
"""Check the accessibility structure of every page on the static site.

Standard library only; no browser and no network. It covers what static HTML can prove about
WCAG 2.2 AA. Keyboard flow, zoom, reflow and the rendered light and dark themes are checked by
hand on the pull request. Color contrast is checked by scripts/check_privacy.py, which reads
the one stylesheet every page loads.

Every .html file under the site directory must have:

* <html lang="en"> and a non-empty <title>;
* a viewport meta that does not block zoom (no user-scalable=no or 0, no maximum-scale);
* exactly one <header>, <main> and <footer>, and <main id="main">;
* a skip link: the first focusable element is an <a href="#main"> with text;
* exactly one <h1>, the first heading is the <h1>, and heading levels never skip a level
  going down (h2 then h4 fails);
* an alt attribute on every <img>, and an href and non-empty text (or an <img> alt) on
  every <a>;
* no tabindex other than 0 or -1;
* exactly one stylesheet, the site's styles.css (a relative path to it, or
  /cubentia-site/styles.css), no <style> element and no style="" attribute, so all of a
  page's CSS is in the stylesheet the contrast check reads.

Usage: python3 scripts/check_a11y.py [SITE_DIR]   (default: site)
Prints each problem as "file: message" and exits 1 if there is any; prints "OK" otherwise.
"""

from __future__ import annotations

import os
import posixpath
import re
import sys
from html.parser import HTMLParser

PROJECT_PREFIX = "/cubentia-site/"
STYLESHEET = "styles.css"
LANDMARKS = ("header", "main", "footer")
HEADING = re.compile(r"h([1-6])")
ALWAYS_FOCUSABLE = {"button", "select", "textarea", "iframe", "summary"}


class Link:
    def __init__(self, attrs: dict[str, str | None]) -> None:
        self.attrs = attrs
        self.text: list[str] = []

    def label(self) -> str:
        return " ".join("".join(self.text).split())


class A11yParser(HTMLParser):
    """Collects the structure the checks need, in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang: str | None = None
        self.html_seen = False
        self.title: list[str] | None = None
        self.in_title = False
        self.viewports: list[str] = []
        self.landmarks: dict[str, int] = {name: 0 for name in LANDMARKS}
        self.main_ids: list[str | None] = []
        self.headings: list[int] = []
        self.links: list[Link] = []
        self.first_focusable: tuple[str, Link | None] | None = None
        self.stylesheets: list[str] = []
        self.style_elements = 0
        self.problems: list[str] = []
        self._open_links: list[Link] = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag == "a":
            self.handle_endtag(tag)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {name.lower(): value for name, value in attrs}
        if tag == "html" and not self.html_seen:
            self.html_seen = True
            self.lang = attr.get("lang")
        elif tag == "title":
            self.in_title = True
            if self.title is None:
                self.title = []
        elif tag == "meta" and (attr.get("name") or "").lower() == "viewport":
            self.viewports.append(attr.get("content") or "")
        elif tag in self.landmarks:
            self.landmarks[tag] += 1
            if tag == "main":
                self.main_ids.append(attr.get("id"))
        elif tag == "link" and "stylesheet" in (attr.get("rel") or "").lower().split():
            self.stylesheets.append(attr.get("href") or "")
        elif tag == "style":
            self.style_elements += 1
        elif tag == "img":
            if "alt" not in attr:
                self.problems.append(f"<img src={attr.get('src')!r}> has no alt attribute")
            elif self._open_links and attr["alt"]:
                self._open_links[-1].text.append(attr["alt"])
        heading = HEADING.fullmatch(tag)
        if heading:
            self.headings.append(int(heading.group(1)))
        if "style" in attr:
            self.problems.append(f"<{tag}> has a style attribute: put styles in {STYLESHEET} "
                                 f"so the contrast check covers them")
        if "tabindex" in attr and (attr["tabindex"] or "").strip() not in ("0", "-1"):
            self.problems.append(f"<{tag}> has tabindex={attr['tabindex']!r}: only 0 or -1 is allowed")
        link = None
        if tag == "a":
            link = Link(attr)
            self.links.append(link)
            self._open_links.append(link)
        if self.first_focusable is None and self._focusable(tag, attr):
            self.first_focusable = (tag, link)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        elif tag == "a" and self._open_links:
            self._open_links.pop()

    def handle_data(self, data: str) -> None:
        if self.in_title and self.title is not None:
            self.title.append(data)
        for link in self._open_links:
            link.text.append(data)

    @staticmethod
    def _focusable(tag: str, attr: dict[str, str | None]) -> bool:
        tabindex = (attr.get("tabindex") or "").strip()
        if tabindex == "-1":
            return False
        if re.fullmatch(r"\d+", tabindex):
            return True
        if tag in ("a", "area"):
            return "href" in attr
        if tag == "input":
            return (attr.get("type") or "").lower() != "hidden"
        return tag in ALWAYS_FOCUSABLE


def viewport_problem(content: str) -> str | None:
    settings: dict[str, str] = {}
    collapsed = re.sub(r"\s*=\s*", "=", content.strip())
    for part in re.split(r"[,;\s]+", collapsed):
        name, _, value = part.partition("=")
        settings[name.strip().lower()] = value.strip().lower()
    if settings.get("user-scalable") in ("no", "0"):
        return f"the viewport meta blocks zoom (user-scalable={settings['user-scalable']})"
    if "maximum-scale" in settings:
        return f"the viewport meta limits zoom (maximum-scale={settings['maximum-scale']})"
    return None


def stylesheet_ok(href: str, page_rel: str) -> bool:
    """href names the site's one stylesheet: relative to the page, or under /cubentia-site/."""
    if href == PROJECT_PREFIX + STYLESHEET:
        return True
    if not href or "://" in href or href.startswith(("/", "//")) or re.search(r"[?#\\]", href):
        return False
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(page_rel), href))
    return resolved == STYLESHEET


def check_page(page_rel: str, html: str) -> list[str]:
    parser = A11yParser()
    parser.feed(html)
    parser.close()
    problems = list(parser.problems)

    if parser.lang is None:
        problems.append('<html> has no lang attribute (needs lang="en")')
    elif parser.lang != "en":
        problems.append(f'<html lang={parser.lang!r}>: the site is English, lang must be "en"')
    if parser.title is None or not "".join(parser.title).strip():
        problems.append("the page has no title, or the title is empty")

    if not parser.viewports:
        problems.append("the page has no viewport meta")
    for content in parser.viewports:
        problem = viewport_problem(content)
        if problem:
            problems.append(problem)

    for name, count in parser.landmarks.items():
        if count != 1:
            problems.append(f"the page has {count} <{name}> elements (needs exactly one)")
    if parser.main_ids and parser.main_ids[0] != "main":
        problems.append('<main> must have id="main" (the skip link target)')

    first = parser.first_focusable
    if first is None:
        problems.append('no skip link: the page has no focusable element (needs <a href="#main">)')
    else:
        tag, link = first
        if link is None or link.attrs.get("href") != "#main" or not link.label():
            what = f"<a href={link.attrs.get('href')!r}>" if link is not None else f"<{tag}>"
            problems.append(f'no skip link: the first focusable element is {what}, '
                            f'not an <a href="#main"> with text')

    h1_count = parser.headings.count(1)
    if h1_count != 1:
        problems.append(f"the page has {h1_count} <h1> elements (needs exactly one)")
    if parser.headings and parser.headings[0] != 1:
        problems.append(f"the first heading is <h{parser.headings[0]}>, not <h1>")
    for previous, level in zip(parser.headings, parser.headings[1:]):
        if level > previous + 1:
            problems.append(f"heading level skips from <h{previous}> to <h{level}>")

    for link in parser.links:
        if "href" not in link.attrs or not (link.attrs["href"] or "").strip():
            problems.append(f"<a> with text {link.label()!r} has no href")
        if not link.label():
            problems.append(f"<a href={link.attrs.get('href')!r}> has no text")

    if parser.style_elements:
        problems.append(f"the page has a <style> element; use {STYLESHEET} only")
    if len(parser.stylesheets) != 1:
        problems.append(f"the page loads {len(parser.stylesheets)} stylesheets "
                        f"(needs exactly one, the site's {STYLESHEET})")
    for href in parser.stylesheets:
        if not stylesheet_ok(href, page_rel):
            problems.append(f"stylesheet {href!r} is not the site's {STYLESHEET}")

    return [f"{page_rel}: {problem}" for problem in problems]


def check_a11y(site_dir: str) -> list[str]:
    if not os.path.isdir(site_dir):
        return [f"{site_dir}: not a directory"]
    problems: list[str] = []
    pages = 0
    for root, dirs, files in os.walk(site_dir):
        dirs.sort()
        for name in sorted(files):
            if not name.lower().endswith(".html"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, site_dir).replace(os.sep, "/")
            pages += 1
            try:
                with open(path, encoding="utf-8") as handle:
                    html = handle.read()
            except (OSError, UnicodeDecodeError) as error:
                problems.append(f"{rel}: cannot read the page ({error})")
                continue
            problems += check_page(rel, html)
    if not pages:
        problems.append(f"{site_dir}: no .html pages found")
    return problems


def main(argv: list[str]) -> int:
    site_dir = argv[1] if len(argv) > 1 else "site"
    problems = check_a11y(site_dir)
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} problem(s) found", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
