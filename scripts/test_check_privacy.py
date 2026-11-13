"""Self-tests for check_privacy.py: one case per row of the Story 13.4 edge-case matrix.

Each case copies the shipped page, the policy record and the v1.0 facts fixture into a
temporary directory, changes one thing and asserts pass or fail. No network is used.

Run: python3 -m unittest discover -s scripts -p 'test_*.py' -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from check_privacy import check_privacy, policy_sha256, update_record  # noqa: E402

CHECKER = os.path.join(HERE, "check_privacy.py")
CHECK_SH = os.path.join(HERE, "check.sh")
PAGE = os.path.join(ROOT, "site", "privacy", "index.html")
STYLES = os.path.join(ROOT, "site", "styles.css")
RECORD = os.path.join(ROOT, "privacy-policy.json")
FIXTURE = os.path.join(HERE, "fixtures", "privacy-facts.json")
DATE_MARKER = '<time data-policy="effective-date" datetime="2026-11-14">14 November 2026</time>'
CONTACT = ('<a data-policy="contact" href="mailto:exxerpro@exxerpro.com">'
           'exxerpro@exxerpro.com</a>')
INTERNET = '<li data-fact="internetPermission" data-value="false">'
INTERNET_TEXT = "The app has no internet permission, so it cannot send anything over the network."
CHANGES_TEXT = "If this policy changes, this page shows the new text and a new effective date."


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class PrivacyCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        # The page sits one level down, as on the site, so its ../styles.css resolves.
        os.makedirs(os.path.join(self.dir, "privacy"))
        self.page_path = os.path.join(self.dir, "privacy", "index.html")
        self.styles_path = os.path.join(self.dir, "styles.css")
        self.styles = read(STYLES)
        self.record_path = os.path.join(self.dir, "privacy-policy.json")
        self.facts_path = os.path.join(self.dir, "privacy-facts.json")
        self.page = read(PAGE)
        self.record = json.loads(read(RECORD))
        self.facts = json.loads(read(FIXTURE))

    def write(self, page: str | None = None, record: dict | None = None,
              facts: dict | str | None = None, styles: str | None = None) -> None:
        with open(self.page_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(self.page if page is None else page)
        with open(self.styles_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(self.styles if styles is None else styles)
        if record is not False:
            with open(self.record_path, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(self.record if record is None else record, handle)
        if facts is not False:
            facts = self.facts if facts is None else facts
            with open(self.facts_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(facts if isinstance(facts, str) else json.dumps(facts))

    def problems(self) -> list[str]:
        return check_privacy(self.facts_path, self.page_path, self.record_path)

    def assertPasses(self, **kwargs) -> None:
        self.write(**kwargs)
        self.assertEqual(self.problems(), [])

    def assertFails(self, *needles: str, **kwargs) -> list[str]:
        self.write(**kwargs)
        problems = self.problems()
        self.assertTrue(problems, "expected the check to fail")
        joined = "\n".join(problems)
        for needle in needles:
            self.assertIn(needle, joined)
        return problems

    def dated(self, page: str, styles: str | None = None) -> dict:
        """A record that matches the page, so only the wording or CSS can fail."""
        self.write(page=page, styles=styles)
        return {**self.record, "textSha256": policy_sha256(self.page_path)}

    def page_with(self, old: str, new: str) -> str:
        self.assertIn(old, self.page)
        return self.page.replace(old, new)


class MatchingPage(PrivacyCase):
    def test_shipped_page_record_and_fixture_pass(self) -> None:
        self.assertEqual(check_privacy(FIXTURE, PAGE, RECORD), [])

    def test_copied_inputs_pass(self) -> None:
        self.assertPasses()

    def test_fixture_is_the_app_v1_file(self) -> None:
        self.assertEqual(self.facts, {
            "schemaVersion": 1, "applicationId": "com.exxerpro.cubentia",
            "internetPermission": False, "backupAllowed": False, "runtimePermissions": [],
            "denylistedSdks": [], "account": False, "ads": False, "analytics": False,
            "crashReporting": False, "dataLeavesDevice": False, "storage": "app-private"})

    def test_record_holds_the_po_values(self) -> None:
        self.assertEqual(self.record["publisher"], "Exxerpro Solutions, S.A. de C.V.")
        self.assertEqual(self.record["playDeveloper"], "Abel Briones")
        self.assertEqual(self.record["contactEmail"], "exxerpro@exxerpro.com")
        self.assertEqual(self.record["effectiveDate"], "2026-11-14")

    def test_cli_ok_and_failure(self) -> None:
        self.write()
        args = [sys.executable, CHECKER, "--facts", self.facts_path, "--page", self.page_path,
                "--record", self.record_path]
        good = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
        self.assertEqual(good.stdout.strip(), "OK")
        self.write(facts={**self.facts, "internetPermission": True})
        bad = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("internetPermission", bad.stdout)


class Facts(PrivacyCase):
    def test_file_gains_a_key(self) -> None:
        self.assertFails("no statement for fact 'location'", facts={**self.facts, "location": False})

    def test_page_drops_a_statement(self) -> None:
        page = self.page_with(INTERNET, "<li>")
        self.assertFails("no statement for fact 'internetPermission'", page=page)

    def test_value_differs(self) -> None:
        problems = self.assertFails("fact 'internetPermission'",
                                    facts={**self.facts, "internetPermission": True})
        line = next(p for p in problems if "internetPermission" in p)
        self.assertIn("'true'", line)
        self.assertIn("'false'", line)

    def test_list_value_differs(self) -> None:
        self.assertFails("fact 'runtimePermissions'", '\'["android.permission.CAMERA"]\'',
                         facts={**self.facts, "runtimePermissions": ["android.permission.CAMERA"]})

    def test_extra_fact_on_page(self) -> None:
        page = self.page_with("</ul>", '<li data-fact="location" data-value="false">No location.</li></ul>')
        self.assertFails("page states fact 'location' that the facts file lacks", page=page)

    def test_conflicting_statements(self) -> None:
        page = self.page_with("</ul>", '<li data-fact="ads" data-value="true">Ads.</li></ul>')
        self.assertFails("conflicting statements for fact 'ads'", page=page)

    def test_repeated_equal_statement_is_fine_for_facts(self) -> None:
        page = self.page_with("</ul>", '<li data-fact="ads" data-value="false">There are no ads.</li></ul>')
        problems = self.assertFails(page=page)  # the text hash changed, nothing else
        self.assertTrue(all("policy text changed" in p for p in problems), problems)

    def test_empty_statement(self) -> None:
        page = self.page_with("</ul>", '<li data-fact="ads" data-value="false">  </li></ul>')
        self.assertFails("statement for fact 'ads' has no text", page=page)

    def test_statement_without_value(self) -> None:
        page = self.page_with(INTERNET, '<li data-fact="internetPermission">')
        self.assertFails("statement for fact 'internetPermission' has no data-value", page=page)


class Markers(PrivacyCase):
    def test_publisher_missing(self) -> None:
        page = self.page_with('<span data-policy="publisher">', "<span>")
        self.assertFails("missing publisher marker", page=page)

    def test_publisher_not_recorded(self) -> None:
        page = self.page_with(">Exxerpro Solutions, S.A. de C.V.<", ">ExxerPro<")
        self.assertFails("publisher marker reads 'ExxerPro'", page=page)

    def test_play_developer_missing(self) -> None:
        page = self.page_with('<span data-policy="play-developer">', "<span>")
        self.assertFails("missing play-developer marker", page=page)

    def test_play_developer_not_recorded(self) -> None:
        page = self.page_with(">Abel Briones<", ">Someone Else<")
        self.assertFails("play-developer marker reads 'Someone Else'", page=page)

    def test_publisher_phrase_missing(self) -> None:
        page = self.page_with("(published on Google Play as", "(on Google Play as")
        self.assertFails("page text must contain", page=page)

    def test_contact_missing(self) -> None:
        page = self.page_with(CONTACT, "")
        self.assertFails("missing contact marker", page=page)

    def test_contact_not_a_link(self) -> None:
        page = self.page_with(CONTACT, '<span data-policy="contact">exxerpro@exxerpro.com</span>')
        self.assertFails("contact marker must be an <a> element", page=page)

    def test_bad_mailto(self) -> None:
        page = self.page_with('href="mailto:exxerpro@exxerpro.com"', 'href="mailto:"')
        self.assertFails("contact marker href", page=page)

    def test_contact_text_differs(self) -> None:
        record = {**self.record, "contactEmail": "help@exxerpro.com"}
        self.assertFails("contact marker reads 'exxerpro@exxerpro.com'", record=record)

    def test_effective_date_missing(self) -> None:
        page = self.page_with(DATE_MARKER, "14 November 2026")
        self.assertFails("missing effective date marker", page=page)

    def test_effective_date_not_time(self) -> None:
        page = self.page_with(DATE_MARKER, '<span data-policy="effective-date">14 November 2026</span>')
        self.assertFails("must be a <time", page=page)

    def test_effective_date_bad_format(self) -> None:
        page = self.page_with('datetime="2026-11-14"', 'datetime="13/11/2026"')
        self.assertFails("invalid datetime '13/11/2026'", page=page)

    def test_effective_date_impossible(self) -> None:
        page = self.page_with('datetime="2026-11-14"', 'datetime="2026-02-30"')
        self.assertFails("invalid datetime '2026-02-30'", page=page)


class SecondEmail(PrivacyCase):
    def test_second_email_in_text(self) -> None:
        page = self.page_with("</main>", "<p>Or abel@example.com.</p></main>")
        self.assertFails("must be the only email", "abel@example.com", page=page)

    def test_second_email_in_attribute(self) -> None:
        page = self.page_with("</main>", '<p title="x@example.org">Hi</p></main>')
        self.assertFails("must be the only email", "x@example.org", page=page)

    def test_second_mailto(self) -> None:
        page = self.page_with("</main>", '<p><a href="mailto:someone@example.org">Mail</a></p></main>')
        self.assertFails("must be the only email", page=page)

    def test_entity_encoded_email(self) -> None:
        page = self.page_with("</main>", "<p>bob&#64;example.org</p></main>")
        self.assertFails("bob@example.org", page=page)

    def test_email_in_comment(self) -> None:
        page = self.page_with("</main>", "<!-- carol@example.org --></main>")
        self.assertFails("carol@example.org", page=page)


class Record(PrivacyCase):
    def test_text_changed_same_date(self) -> None:
        page = self.page_with("There are no ads.", "There are never any ads.")
        self.assertFails("policy text changed without a new effective date", page=page)

    def test_value_change_counts_as_text_change(self) -> None:
        # Only a data-value changes (the facts file agrees), the visible text stays the same.
        page = self.page_with('data-fact="applicationId" data-value="com.exxerpro.cubentia"',
                              'data-fact="applicationId" data-value="com.exxerpro.other"')
        facts = {**self.facts, "applicationId": "com.exxerpro.other"}
        self.assertFails("policy text changed without a new effective date", page=page, facts=facts)

    def test_description_change_counts_as_text_change(self) -> None:
        page = self.page_with("Read the full privacy policy", "Read the whole privacy policy")
        self.assertFails("policy text changed without a new effective date", page=page)

    def test_title_change_counts_as_text_change(self) -> None:
        page = self.page_with("<title>Privacy policy · Cubentia</title>",
                              "<title>Privacy · Cubentia</title>")
        self.assertFails("policy text changed without a new effective date", page=page)

    def test_link_target_change_counts_as_text_change(self) -> None:
        page = self.page_with("privacy-policies/github-general-privacy-statement",
                              "privacy-policies/github-privacy-statement")
        self.assertFails("policy text changed without a new effective date", page=page)

    def test_effective_date_text_must_match_datetime(self) -> None:
        page = self.page_with(DATE_MARKER, DATE_MARKER.replace('datetime="2026-11-14"',
                                                               'datetime="2026-11-20"'))
        self.assertFails("effective date marker reads '14 November 2026'", page=page)

    def test_non_ascii_list_fact_matches(self) -> None:
        facts = {**self.facts, "runtimePermissions": ["permiso.cámara"]}
        page = self.page_with('data-fact="runtimePermissions" data-value="[]"',
                              'data-fact="runtimePermissions" data-value="[&quot;permiso.cámara&quot;]"')
        self.write(page=page, facts=facts)
        # The values match; the only complaint is that this value has no approved wording yet.
        problems = [p for p in self.problems() if "runtimePermissions" in p]
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("has no approved wording yet", problems[0])

    def test_new_date_not_recorded(self) -> None:
        page = self.page_with('datetime="2026-11-14">14 November 2026', 'datetime="2026-12-01">1 December 2026')
        self.assertFails("is not recorded", "--update", page=page)

    def test_date_earlier_than_record(self) -> None:
        page = self.page_with('datetime="2026-11-14">14 November 2026', 'datetime="2026-11-01">1 November 2026')
        self.assertFails("earlier than the recorded 2026-11-14", page=page)

    def test_record_missing(self) -> None:
        self.assertFails("policy record not found", record=False)

    def test_record_invalid(self) -> None:
        self.assertFails("missing or has empty fields: textSha256",
                         record={k: v for k, v in self.record.items() if k != "textSha256"})

    def test_page_without_main(self) -> None:
        page = self.page.replace('<main id="main">', "<div>").replace("</main>", "</div>")
        self.assertFails("exactly one <main>", page=page)


class TextOutsideMain(PrivacyCase):
    """Every reader-visible word and every attribute is hashed, not only <main>."""

    def assertTextChanged(self, page: str) -> None:
        self.assertFails("policy text changed without a new effective date", page=page)

    def test_footer_paragraph(self) -> None:
        self.assertTextChanged(self.page_with(
            "</footer>", "<p>Cubentia collects personal data.</p></footer>"))

    def test_header_text(self) -> None:
        self.assertTextChanged(self.page_with(
            '<a class="wordmark" href="../">Cubentia</a>',
            '<a class="wordmark" href="../">Cubentia collects personal data</a>'))

    def test_nav_in_header(self) -> None:
        self.assertTextChanged(self.page_with(
            "</header>", "<nav><p>Cubentia collects personal data.</p></nav></header>"))

    def test_hidden_element_in_footer(self) -> None:
        self.assertTextChanged(self.page_with(
            "</footer>", "<p hidden>Cubentia collects personal data.</p></footer>"))

    def test_text_after_body(self) -> None:
        self.assertTextChanged(self.page_with(
            "</body>", "</body>Cubentia collects personal data."))

    def test_attribute_only_change_outside_main(self) -> None:
        self.assertTextChanged(self.page_with(
            '<footer class="site-footer">',
            '<footer class="site-footer" title="Cubentia collects personal data">'))

    def test_class_change_outside_main(self) -> None:
        self.assertTextChanged(self.page_with('<footer class="site-footer">',
                                              '<footer class="skip-link">'))

    def test_markup_split_outside_main(self) -> None:
        self.assertTextChanged(self.page_with(
            "<p><a href=\"../\">Cubentia home</a></p>",
            "<p><a href=\"../\">Cubentia home</a> <span>collects</span> data</p>"))


class Wording(PrivacyCase):
    """Each fact value is tied to the sentence it must produce."""

    def test_supported_value_with_contradictory_wording(self) -> None:
        page = self.page_with(INTERNET + INTERNET_TEXT,
                              INTERNET + "The app has internet permission and sends data.")
        self.assertFails("statement for fact 'internetPermission'", "approved wording",
                         page=page, record=self.dated(page))

    def test_true_value_without_approved_wording(self) -> None:
        # Facts file and data-value agree on true, the record is dated, the text still says no.
        page = self.page_with(INTERNET, '<li data-fact="internetPermission" data-value="true">')
        problems = self.assertFails("fact 'internetPermission' = 'true' has no approved wording",
                                    page=page, record=self.dated(page),
                                    facts={**self.facts, "internetPermission": True})
        self.assertTrue(all("internetPermission" in p for p in problems), problems)

    def test_fact_file_value_without_wording_is_named(self) -> None:
        self.assertFails("fact 'ads' = 'true' has no approved wording",
                         facts={**self.facts, "ads": True})

    def test_appended_contradiction_inside_statement(self) -> None:
        page = self.page_with(INTERNET_TEXT, INTERNET_TEXT + " Just kidding, it has one.")
        self.assertFails("statement for fact 'internetPermission'", "approved wording",
                         page=page, record=self.dated(page))

    def test_whitespace_and_plain_inline_markup_are_fine(self) -> None:
        page = self.page_with(INTERNET_TEXT, "The app has  no\n  <em>internet</em> permission, "
                              "so it cannot send anything over the network.")
        self.assertPasses(page=page, record=self.dated(page))

    def test_hidden_word_inside_statement(self) -> None:
        page = self.page_with(INTERNET_TEXT, "The app has <span hidden>no </span>internet "
                              "permission, so it cannot send anything over the network.")
        self.assertFails("contains <span>", page=page, record=self.dated(page))

    def test_attribute_on_inline_markup_inside_statement(self) -> None:
        page = self.page_with(INTERNET_TEXT, "The app has <em class=\"skip-link\">no</em> internet "
                              "permission, so it cannot send anything over the network.")
        self.assertFails("contains <em> with attributes: class", page=page, record=self.dated(page))

    def test_hidden_statement(self) -> None:
        page = self.page_with(INTERNET, '<li data-fact="internetPermission" data-value="false" hidden>')
        self.assertFails("attributes that may change what is shown: hidden",
                         page=page, record=self.dated(page))

    def test_styled_or_classed_statement(self) -> None:
        for extra in ('style="display:none"', 'class="skip-link"', 'aria-hidden="true"'):
            with self.subTest(extra=extra):
                page = self.page_with(INTERNET, INTERNET[:-1] + " " + extra + ">")
                self.assertFails("attributes that may change what is shown",
                                 page=page, record=self.dated(page))

    def test_statement_in_hidden_ancestor(self) -> None:
        start = INTERNET + INTERNET_TEXT + "</li>"
        page = self.page_with(start, f"</ul><div hidden><ul>{start}</ul></div><ul>")
        self.assertFails("inside <div> with attributes that may hide it: hidden",
                         page=page, record=self.dated(page))

    def test_statement_in_template_or_details(self) -> None:
        start = INTERNET + INTERNET_TEXT + "</li>"
        for tag in ("template", "noscript", "details"):
            with self.subTest(tag=tag):
                page = self.page_with(start, f"</ul><{tag}><ul>{start}</ul></{tag}><ul>")
                self.assertFails(f"is inside <{tag}>", page=page, record=self.dated(page))

    def test_statement_outside_main(self) -> None:
        start = INTERNET + INTERNET_TEXT + "</li>"
        page = self.page_with(start, "").replace(
            "</footer>", f"<ul>{start}</ul></footer>")
        self.assertFails("must be inside <main>", page=page, record=self.dated(page))

    def test_statement_on_another_element(self) -> None:
        page = self.page_with(INTERNET + INTERNET_TEXT + "</li>",
                              '<li><span data-fact="internetPermission" data-value="false">'
                              + INTERNET_TEXT + "</span></li>")
        self.assertFails("must be a <p> or <li>", page=page, record=self.dated(page))

    def test_inline_style_element(self) -> None:
        page = self.page_with("</head>", "<style>li { display: none; }</style></head>")
        self.assertFails("may not have a <style> element", page=page, record=self.dated(page))

    def test_application_id_wording_follows_the_value(self) -> None:
        page = self.page_with('data-value="com.exxerpro.cubentia"', 'data-value="com.example.other"')
        self.assertFails("statement for fact 'applicationId'", "com.example.other",
                         page=page, record=self.dated(page),
                         facts={**self.facts, "applicationId": "com.example.other"})


class Stylesheets(PrivacyCase):
    """Every stylesheet the page loads is hashed and may hold only allowlisted CSS."""

    def styled(self, styles: str, page: str | None = None) -> list[str]:
        """Problems with this stylesheet and a record that matches it, so only the CSS can fail."""
        page = self.page if page is None else page
        record = self.dated(page, styles)
        self.write(page=page, record=record, styles=styles)
        return self.problems()

    def assertCssFails(self, css: str, needle: str = "not allowed", append: bool = True) -> None:
        problems = self.styled(self.styles + "\n" + css if append else css)
        self.assertTrue(problems, f"expected {css!r} to fail")
        self.assertTrue(any(needle in p for p in problems), (css, problems))
        self.assertFalse(any("policy text changed" in p for p in problems), problems)

    def test_shipped_stylesheet_passes(self) -> None:
        self.assertEqual(self.styled(self.styles), [])

    def test_stylesheet_change_needs_a_new_date(self) -> None:
        # Allowed CSS, stale record: a stylesheet change is a policy change.
        self.assertFails("policy text changed without a new effective date",
                         styles=self.styles + "\nh1 { font-weight: 700; }\n")

    def test_data_fact_display_none(self) -> None:
        self.assertCssFails("[data-fact]{display:none}", "CSS display: 'none'")
        self.assertCssFails("[data-fact] { display: none; }", "CSS display: 'none'")

    def test_hiding_properties_are_rejected(self) -> None:
        for css in ("li { visibility: hidden; }", "li { visibility: collapse; }",
                    "li { content-visibility: hidden; }", "li { opacity: 0; }",
                    "li { opacity: .01; }", "li { font-size: 0; }", "li { font-size: 1px; }",
                    "li { font-size: 0.1rem; }", "li { font-size: 0.5em; }",
                    "li { width: 0; overflow: hidden; }", "li { height: 0; }",
                    "li { max-height: 1px; overflow: clip; }", "li { clip: rect(0 0 0 0); }",
                    "li { clip-path: inset(50%); }", "li { position: absolute; left: -9999px; }",
                    "li { text-indent: -9999px; }", "li { color: transparent; }",
                    "li { color: #f4f5f7; }", "li { color: var(--surface); }",
                    "li { color: currentcolor; }", "li { background: transparent; }",
                    "li { transform: scale(0); }", "li { scale: 0; }", "li { zoom: 0.01; }",
                    "li { filter: opacity(0); }", "li { line-height: 0; }",
                    "li { -webkit-text-fill-color: transparent; }", "li { margin: -9999px; }",
                    "li { margin-left: 9999px; }", "li { padding-left: 200rem; }",
                    "li { max-width: 0; }", "li { display: contents; }",
                    "main { outline: 9999px solid var(--surface); }",
                    "li { border: 9999px solid var(--surface); }", "li { inset: 0; }",
                    "li { mix-blend-mode: screen; }", "li { letter-spacing: -1rem; }"):
            with self.subTest(css=css):
                self.assertCssFails(css)

    def test_shorthand_forms_are_rejected(self) -> None:
        for css in ("li { font: 0/0 a; }", "li { overflow: hidden; }", "li { inset: -100%; }",
                    "li { flex: 0; }", "li { transition: all 1s; }", "li { animation: x 1s; }",
                    "li { background: #f4f5f7; }", "li { columns: 1 0; }"):
            with self.subTest(css=css):
                self.assertCssFails(css)

    def test_uppercase_and_important(self) -> None:
        self.assertCssFails("[DATA-FACT] { DISPLAY: NONE; }", "CSS display: 'NONE'")
        self.assertCssFails("li { Opacity: 0 }", "CSS property opacity")
        self.assertCssFails("li { display: block !important; }", "!important")
        self.assertCssFails("li { display: none!IMPORTANT; }", "!important")

    def test_escapes_are_rejected(self) -> None:
        for css in ("li { dis\\70 lay: none; }", "li { display: \\6e one; }",
                    "[data-fact] { \\64isplay: none }", 'li { font-family: "\\41"; }'):
            with self.subTest(css=css):
                self.assertCssFails(css, "escapes")

    def test_comments(self) -> None:
        # A comment is removed, never joined: dis/**/play is not display.
        self.assertCssFails("li { dis/**/play: none; }", "not allowed")
        self.assertCssFails("li { display: /* */ none; }", "CSS display: 'none'")
        self.assertCssFails("li { display: none; } /* unterminated", "unterminated CSS comment")
        # A comment opener inside a string does not start a comment.
        self.assertCssFails(':root { --font-sans: "/*"; } li { display: none; } '
                            ':root { --gutter: "*/"; }', "CSS display: 'none'")

    def test_custom_properties_and_var(self) -> None:
        self.assertCssFails(":root { --gutter: none; } li { display: var(--gutter); }")
        self.assertCssFails("li { --ink: #f4f5f7; }", "may be set only on :root")
        self.assertCssFails(":root, [data-fact] { --ink: #f4f5f7; }", "may be set only on :root")
        self.assertCssFails(":root { --hide: none; }", "custom property --hide")
        self.assertCssFails(":root { --Ink: #f4f5f7; }", "custom property --Ink")
        self.assertCssFails(":root { --ink: #f4f5f7; }", "contrast")
        self.assertCssFails(":root { --ink: transparent; }")
        self.assertCssFails(":root { --gutter: 9999px; }")
        self.assertCssFails(":root { --measure: 0; }")
        self.assertCssFails(':root { --font-sans: "Adobe Blank", sans-serif; }')
        self.assertCssFails("li { color: var(--ink, transparent); }")
        self.assertCssFails("li { color: var(--surface-raised); }")

    def test_text_contrast_needs_4_5(self) -> None:
        # #888888 on --surface #f4f5f7 is about 3.25:1: enough for the old 3:1, not for AA text.
        self.assertCssFails(":root { --ink-muted: #888888; }", "4.5")
        # #656d79 on #f4f5f7 is about 4.79:1 and passes.
        self.assertIn("--ink-muted: #5a6370;", self.styles)
        self.assertEqual(self.styled(self.styles.replace("--ink-muted: #5a6370;",
                                                         "--ink-muted: #656d79;")), [])

    def test_border_and_outline_need_3_to_1(self) -> None:
        self.assertCssFails("li { outline: 3px solid var(--surface-raised); }", "3:1")
        self.assertCssFails("li { border: 2px solid var(--surface); }", "3:1")
        # In a style="" attribute on the policy page too.
        page = self.page_with("<h2>In short</h2>",
                              '<h2 style="border: 2px solid var(--surface)">In short</h2>')
        problems = self.styled(self.styles, page=page)
        self.assertTrue(any("3:1" in p for p in problems), problems)
        # Checked against the site's own surfaces, not only the browser canvas: in the dark
        # scheme #636363 reaches 3.12:1 on the canvas #121212 and 3.10:1 on --surface, but
        # --surface-raised is #636363 itself, so a border in it fails (inks still reach 4.5:1).
        dark = ("@media (prefers-color-scheme: dark) { :root { --surface-raised: #636363; "
                "--ink-muted: #e7eaee; --accent: #e7eaee; } }\n"
                "li { border: 2px solid var(--surface-raised); }")
        problems = [p for p in self.styled(self.styles + "\n" + dark) if "dark scheme" in p]
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("3:1", problems[0])
        self.assertIn("on background #636363", problems[0])
        # A token used only as a text or background color is not held to the non-text rule.
        self.assertEqual(self.styled(self.styles + "\nli { background: var(--surface-raised); }"), [])

    def test_browser_link_colors_checked_without_an_a_color_rule(self) -> None:
        a_rule = "a {\n  color: var(--accent);\n}\n"
        self.assertIn(a_rule, self.styles)
        without = self.styles.replace(a_rule, "")
        for styles in (without,
                       without + "main a { color: var(--accent); }",
                       without + "@media (prefers-color-scheme: dark) { a { color: var(--accent); } }"):
            with self.subTest(styles=styles[-60:]):
                problems = self.styled(styles)
                self.assertTrue(any("#ee0000" in p and "4.5" in p for p in problems), problems)

    def test_browser_link_colors_skipped_with_an_a_color_rule(self) -> None:
        problems = self.styled(self.styles)
        self.assertEqual(problems, [])
        self.assertFalse(any(c in p for p in problems for c in ("#ee0000", "#0000ee")))

    def test_at_rules(self) -> None:
        for css, needle in (("@media screen { li { display: none; } }", "@media 'screen'"),
                            ("@media (prefers-color-scheme: dark) { li { display: none; } }",
                             "CSS display: 'none'"),
                            ("@media (prefers-color-scheme: dark) { :root { --ink: #111317; } }",
                             "contrast"),
                            ("@media (prefers-color-scheme: dark) { @media print { li "
                             "{ display: none } } }", "at-rule @media"),
                            ("@supports (display: grid) { li { display: none; } }", "@supports"),
                            ("@layer x { li { display: none; } }", "@layer"),
                            ("@layer x;", "@layer"),
                            ("@container (min-width: 1px) { li { display: none; } }", "@container"),
                            ("@font-face { font-family: x; src: url(x.woff2); }", "@font-face"),
                            ("@keyframes k { to { opacity: 0; } }", "@keyframes"),
                            ("@scope (main) { li { display: none; } }", "@scope")):
            with self.subTest(css=css):
                self.assertCssFails(css, needle)

    def test_nesting_and_broken_structure(self) -> None:
        for css in ("main { & li { display: none; } }", "main { li { display: none; } }",
                    "li { color: var(--ink); } } [data-fact] { display: none; }",
                    "li { color: var(--ink) ", "li { color: var(--ink)( } [data-fact] "
                    "{ display: none; }", "[data-fact] { display: none; ",
                    "<!-- [data-fact] { display: none; } -->", "li { color }"):
            with self.subTest(css=css):
                self.assertCssFails(css, "")

    def test_generated_content(self) -> None:
        self.assertCssFails('li::before { content: "Not true: "; }', "CSS property content")
        self.assertCssFails("li::after { content: none; display: block; }", "CSS property content")

    def test_attribute_and_structural_selectors_are_still_checked(self) -> None:
        for selector in ('[data-value="false"]', "main > ul > li:nth-child(2)", "li + li",
                         ":is(p, li)", "main *", "body:has(li)", "li::first-line"):
            with self.subTest(selector=selector):
                self.assertCssFails(selector + " { font-size: 0; }", "CSS font-size")

    def test_non_ascii_css_is_rejected(self) -> None:
        self.assertCssFails("li { display\uff1a none; }", "printable ASCII")

    def test_color_scheme_must_be_set(self) -> None:
        styles = self.styles.replace("  color-scheme: light dark;\n", "")
        self.assertNotEqual(styles, self.styles)
        problems = self.styled(styles)
        self.assertTrue(any("color-scheme" in p for p in problems), problems)

    def test_inline_style_anywhere_is_checked(self) -> None:
        for old, new in (("<h1>", '<h1 style="display:none">'),
                         ("<body>", '<body style="opacity:0">'),
                         ('<html lang="en">', '<html lang="en" style="--ink:#f4f5f7">'),
                         ("<h2>In short</h2>",
                          '<h2 style="position:fixed;inset:0;background:var(--surface)">In short</h2>'),
                         ("<h1>", '<h1 style="DIS\\70 LAY:none">')):
            with self.subTest(new=new):
                page = self.page_with(old, new)
                problems = self.styled(self.styles, page=page)
                self.assertTrue(any("style attribute" in p for p in problems), problems)
        page = self.page_with("<h1>", '<h1 style="color: var(--ink)">')
        self.assertEqual(self.styled(self.styles, page=page), [])

    def test_imported_stylesheet_is_checked_and_hashed(self) -> None:
        extra = os.path.join(self.dir, "extra.css")
        with open(extra, "w", encoding="utf-8") as handle:
            handle.write("h1 { font-weight: 700; }\n")
        styles = '@import "extra.css";\n' + self.styles
        self.assertEqual(self.styled(styles), [])
        with open(extra, "w", encoding="utf-8") as handle:
            handle.write("h1 { font-weight: 600; }\n")
        self.assertFails("policy text changed without a new effective date", styles=styles)
        with open(extra, "w", encoding="utf-8") as handle:
            handle.write("[data-fact]{display:none}\n")
        problems = self.styled(styles)
        self.assertTrue(any("extra.css" in p and "CSS display" in p for p in problems), problems)
        for imported in ("url(extra.css)", 'url("extra.css")', "'extra.css'"):
            with self.subTest(imported=imported):
                problems = self.styled(f"@import {imported};\n" + self.styles)
                self.assertTrue(any("CSS display" in p for p in problems), problems)
        for bad in ('@import "extra.css" print;', '@import url(extra.css) layer(x);',
                    '@import "https://example.com/x.css";', '@import "/x.css";',
                    '@import "extra.css?x";', '@import "missing.css";', '@import "styles.css";'):
            with self.subTest(bad=bad):
                self.assertTrue(self.styled(bad + "\n" + self.styles))

    def test_every_linked_stylesheet_is_checked(self) -> None:
        extra = os.path.join(self.dir, "extra.css")
        with open(extra, "w", encoding="utf-8") as handle:
            handle.write("[data-fact]{display:none}\n")
        for link in ('<link rel="StyleSheet" href="../extra.css">',
                     '<link rel="alternate stylesheet" title="x" href="../extra.css">',
                     '<link rel="stylesheet" media="print" href="../extra.css">'):
            with self.subTest(link=link):
                page = self.page_with("</head>", link + "</head>")
                problems = self.styled(self.styles, page=page)
                self.assertTrue(any("extra.css" in p and "CSS display" in p for p in problems),
                                problems)

    def test_stylesheet_href_must_be_a_plain_relative_path(self) -> None:
        for href in ("https://example.com/x.css", "//example.com/x.css", "/cubentia-site/styles.css",
                     "../styles.css?v=2", "../styles.css#x", "data:text/css,li{display:none}",
                     " ../styles.css", "../missing.css"):
            with self.subTest(href=href):
                page = self.page_with('href="../styles.css"', f'href="{href}"')
                self.assertTrue(self.styled(self.styles, page=page))

    def test_base_element_is_rejected(self) -> None:
        page = self.page_with("</head>", '<base href="/other/"></head>')
        problems = self.styled(self.styles, page=page)
        self.assertTrue(any("<base>" in p for p in problems), problems)


class BadFacts(PrivacyCase):
    def test_facts_missing(self) -> None:
        self.write(facts=False)
        problems = check_privacy(os.path.join(self.dir, "nope.json"), self.page_path, self.record_path)
        self.assertTrue(any("facts file not found" in p for p in problems), problems)

    def test_facts_invalid_json(self) -> None:
        self.assertFails("facts file is not valid JSON", facts="{nope")

    def test_facts_not_an_object(self) -> None:
        self.assertFails("must be a JSON object", facts="[]")

    def test_schema_version_two(self) -> None:
        self.assertFails("unsupported schemaVersion 2", facts={**self.facts, "schemaVersion": 2})

    def test_schema_version_missing(self) -> None:
        facts = {k: v for k, v in self.facts.items() if k != "schemaVersion"}
        self.assertFails("unsupported schemaVersion None", facts=facts)

    def test_schema_version_not_int(self) -> None:
        self.assertFails("unsupported schemaVersion", facts={**self.facts, "schemaVersion": "1"})
        self.assertFails("unsupported schemaVersion", facts={**self.facts, "schemaVersion": True})


class Update(PrivacyCase):
    def test_later_date_rewrites_the_record(self) -> None:
        page = self.page_with('datetime="2026-11-14">14 November 2026', 'datetime="2026-12-01">1 December 2026')
        page = page.replace(CHANGES_TEXT, "When this policy changes, this page shows a new date.")
        self.write(page=page)
        ok, messages = update_record(self.page_path, self.record_path)
        self.assertTrue(ok, messages)
        record = json.loads(read(self.record_path))
        self.assertEqual(record["effectiveDate"], "2026-12-01")
        self.assertNotEqual(record["textSha256"], self.record["textSha256"])
        self.assertEqual(record["publisher"], self.record["publisher"])
        self.assertEqual(self.problems(), [])

    def test_same_date_refuses(self) -> None:
        page = self.page_with("There are no ads.", "There are never any ads.")
        self.write(page=page)
        ok, messages = update_record(self.page_path, self.record_path)
        self.assertFalse(ok)
        self.assertIn("refusing to update", "\n".join(messages))
        self.assertEqual(json.loads(read(self.record_path)), self.record)

    def test_missing_record_is_created_from_the_page(self) -> None:
        self.write(record=False)
        ok, messages = update_record(self.page_path, self.record_path)
        self.assertTrue(ok, messages)
        self.assertEqual(json.loads(read(self.record_path)), self.record)

    def test_cli_exit_codes(self) -> None:
        self.write()
        args = [sys.executable, CHECKER, "--update", "--page", self.page_path,
                "--record", self.record_path]
        refused = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(refused.returncode, 1)
        self.write(page=self.page_with('datetime="2026-11-14">14 November 2026', 'datetime="2026-12-01">1 December 2026'))
        updated = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(updated.returncode, 0, updated.stdout + updated.stderr)


class CheckScriptOnCi(unittest.TestCase):
    """check.sh resolves PRIVACY_FACTS before anything else, so these runs stop at once."""

    def run_check(self, cwd: str | None = None, **env: str) -> subprocess.CompletedProcess[str]:
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("PRIVACY_FACTS", "PRIVACY_BASE", "GITHUB_ACTIONS")}
        return subprocess.run(["bash", CHECK_SH], capture_output=True, text=True,
                              env={**clean, **env}, timeout=60, cwd=cwd)

    def test_relative_privacy_facts_resolves_from_the_callers_directory(self) -> None:
        with tempfile.TemporaryDirectory() as caller:
            result = self.run_check(cwd=caller, GITHUB_ACTIONS="true", PRIVACY_FACTS="missing.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(os.path.join(caller, "missing.json"), result.stderr)

    def test_ci_without_privacy_facts_fails(self) -> None:
        result = self.run_check(GITHUB_ACTIONS="true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRIVACY_FACTS", result.stderr)

    def test_ci_with_missing_privacy_facts_fails(self) -> None:
        result = self.run_check(GITHUB_ACTIONS="true", PRIVACY_FACTS="/nonexistent/privacy-facts.json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exist", result.stderr)

    def test_ci_without_privacy_base_fails(self) -> None:
        result = self.run_check(GITHUB_ACTIONS="true", PRIVACY_FACTS=FIXTURE)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRIVACY_BASE must be set on CI", result.stderr)


class PreviousRevision(unittest.TestCase):
    """--base compares with a trusted git revision, never with the record in the same change.

    Each case builds a temporary git repository (no network) whose commits hold the page and
    the record at site/privacy/index.html and privacy-policy.json.
    """

    NEW_DATE = 'datetime="2026-12-01">1 December 2026'
    OLD_DATE = 'datetime="2026-11-14">14 November 2026'

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = os.path.join(tmp.name, "repo")
        self.page_path = os.path.join(self.repo, "site", "privacy", "index.html")
        self.record_path = os.path.join(self.repo, "privacy-policy.json")
        os.makedirs(os.path.dirname(self.page_path))
        with open(os.path.join(self.repo, "site", "styles.css"), "w", encoding="utf-8",
                  newline="\n") as handle:
            handle.write(read(STYLES))
        self.git("init", "-q", "-b", "main")
        self.page = read(PAGE)
        self.record = json.loads(read(RECORD))

    def git(self, *args: str, cwd: str | None = None) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.invalid",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.invalid",
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        result = subprocess.run(["git", *args], cwd=cwd or self.repo, capture_output=True,
                                text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def put(self, page: str | None = None, record: dict | None | bool = None) -> None:
        """Write the page and the record, recomputing textSha256 as an attacker would."""
        with open(self.page_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(self.page if page is None else page)
        if record is False:
            if os.path.exists(self.record_path):
                os.remove(self.record_path)
            return
        record = dict(self.record if record is None else record)
        record["textSha256"] = policy_sha256(self.page_path)
        with open(self.record_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(record, handle)

    def commit(self, message: str = "c") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def problems(self, base: str, repo: str | None = None,
                 notes: list[str] | None = None) -> list[str]:
        repo = repo or self.repo
        return check_privacy(FIXTURE, os.path.join(repo, "site", "privacy", "index.html"),
                             os.path.join(repo, "privacy-policy.json"), base, notes)

    def changed_page(self, date: str | None = None) -> str:
        self.assertIn(CHANGES_TEXT, self.page)
        page = self.page.replace(CHANGES_TEXT, "This policy may change at any time.")
        return page if date is None else page.replace(self.OLD_DATE, date)

    def published(self) -> str:
        """main holds the shipped page and record; returns its commit."""
        self.put()
        return self.commit("publish")

    def test_text_and_committed_hash_changed_same_date_fails(self) -> None:
        base = self.published()
        self.put(page=self.changed_page())  # the record's hash is recomputed, the date is kept
        problems = self.problems(base)
        self.assertTrue(any("policy text changed since" in p for p in problems), problems)
        # Without --base this change is invisible, which is the bug being closed.
        self.assertEqual(self.problems(None), [])

    def test_stylesheet_change_same_date_fails(self) -> None:
        base = self.published()
        styles = os.path.join(self.repo, "site", "styles.css")
        with open(styles, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("h1 { font-weight: 700; }\n")
        self.put()  # page unchanged, the record's hash is recomputed, the date is kept
        problems = self.problems(base)
        self.assertTrue(any("policy text changed since" in p for p in problems), problems)

    def test_age_coverage_change_same_date_fails(self) -> None:
        base = self.published()
        age = "This policy applies to Learners of any age."
        self.assertIn(age, self.page)
        self.put(page=self.page.replace(age, "This policy applies to Learners aged 13 or older."))
        self.assertTrue(any("policy text changed since" in p for p in self.problems(base)))

    def test_text_changed_with_later_date_passes(self) -> None:
        base = self.published()
        self.put(page=self.changed_page(self.NEW_DATE), record={**self.record,
                                                                "effectiveDate": "2026-12-01"})
        self.assertEqual(self.problems(base), [])

    def test_date_moved_backwards_fails(self) -> None:
        base = self.published()
        earlier = 'datetime="2026-11-01">1 November 2026'
        self.put(page=self.changed_page(earlier), record={**self.record,
                                                          "effectiveDate": "2026-11-01"})
        self.assertTrue(any("without a later effective date" in p for p in self.problems(base)))

    def test_unchanged_text_passes(self) -> None:
        base = self.published()
        self.put()
        self.assertEqual(self.problems(base), [])

    def test_committed_hash_is_not_trusted_from_base_either(self) -> None:
        # A base record whose textSha256 was already forged still compares by base's page.
        self.put()
        forged = dict(self.record)
        with open(self.record_path, "w", encoding="utf-8") as handle:
            json.dump({**forged, "textSha256": "0" * 64}, handle)
        base = self.commit()
        self.put(page=self.changed_page())
        self.assertTrue(any("policy text changed since" in p for p in self.problems(base)))

    def test_other_record_fields_changed_same_date_fails(self) -> None:
        base = self.published()
        page = self.page.replace("exxerpro@exxerpro.com", "privacy@exxerpro.com")
        self.put(page=page, record={**self.record, "contactEmail": "privacy@exxerpro.com"})
        self.assertTrue(any("policy text changed since" in p for p in self.problems(base)))

    def test_first_publish_without_previous_record_passes_and_says_so(self) -> None:
        with open(os.path.join(self.repo, "README.md"), "w") as handle:
            handle.write("site\n")
        base = self.commit("no policy yet")
        self.put()
        notes: list[str] = []
        self.assertEqual(self.problems(base, notes=notes), [])
        self.assertTrue(any("first publish" in n for n in notes), notes)

    def test_cli_prints_first_publish_note(self) -> None:
        base = self.commit("empty")
        self.put()
        result = subprocess.run([sys.executable, CHECKER, "--facts", FIXTURE, "--page",
                                 self.page_path, "--record", self.record_path, "--base", base],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("first publish", result.stdout)
        self.assertTrue(result.stdout.strip().endswith("OK"))

    def test_record_deleted_in_change_fails(self) -> None:
        base = self.published()
        self.put(page=self.changed_page(), record=False)
        self.assertTrue(any("policy record not found" in p for p in self.problems(base)))

    def test_base_record_without_page_fails(self) -> None:
        self.put()
        os.remove(self.page_path)
        base = self.commit()
        self.put()
        self.assertTrue(any("has a policy record but no" in p for p in self.problems(base)))

    def test_base_record_with_bad_date_fails(self) -> None:
        self.put(record={**self.record, "effectiveDate": "13/11/2026"})
        base = self.commit()
        self.put()
        self.assertTrue(any("no valid effectiveDate" in p for p in self.problems(base)))

    def test_base_record_invalid_json_fails(self) -> None:
        self.put()
        with open(self.record_path, "w") as handle:
            handle.write("{")
        base = self.commit()
        self.put()
        self.assertTrue(any("not valid JSON" in p for p in self.problems(base)))

    def test_unknown_base_fails_closed(self) -> None:
        self.published()
        for base in ("does-not-exist", "0" * 40, "", "--output=x"):
            problems = self.problems(base)
            self.assertTrue(any("cannot read the previous revision" in p for p in problems),
                            (base, problems))

    def test_page_outside_a_git_repo_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as plain:
            os.makedirs(os.path.join(plain, "site", "privacy"))
            with open(os.path.join(plain, "site", "privacy", "index.html"), "w",
                      encoding="utf-8") as handle:
                handle.write(self.page)
            with open(os.path.join(plain, "privacy-policy.json"), "w") as handle:
                json.dump(self.record, handle)
            env_git_dir = os.environ.get("GIT_CEILING_DIRECTORIES")
            os.environ["GIT_CEILING_DIRECTORIES"] = os.path.dirname(plain)
            try:
                problems = self.problems("HEAD", repo=plain)
            finally:
                if env_git_dir is None:
                    del os.environ["GIT_CEILING_DIRECTORIES"]
                else:
                    os.environ["GIT_CEILING_DIRECTORIES"] = env_git_dir
        self.assertTrue(any("not in a git work tree" in p for p in problems), problems)

    def test_push_to_main_with_merge_commit_uses_first_parent(self) -> None:
        self.published()
        self.git("checkout", "-q", "-b", "feature")
        self.put(page=self.changed_page())  # same date, hash recomputed
        self.commit("feature")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", "merge", "feature")
        # HEAD^1 is the previous main; HEAD^2 (the feature tip) would hide the change.
        self.assertTrue(any("policy text changed since" in p for p in self.problems("HEAD^1")))
        self.assertEqual(self.problems("HEAD^2"), [])

    def test_shallow_clone_without_parent_fails_closed(self) -> None:
        self.published()
        self.put(page=self.changed_page())
        self.commit("change")
        clone = os.path.join(os.path.dirname(self.repo), "clone")
        self.git("clone", "-q", "--depth", "1", "file://" + self.repo, clone,
                 cwd=os.path.dirname(self.repo))
        problems = self.problems("HEAD^1", repo=clone)
        self.assertTrue(any("is not available" in p for p in problems), problems)
        deeper = os.path.join(os.path.dirname(self.repo), "clone2")
        self.git("clone", "-q", "--depth", "2", "file://" + self.repo, deeper,
                 cwd=os.path.dirname(self.repo))
        self.assertTrue(any("policy text changed since" in p
                            for p in self.problems("HEAD^1", repo=deeper)))


if __name__ == "__main__":
    unittest.main()
