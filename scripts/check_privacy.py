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
  whitespace-normalized text of <main>, the (data-fact, data-value) pairs in order, the
  <title>, the meta description and every <a href> target.

The check fails when a fact has no statement with an equal value, the page states a fact the
file lacks, statements conflict or are empty, a marker is missing or wrong, the page holds any
email other than the contact, or the policy text changed without a new, recorded effective date.

Usage:
  python3 scripts/check_privacy.py --facts FILE [--page PAGE] [--record RECORD]
  python3 scripts/check_privacy.py --update [--page PAGE] [--record RECORD]

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
        # Policy wording outside <main> (title, meta description) and every link target count too.
        title = self.first("title")
        descriptions = [e.attrs.get("content", "") for e in self.elements
                        if e.tag == "meta" and e.attrs.get("name", "").lower() == "description"]
        hrefs = [e.attrs["href"] for e in self.elements if e.tag == "a" and "href" in e.attrs]
        payload = json.dumps({"main": main[0].text,
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


def check_privacy(facts_path: str, page_path: str = DEFAULT_PAGE,
                  record_path: str = DEFAULT_RECORD) -> list[str]:
    problems: list[str] = []
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
    parser.add_argument("--update", action="store_true",
                        help="rewrite the record's effectiveDate and textSha256 from the page")
    args = parser.parse_args(argv)
    if args.update:
        ok, messages = update_record(args.page, args.record)
        print("\n".join(messages))
        return 0 if ok else 1
    if not args.facts:
        parser.error("--facts is required unless --update is given")
    problems = check_privacy(args.facts, args.page, args.record)
    if problems:
        print("\n".join(problems))
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
