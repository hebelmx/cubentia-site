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

from check_privacy import Page, check_privacy, update_record  # noqa: E402

CHECKER = os.path.join(HERE, "check_privacy.py")
CHECK_SH = os.path.join(HERE, "check.sh")
PAGE = os.path.join(ROOT, "site", "privacy", "index.html")
RECORD = os.path.join(ROOT, "privacy-policy.json")
FIXTURE = os.path.join(HERE, "fixtures", "privacy-facts.json")
DATE_MARKER = '<time data-policy="effective-date" datetime="2026-11-13">13 November 2026</time>'
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
        self.page_path = os.path.join(self.dir, "index.html")
        self.record_path = os.path.join(self.dir, "privacy-policy.json")
        self.facts_path = os.path.join(self.dir, "privacy-facts.json")
        self.page = read(PAGE)
        self.record = json.loads(read(RECORD))
        self.facts = json.loads(read(FIXTURE))

    def write(self, page: str | None = None, record: dict | None = None,
              facts: dict | str | None = None) -> None:
        with open(self.page_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(self.page if page is None else page)
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
        self.assertEqual(self.record["effectiveDate"], "2026-11-13")

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
        page = self.page_with(DATE_MARKER, "13 November 2026")
        self.assertFails("missing effective date marker", page=page)

    def test_effective_date_not_time(self) -> None:
        page = self.page_with(DATE_MARKER, '<span data-policy="effective-date">13 November 2026</span>')
        self.assertFails("must be a <time", page=page)

    def test_effective_date_bad_format(self) -> None:
        page = self.page_with('datetime="2026-11-13"', 'datetime="13/11/2026"')
        self.assertFails("invalid datetime '13/11/2026'", page=page)

    def test_effective_date_impossible(self) -> None:
        page = self.page_with('datetime="2026-11-13"', 'datetime="2026-02-30"')
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
        page = self.page_with(DATE_MARKER, DATE_MARKER.replace('datetime="2026-11-13"',
                                                               'datetime="2026-11-20"'))
        self.assertFails("effective date marker reads '13 November 2026'", page=page)

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
        page = self.page_with('datetime="2026-11-13">13 November 2026', 'datetime="2026-12-01">1 December 2026')
        self.assertFails("is not recorded", "--update", page=page)

    def test_date_earlier_than_record(self) -> None:
        page = self.page_with('datetime="2026-11-13">13 November 2026', 'datetime="2026-11-01">1 November 2026')
        self.assertFails("earlier than the recorded 2026-11-13", page=page)

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

    def dated(self, page: str) -> dict:
        """A record that matches the page, so only the wording can fail."""
        return {**self.record, "textSha256": Page(page).text_sha256()}

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
        page = self.page_with('datetime="2026-11-13">13 November 2026', 'datetime="2026-12-01">1 December 2026')
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
        self.write(page=self.page_with('datetime="2026-11-13">13 November 2026', 'datetime="2026-12-01">1 December 2026'))
        updated = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(updated.returncode, 0, updated.stdout + updated.stderr)


class CheckScriptOnCi(unittest.TestCase):
    """check.sh resolves PRIVACY_FACTS before anything else, so these runs stop at once."""

    def run_check(self, cwd: str | None = None, **env: str) -> subprocess.CompletedProcess[str]:
        clean = {k: v for k, v in os.environ.items() if k not in ("PRIVACY_FACTS", "GITHUB_ACTIONS")}
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


if __name__ == "__main__":
    unittest.main()
