"""Self-tests for the shipped support page (Story 13.5).

The support page gives the same contact email as the privacy policy record
(privacy-policy.json contactEmail), and it is the only email on the page. It links the
privacy policy and the source repository's issues, and says what a report should include.
No network is used.

Run: python3 -m unittest discover -s scripts -p 'test_*.py' -v
"""

from __future__ import annotations

import html
import json
import os
import re
import unittest
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SUPPORT = os.path.join(ROOT, "site", "support", "index.html")
RECORD = os.path.join(ROOT, "privacy-policy.json")
ISSUES_URL = "https://github.com/hebelmx/RubikXplorer/issues"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

REPORT_ITEMS = (
    "Your device, for example the phone model",
    "Your Android version",
    "The app version, from Settings → Apps → Cubentia",
    "What happened, and what you expected",
)
NO_PERSONAL_DATA = "No personal data is needed."
DO_NOT_POST = "Do not post personal details there."


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.items: list[str] = []
        self.text: list[str] = []
        self._item: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs.extend(value or "" for name, value in attrs if name == "href")
        if tag == "li":
            self._item = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self._item is not None:
            self.items.append(" ".join("".join(self._item).split()))
            self._item = None

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self._item is not None:
            self._item.append(data)


def support_problems(page: str, contact: str) -> list[str]:
    """Return what is wrong with a support page, given the policy record's contactEmail."""
    parser = _Page()
    parser.feed(page)
    parser.close()
    problems: list[str] = []
    text = " ".join(" ".join(parser.text).split())

    mailtos = [h[len("mailto:"):] for h in parser.hrefs if h.lower().startswith("mailto:")]
    if not mailtos:
        problems.append(f"no mailto link; expected mailto:{contact}")
    for address in mailtos:
        if address != contact:
            problems.append(f"support email {address!r} differs from the policy record's "
                            f"contactEmail {contact!r}")
    others = {e for e in EMAIL.findall(html.unescape(page)) if e != contact}
    for address in sorted(others):
        problems.append(f"email {address!r} is not the policy record's contactEmail {contact!r}")

    for href in ("../privacy/", ISSUES_URL):
        if href not in parser.hrefs:
            problems.append(f"no link to {href}")
    for item in REPORT_ITEMS:
        if item not in parser.items:
            problems.append(f"report item missing: {item!r}")
    for sentence in (NO_PERSONAL_DATA, DO_NOT_POST):
        if sentence not in text:
            problems.append(f"statement missing: {sentence!r}")
    return problems


class SupportPage(unittest.TestCase):
    def setUp(self) -> None:
        self.page = read(SUPPORT)
        self.contact = json.loads(read(RECORD))["contactEmail"]

    def test_shipped_page_passes(self) -> None:
        self.assertEqual(support_problems(self.page, self.contact), [])

    def test_contact_is_the_policy_contact(self) -> None:
        self.assertEqual(self.contact, "exxerpro@exxerpro.com")

    def test_changed_email_fails_naming_both_addresses(self) -> None:
        page = self.page.replace(self.contact, "help@example.com")
        problems = support_problems(page, self.contact)
        self.assertTrue(any("help@example.com" in p and self.contact in p for p in problems),
                        problems)

    def test_second_email_fails(self) -> None:
        page = self.page.replace("</main>", "<p>Or write to other@example.com.</p></main>")
        problems = support_problems(page, self.contact)
        self.assertTrue(any("other@example.com" in p for p in problems), problems)

    def test_missing_issues_link_fails(self) -> None:
        page = self.page.replace(ISSUES_URL, "https://github.com/hebelmx")
        self.assertIn(f"no link to {ISSUES_URL}", support_problems(page, self.contact))

    def test_missing_report_item_fails(self) -> None:
        page = self.page.replace("<li>Your Android version</li>", "")
        problems = support_problems(page, self.contact)
        self.assertIn("report item missing: 'Your Android version'", problems)


if __name__ == "__main__":
    unittest.main()
