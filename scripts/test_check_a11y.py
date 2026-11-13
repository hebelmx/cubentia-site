"""Self-tests for check_a11y.py: one case per accessibility row of the Story 13.2 matrix.

Each case builds a temporary site from a page that passes, changes one thing and asserts pass
or fail. No network is used.

Run: python3 -m unittest discover -s scripts -p 'test_*.py' -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from check_a11y import check_a11y  # noqa: E402

CHECKER = os.path.join(HERE, "check_a11y.py")
SHIPPED = os.path.join(os.path.dirname(HERE), "site")

PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cubentia</title>
  <link rel="stylesheet" href="{css}">
</head>
<body>
  <a class="skip-link" href="#main">Skip to content</a>
  <header class="site-header">
    <a class="wordmark" href="{home}">Cubentia</a>
  </header>
  <main id="main">
    <h1>Cubentia</h1>
    <p>An offline tutor.</p>
    <h2>Section</h2>
    <h3>Detail</h3>
    <h2>Another section</h2>
  </main>
  <footer class="site-footer">
    <p>© 2026 Exxerpro Solutions, S.A. de C.V.</p>
  </footer>
</body>
</html>
"""
INDEX = PAGE.format(css="styles.css", home="./")
NESTED = PAGE.format(css="../styles.css", home="../")
NOT_FOUND = PAGE.format(css="/cubentia-site/styles.css", home="/cubentia-site/")


class A11yCase(unittest.TestCase):
    def build(self, index: str = INDEX, extra_files: dict[str, str] | None = None) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        site = os.path.join(tmp.name, "site")
        files = {"index.html": index, "support/index.html": NESTED, "404.html": NOT_FOUND,
                 "styles.css": "body { margin: 0; }\n"}
        files.update(extra_files or {})
        for rel, content in files.items():
            path = os.path.join(site, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        return site

    def index_with(self, old: str, new: str) -> str:
        self.assertIn(old, INDEX)
        return INDEX.replace(old, new, 1)

    def assertPasses(self, index: str = INDEX) -> None:
        self.assertEqual(check_a11y(self.build(index)), [])

    def assertFails(self, index: str, *needles: str) -> list[str]:
        problems = check_a11y(self.build(index))
        self.assertTrue(problems, "expected the checker to fail")
        joined = "\n".join(problems)
        for needle in needles:
            self.assertIn(needle, joined)
        self.assertTrue(all(p.startswith("index.html: ") for p in problems), problems)
        return problems


class CleanSite(A11yCase):
    def test_built_site_passes(self) -> None:
        self.assertPasses()

    def test_shipped_site_passes(self) -> None:
        self.assertEqual(check_a11y(SHIPPED), [])

    def test_cli_exit_codes(self) -> None:
        good = subprocess.run([sys.executable, CHECKER, self.build()], capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
        self.assertIn("OK", good.stdout)
        bad_site = self.build(self.index_with('lang="en"', 'lang="es"'))
        bad = subprocess.run([sys.executable, CHECKER, bad_site], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("index.html: <html lang='es'>", bad.stdout)

    def test_every_page_is_checked(self) -> None:
        nested = PAGE.format(css="../../styles.css", home="../../").replace(' lang="en"', "")
        problems = check_a11y(self.build(extra_files={"deep/er/page.html": nested}))
        self.assertEqual(problems, ["deep/er/page.html: <html> has no lang attribute "
                                    '(needs lang="en")'])


class Language(A11yCase):
    def test_missing_or_wrong_lang(self) -> None:
        self.assertFails(self.index_with(' lang="en"', ""), "no lang attribute")
        self.assertFails(self.index_with('lang="en"', 'lang="es"'), "lang must be \"en\"")
        self.assertFails(self.index_with('lang="en"', 'lang="en-US"'), "lang must be \"en\"")

    def test_missing_or_empty_title(self) -> None:
        self.assertFails(self.index_with("<title>Cubentia</title>", ""), "no title")
        self.assertFails(self.index_with("<title>Cubentia</title>", "<title> </title>"), "no title")


class Zoom(A11yCase):
    def test_zoom_blocked(self) -> None:
        viewport = "width=device-width, initial-scale=1"
        for content, needle in ((viewport + ", user-scalable=no", "user-scalable=no"),
                                (viewport + ", user-scalable=0", "user-scalable=0"),
                                (viewport + ", maximum-scale=1", "maximum-scale=1"),
                                (viewport + ", Maximum-Scale=5", "maximum-scale=5")):
            with self.subTest(content=content):
                self.assertFails(self.index_with(viewport, content), needle)

    def test_missing_viewport(self) -> None:
        self.assertFails(self.index_with(
            '<meta name="viewport" content="width=device-width, initial-scale=1">', ""),
            "no viewport meta")


class Headings(A11yCase):
    def test_zero_or_two_h1(self) -> None:
        self.assertFails(self.index_with("<h1>Cubentia</h1>", "<h2>Cubentia</h2>"),
                         "0 <h1> elements", "first heading is <h2>")
        self.assertFails(self.index_with("<h2>Section</h2>", "<h1>Section</h1>"),
                         "2 <h1> elements")

    def test_first_heading_not_h1(self) -> None:
        self.assertFails(self.index_with('<main id="main">', '<main id="main">\n    <h2>Intro</h2>'),
                         "first heading is <h2>")

    def test_skipped_level(self) -> None:
        self.assertFails(self.index_with("<h3>Detail</h3>", "<h4>Detail</h4>"),
                         "skips from <h2> to <h4>")
        self.assertFails(self.index_with("<h2>Section</h2>", "<h3>Section</h3>"),
                         "skips from <h1> to <h3>")

    def test_going_back_up_is_fine(self) -> None:
        # h1 h2 h3 h4 then back to h2: only a skip going down fails.
        self.assertPasses(self.index_with("<h3>Detail</h3>", "<h3>Detail</h3><h4>Deeper</h4>"))


class SkipLink(A11yCase):
    SKIP = '<a class="skip-link" href="#main">Skip to content</a>'

    def test_no_skip_link(self) -> None:
        self.assertFails(self.index_with(self.SKIP, ""),
                         "no skip link", "<a href='./'>")

    def test_another_link_first(self) -> None:
        self.assertFails(self.index_with(self.SKIP, '<a href="./">Home</a>\n  ' + self.SKIP),
                         "no skip link")

    def test_other_focusable_first(self) -> None:
        self.assertFails(self.index_with(self.SKIP, "<button>Menu</button>\n  " + self.SKIP),
                         "no skip link", "<button>")
        self.assertFails(self.index_with(self.SKIP, '<p tabindex="0">Hi</p>\n  ' + self.SKIP),
                         "no skip link", "<p>")

    def test_skip_link_without_text_or_wrong_target(self) -> None:
        self.assertFails(self.index_with(self.SKIP, '<a href="#main"></a>'), "no skip link")
        self.assertFails(self.index_with(self.SKIP, '<a href="#content">Skip to content</a>'),
                         "no skip link")

    def test_unfocusable_elements_before_are_fine(self) -> None:
        self.assertPasses(self.index_with(self.SKIP, '<a tabindex="-1" href="./">x</a>'
                                          '<input type="hidden" name="x">' + self.SKIP))


class Landmarks(A11yCase):
    def test_missing_landmark(self) -> None:
        for name in ("header", "footer"):
            with self.subTest(name=name):
                page = INDEX.replace(f'<{name} class="site-{name}">', "<div>", 1)
                page = page.replace(f"</{name}>", "</div>", 1)
                self.assertFails(page, f"0 <{name}> elements")
        page = self.index_with('<main id="main">', '<div id="main">').replace("</main>", "</div>")
        self.assertFails(page, "0 <main> elements")

    def test_two_main(self) -> None:
        self.assertFails(self.index_with("</main>", "</main>\n  <main><p>x</p></main>"),
                         "2 <main> elements")

    def test_main_without_id(self) -> None:
        self.assertFails(self.index_with('<main id="main">', '<main id="content">'),
                         'id="main"')
        self.assertFails(self.index_with('<main id="main">', "<main>"), 'id="main"')


class ImagesAndLinks(A11yCase):
    def test_img_without_alt(self) -> None:
        self.assertFails(self.index_with("<p>An offline tutor.</p>", '<img src="cube.png">'),
                         "<img src='cube.png'> has no alt")

    def test_img_with_alt_passes(self) -> None:
        self.assertPasses(self.index_with("<p>An offline tutor.</p>", '<img src="c.png" alt="">'))

    def test_empty_link(self) -> None:
        self.assertFails(self.index_with("<p>An offline tutor.</p>", '<p><a href="./"> </a></p>'),
                         "<a href='./'> has no text")
        self.assertFails(self.index_with("<p>An offline tutor.</p>",
                                         '<p><a href="./"><img src="c.png" alt=""></a></p>'),
                         "has no text")

    def test_image_link_with_alt_passes(self) -> None:
        self.assertPasses(self.index_with("<p>An offline tutor.</p>",
                                          '<p><a href="./"><img src="c.png" alt="Home"></a></p>'))

    def test_link_without_href(self) -> None:
        self.assertFails(self.index_with("<p>An offline tutor.</p>", "<p><a>Home</a></p>"),
                         "<a> with text 'Home' has no href")


class TabIndex(A11yCase):
    def test_positive_tabindex(self) -> None:
        self.assertFails(self.index_with("<p>An offline tutor.</p>",
                                         '<p tabindex="1">An offline tutor.</p>'),
                         "tabindex='1'")

    def test_zero_and_minus_one_pass(self) -> None:
        self.assertPasses(self.index_with("<p>An offline tutor.</p>",
                                          '<p tabindex="-1">An offline tutor.</p>'))
        self.assertPasses(self.index_with("<h2>Section</h2>", '<h2 tabindex="-1">Section</h2>'))


class Stylesheet(A11yCase):
    LINK = '<link rel="stylesheet" href="styles.css">'

    def test_other_stylesheet(self) -> None:
        self.assertFails(self.index_with(self.LINK, '<link rel="stylesheet" href="extra.css">'),
                         "'extra.css' is not the site's styles.css")

    def test_second_stylesheet(self) -> None:
        self.assertFails(self.index_with(self.LINK, self.LINK + '<link rel="stylesheet" '
                                                                'href="extra.css">'),
                         "loads 2 stylesheets")

    def test_no_stylesheet(self) -> None:
        self.assertFails(self.index_with(self.LINK, ""), "loads 0 stylesheets")

    def test_style_element(self) -> None:
        self.assertFails(self.index_with(self.LINK, self.LINK + "<style>p { color: red }</style>"),
                         "<style> element")

    def test_other_path_to_styles(self) -> None:
        for href in ("../styles.css", "/styles.css", "https://example.org/styles.css",
                     "sub/styles.css", "styles.css?v=1"):
            with self.subTest(href=href):
                self.assertFails(self.index_with(self.LINK, f'<link rel="stylesheet" href="{href}">'),
                                 "is not the site's styles.css")

    def test_absolute_project_path_passes(self) -> None:
        self.assertPasses(self.index_with(self.LINK, '<link rel="stylesheet" '
                                                     'href="/cubentia-site/styles.css">'))


if __name__ == "__main__":
    unittest.main()
