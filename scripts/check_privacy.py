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
  pairs in order, the <title>, the meta description and every <a href> target.

Each supported fact value has one approved wording (APPROVED_WORDING). A statement's text must
equal the wording for its value; a value with no approved wording fails, naming the fact. A
statement must be a visible <p> or <li> inside <main>: no hiding attributes on it or its
ancestors, and only attribute-free <code>, <em> or <strong> inside it.

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

    def text_sha256(self) -> str | None:
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
                              "hrefs": hrefs},
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


def check_record(page_path: str, record_path: str, page: Page, record: dict,
                 problems: list[str]) -> None:
    date, problem = page.effective_date()
    digest = page.text_sha256()
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


def check_previous(page_path: str, record_path: str, page: Page, base: str,
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
    previous_digest = Page(page_source).text_sha256()
    if previous_digest is None:
        problems.append(f"{page_path}: the previous page ({page_tree} at {base}) has no single <main>")
        return
    date, problem = page.effective_date()
    digest = page.text_sha256()
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
    if facts is not None:
        check_facts(page_path, page, facts, problems)
    if record is not None:
        check_markers(page_path, page, record, problems)
        check_single_email(page_path, page, record["contactEmail"], problems)
        check_record(page_path, record_path, page, record, problems)
    if base is not None:
        check_previous(page_path, record_path, page, base, problems, notes)
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
    digest = page.text_sha256()
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
