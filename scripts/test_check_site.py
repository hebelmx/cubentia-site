"""Self-tests for check_site.py: each case builds a temporary site and asserts pass or fail.

Run: python3 -m unittest discover -s scripts -p 'test_*.py' -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_site import check_site  # noqa: E402

CHECKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_site.py")

INDEX = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Cubentia</title>
  <link rel="stylesheet" href="styles.css">
  {head}
</head>
<body>
  <a href="#main">Skip to content</a>
  <main id="main">
    <h1>Cubentia</h1>
    <p><a href="https://github.com/hebelmx/RubikXplorer">Source</a></p>
    {body}
  </main>
</body>
</html>
"""

NOT_FOUND = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Page not found</title>
  <link rel="stylesheet" href="/cubentia-site/styles.css">
</head>
<body>
  <main id="main"><h1>Page not found</h1><a href="/cubentia-site/">Home</a>{body}</main>
</body>
</html>
"""

CSS = """:root { --accent: #5b3fd1; }
body { color: var(--accent); }
{extra}
"""


class SiteCase(unittest.TestCase):
    def build(self, head: str = "", body: str = "", css_extra: str = "", not_found_body: str = "",
              extra_files: dict[str, str] | None = None) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        site = os.path.join(tmp.name, "site")
        files = {
            "index.html": INDEX.format(head=head, body=body),
            "404.html": NOT_FOUND.format(body=not_found_body),
            "styles.css": CSS.replace("{extra}", css_extra),
        }
        files.update(extra_files or {})
        for rel, content in files.items():
            path = os.path.join(site, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        return site

    def assertPasses(self, site: str) -> None:
        self.assertEqual(check_site(site), [])

    def assertFails(self, site: str, *needles: str) -> list[str]:
        problems = check_site(site)
        self.assertTrue(problems, "expected the checker to fail")
        joined = "\n".join(problems)
        for needle in needles:
            self.assertIn(needle, joined)
        return problems


class CleanSite(SiteCase):
    def test_clean_site_passes(self) -> None:
        self.assertPasses(self.build())

    def test_nested_page_and_relative_links_pass(self) -> None:
        nested = INDEX.format(head="", body='<a href="../">Home</a> <a href="../#main">Top</a>')
        nested = nested.replace('href="styles.css"', 'href="../styles.css"')
        site = self.build(body='<a href="privacy/">Privacy</a> <a href="privacy/#main">Main</a>',
                          extra_files={"privacy/index.html": nested})
        self.assertPasses(site)

    def test_external_navigation_and_mailto_pass(self) -> None:
        self.assertPasses(self.build(body='<a href="mailto:hello@example.com">Mail</a>'
                                          '<a href="//example.org/">Ex</a>'))

    def test_same_origin_css_url_passes(self) -> None:
        site = self.build(css_extra='@font-face { src: url("fonts/plex.woff2"); }',
                          extra_files={"fonts/plex.woff2": "x"})
        self.assertPasses(site)

    def test_shipped_site_passes(self) -> None:
        shipped = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site")
        self.assertPasses(shipped)

    def test_cli_exit_codes(self) -> None:
        good = subprocess.run([sys.executable, CHECKER, self.build()], capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
        self.assertIn("OK", good.stdout)
        bad_site = self.build(body='<a href="missing/">Gone</a>')
        bad = subprocess.run([sys.executable, CHECKER, bad_site], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("index.html: broken link 'missing/'", bad.stdout)


class BrokenLinks(SiteCase):
    def test_missing_directory_link_fails(self) -> None:
        self.assertFails(self.build(body='<a href="missing/">Gone</a>'), "index.html", "missing/")

    def test_missing_file_link_fails(self) -> None:
        self.assertFails(self.build(body='<a href="nope.html">Gone</a>'), "nope.html")

    def test_missing_fragment_fails(self) -> None:
        self.assertFails(self.build(body='<a href="#nope">Gone</a>'), "index.html", "#nope")

    def test_missing_fragment_on_other_page_fails(self) -> None:
        self.assertFails(self.build(body='<a href="404.html#nope">Gone</a>'), "#nope")

    def test_link_outside_site_root_fails(self) -> None:
        self.assertFails(self.build(body='<a href="../outside.html">Out</a>'), "outside the site root")

    def test_missing_stylesheet_fails(self) -> None:
        self.assertFails(self.build(head='<link rel="stylesheet" href="other.css">'), "other.css")


class ThirdPartyResources(SiteCase):
    def test_google_fonts_link_fails(self) -> None:
        self.assertFails(
            self.build(head='<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans">'),
            "third-party", "fonts.googleapis.com")

    def test_protocol_relative_img_fails(self) -> None:
        self.assertFails(self.build(body='<img src="//x.com/a.png" alt="A">'), "third-party", "//x.com/a.png")

    def test_srcset_fails(self) -> None:
        self.assertFails(self.build(body='<img src="a.png" srcset="https://x.com/a2.png 2x" alt="A">',
                                    extra_files={"a.png": "x"}), "third-party")

    def test_css_url_fails(self) -> None:
        self.assertFails(self.build(css_extra="body { background: url(https://x.com/bg.png); }"),
                         "styles.css", "third-party")

    def test_css_import_url_fails(self) -> None:
        self.assertFails(self.build(css_extra='@import url(https://fonts.googleapis.com/css2);'),
                         "styles.css", "third-party")

    def test_css_import_string_fails(self) -> None:
        self.assertFails(self.build(css_extra='@import "http://x.com/a.css";'), "styles.css", "third-party")

    def test_inline_style_url_fails(self) -> None:
        self.assertFails(self.build(body='<p style="background:url(\'https://x.com/a.png\')">x</p>'),
                         "third-party")

    def test_style_element_import_fails(self) -> None:
        self.assertFails(self.build(head='<style>@import url("https://x.com/a.css");</style>'), "third-party")

    def test_ping_fails(self) -> None:
        self.assertFails(self.build(body='<a href="#main" ping="/cubentia-site/ https://x.com/t">x</a>'),
                         "third-party", "https://x.com/t")

    def test_form_action_fails(self) -> None:
        self.assertFails(self.build(body='<form action="https://x.com/f"><button>Go</button></form>'),
                         "third-party", "https://x.com/f")

    def test_formaction_fails(self) -> None:
        self.assertFails(self.build(body='<form><button formaction="https://x.com/f">Go</button></form>'),
                         "third-party", "https://x.com/f")

    def test_meta_refresh_fails(self) -> None:
        self.assertFails(self.build(head='<meta http-equiv="refresh" content="0;url=https://x.com/">'),
                         "third-party", "https://x.com/")

    def test_xlink_href_fails(self) -> None:
        self.assertFails(self.build(body='<svg><image xlink:href="https://x.com/a.png"/></svg>'),
                         "third-party", "https://x.com/a.png")

    def test_css_image_set_string_fails(self) -> None:
        self.assertFails(self.build(css_extra='body { background-image: image-set("https://x.com/a.png" 1x); }'),
                         "styles.css", "third-party")

    def test_css_webkit_image_set_string_fails(self) -> None:
        self.assertFails(self.build(css_extra="body { background-image: -webkit-image-set('//x.com/a.png' 1x); }"),
                         "styles.css", "third-party")

    def test_css_image_set_type_passes(self) -> None:
        self.assertPasses(self.build(css_extra='body { background-image: image-set("styles.css" type("image/avif")); }'))

    def test_video_poster_fails(self) -> None:
        self.assertFails(self.build(body='<video poster="https://x.com/p.png"></video>'), "third-party")

    def test_object_data_fails(self) -> None:
        self.assertFails(self.build(body='<object data="https://x.com/a"></object>'), "third-party", "<object>")

    def test_td_background_fails(self) -> None:
        self.assertFails(self.build(body='<table><tr><td background="https://x.com/a.png">x</td></tr></table>'),
                         "third-party")

    def test_preload_imagesrcset_fails(self) -> None:
        self.assertFails(self.build(head='<link rel="preload" as="image" href="styles.css" '
                                         'imagesrcset="https://x.com/a.png 1x">'), "third-party")

    def test_embed_fails(self) -> None:
        self.assertFails(self.build(body='<embed src="a.swf">'), "<embed>")

    def test_img_data_uri_passes(self) -> None:
        self.assertPasses(self.build(body='<img src="data:image/png;base64,iVBORw0KGgo=" alt="Dot">'))

    def test_css_data_image_url_passes(self) -> None:
        self.assertPasses(self.build(css_extra='body { background: url("data:image/svg+xml,%3Csvg%3E"); }'))

    def test_data_stylesheet_link_fails(self) -> None:
        self.assertFails(self.build(head='<link rel="stylesheet" '
                                         'href="data:text/css,@import url(https://example.com/x.css)">'),
                         "index.html", "non-image data:")

    def test_data_stylesheet_link_uppercase_with_params_fails(self) -> None:
        self.assertFails(self.build(head='<link rel="stylesheet" href=" DATA:Text/CSS;charset=utf-8,a{}">'),
                         "non-image data:")

    def test_data_url_without_media_type_fails(self) -> None:
        self.assertFails(self.build(head='<link rel="stylesheet" href="data:,a{}">'), "non-image data:")

    def test_data_html_form_action_fails(self) -> None:
        self.assertFails(self.build(body='<form action="data:text/html,<img src=//x.com/a>"><button>Go</button></form>'),
                         "non-image data:")

    def test_css_import_data_url_fails(self) -> None:
        self.assertFails(self.build(css_extra='@import url("data:text/css,@import url(https://x.com/a.css)");'),
                         "styles.css", "non-image data:")

    def test_css_import_data_string_fails(self) -> None:
        self.assertFails(self.build(css_extra="@import 'data:text/css,a{}';"), "styles.css", "non-image data:")

    def test_css_hex_escaped_url_fails(self) -> None:
        self.assertFails(self.build(css_extra=r"body { background-image: u\72l(https://example.com/tracker.png); }"),
                         "styles.css", "third-party", "https://example.com/tracker.png")

    def test_css_hex_escape_with_trailing_space_fails(self) -> None:
        self.assertFails(self.build(css_extra="body { background: \\75 rl(https://x.com/a.png); }"),
                         "styles.css", "third-party")

    def test_css_escaped_literal_url_fails(self) -> None:
        self.assertFails(self.build(css_extra=r"body { background: u\r\l(https\:\/\/x.com/a.png); }"),
                         "styles.css", "third-party", "https://x.com/a.png")

    def test_css_escaped_import_fails(self) -> None:
        self.assertFails(self.build(css_extra=r'@\69mport "https://x.com/a.css";'), "styles.css", "third-party")

    def test_css_escaped_image_set_fails(self) -> None:
        self.assertFails(self.build(css_extra=r'body { background-image: \69mage-set("https://x.com/a.png" 1x); }'),
                         "styles.css", "third-party")

    def test_css_comment_inside_function_name_is_removed(self) -> None:
        self.assertFails(self.build(css_extra="body { background: u/**/rl(https://x.com/a.png); }"),
                         "styles.css", "third-party")

    def test_css_escaped_url_in_style_attribute_fails(self) -> None:
        self.assertFails(self.build(body=r'<p style="background:u\72l(https://x.com/a.png)">x</p>'),
                         "index.html", "third-party")

    def test_css_escaped_import_in_style_element_fails(self) -> None:
        self.assertFails(self.build(head=r'<style>@\69 mport url("https://x.com/a.css");</style>'),
                         "index.html", "third-party")

    def test_css_escaped_whitespace_keeps_url_detected(self) -> None:
        self.assertFails(self.build(css_extra=r"body { background: url(https://x.com/a\20 b.png); }"),
                         "styles.css", "third-party")

    def test_svg_external_xlink_href_fails(self) -> None:
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
               '<image xlink:href="https://x.com/a.png"/></svg>')
        self.assertFails(self.build(extra_files={"logo.svg": svg}), "logo.svg", "third-party")

    def test_svg_set_href_in_svg_file_fails(self) -> None:
        svg = ('<svg xmlns="http://www.w3.org/2000/svg"><image width="1" height="1">'
               '<set attributeName="href" to="https://example.com/tracker.svg"/></image></svg>')
        self.assertFails(self.build(extra_files={"logo.svg": svg}), "logo.svg", "SVG animation <set>")

    def test_svg_animation_elements_inline_fail(self) -> None:
        for tag in ("set", "animate", "animateMotion", "animateTransform"):
            with self.subTest(tag=tag):
                body = (f'<svg width="1" height="1"><image width="1" height="1">'
                        f'<{tag} attributeName="href" values="https://x.com/a.svg"/></image></svg>')
                self.assertFails(self.build(body=body), "index.html", f"SVG animation <{tag.lower()}>")

    def test_attribute_name_on_any_element_fails(self) -> None:
        svg = ('<svg xmlns="http://www.w3.org/2000/svg">'
               '<rect attributeName="xlink:href" from="a" to="https://x.com/a.svg"/></svg>')
        self.assertFails(self.build(extra_files={"logo.svg": svg}), "logo.svg", "attributeName")

    def test_iframe_fails(self) -> None:
        self.assertFails(self.build(body='<iframe src="https://x.com/" title="x"></iframe>'), "<iframe>")


class ScriptsAndCookies(SiteCase):
    def test_script_element_fails(self) -> None:
        self.assertFails(self.build(body="<script>console.log(1)</script>"), "<script>")

    def test_same_origin_script_file_fails(self) -> None:
        self.assertFails(self.build(head='<script src="app.js"></script>', extra_files={"app.js": ""}),
                         "<script>")

    def test_event_handler_fails(self) -> None:
        self.assertFails(self.build(body='<p onclick="x()">Hi</p>'), "onclick")

    def test_javascript_url_fails(self) -> None:
        self.assertFails(self.build(body='<a href="javascript:void(0)">x</a>'), "javascript:")

    def test_javascript_url_with_tab_entity_fails(self) -> None:
        self.assertFails(self.build(body='<a href="java&#9;script:alert(1)">x</a>'), "script URL")

    def test_vbscript_url_fails(self) -> None:
        self.assertFails(self.build(body='<a href="vbscript:msgbox(1)">x</a>'), "script URL")

    def test_svg_script_fails(self) -> None:
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        self.assertFails(self.build(extra_files={"logo.svg": svg}), "logo.svg", "<script>")

    def test_htm_page_is_checked(self) -> None:
        self.assertFails(self.build(extra_files={"old.htm": "<p onclick=\"x()\">Hi</p>"}), "old.htm", "onclick")

    def test_uppercase_extensions_are_checked(self) -> None:
        site = self.build(extra_files={"OLD.HTML": "<script></script>",
                                       "EXTRA.CSS": "@import url(https://x.com/a.css);"})
        self.assertFails(site, "OLD.HTML", "<script>", "EXTRA.CSS", "third-party")

    def test_set_cookie_meta_fails(self) -> None:
        self.assertFails(self.build(head='<meta http-equiv="Set-Cookie" content="a=b">'), "set-cookie")


class RequiredPages(SiteCase):
    def test_missing_index_fails(self) -> None:
        site = self.build()
        os.remove(os.path.join(site, "index.html"))
        self.assertFails(site, "index.html: required page does not exist")

    def test_missing_404_fails(self) -> None:
        site = self.build()
        os.remove(os.path.join(site, "404.html"))
        self.assertFails(site, "404.html: required page does not exist")

    def test_empty_site_directory_fails(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        problems = self.assertFails(tmp.name, "required page does not exist")
        self.assertEqual(len(problems), 2)

    def test_missing_site_directory_fails(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.assertFails(os.path.join(tmp.name, "site"), "site directory does not exist")


class UnreadableFiles(SiteCase):
    def test_non_utf8_css_is_reported(self) -> None:
        site = self.build()
        with open(os.path.join(site, "extra.css"), "wb") as handle:
            handle.write(b"body { color: \xff\xfe; }")
        self.assertFails(site, "extra.css", "cannot be read as UTF-8")


class RootPaths(SiteCase):
    def test_root_path_outside_project_fails(self) -> None:
        self.assertFails(self.build(body='<a href="/privacy/">Privacy</a>'), "escapes the project path")

    def test_root_resource_outside_project_fails(self) -> None:
        self.assertFails(self.build(head='<link rel="stylesheet" href="/styles.css">'), "escapes the project path")

    def test_project_root_path_passes(self) -> None:
        self.assertPasses(self.build(body='<a href="/cubentia-site/">Home</a>'
                                          '<a href="/cubentia-site">Home</a>'
                                          '<a href="/cubentia-site/#main">Main</a>'))

    def test_missing_project_path_fails(self) -> None:
        self.assertFails(self.build(body='<a href="/cubentia-site/privacy/">Privacy</a>'), "broken link")

    def test_relative_link_in_404_fails(self) -> None:
        self.assertFails(self.build(not_found_body='<a href="index.html">Home</a>'), "404.html", "relative path")


class RubikText(SiteCase):
    def test_rubik_in_text_fails(self) -> None:
        self.assertFails(self.build(body="<p>Solve your RUBIK cube.</p>"), "Rubik")

    def test_rubiks_in_title_fails(self) -> None:
        site = self.build()
        path = os.path.join(site, "index.html")
        with open(path, encoding="utf-8") as handle:
            content = handle.read().replace("<title>Cubentia</title>", "<title>Rubik's tutor</title>")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        self.assertFails(site, "Rubik")

    def test_rubik_in_alt_fails(self) -> None:
        self.assertFails(self.build(body='<img src="a.png" alt="A rubik cube">', extra_files={"a.png": "x"}),
                         "alt")

    def test_rubik_in_aria_label_fails(self) -> None:
        self.assertFails(self.build(body='<a href="#main" aria-label="Rubik">x</a>'), "aria-label")

    def test_rubik_in_meta_content_fails(self) -> None:
        self.assertFails(self.build(head='<meta name="description" content="A Rubik tutor">'), "meta")

    def test_rubik_in_value_label_roledescription_abbr_fails(self) -> None:
        for attr in ("value", "label", "aria-roledescription", "abbr"):
            with self.subTest(attr=attr):
                self.assertFails(self.build(body=f'<p {attr}="Rubik">x</p>'), attr)

    def test_rubik_split_by_inline_markup_fails(self) -> None:
        self.assertFails(self.build(body="<p>Ru<span>bik</span></p>"), "Rubik", "page text")

    def test_rubik_split_by_nested_markup_and_comment_fails(self) -> None:
        self.assertFails(self.build(body="<p>R<b>u<i>b</i></b><!-- x -->ik cube</p>"), "Rubik")

    def test_rubik_split_by_invisible_characters_fails(self) -> None:
        for char in ("\u00ad", "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"):
            with self.subTest(char=hex(ord(char))):
                self.assertFails(self.build(body=f"<p>Ru{char}bik</p>"), "Rubik")

    def test_rubik_split_by_character_references_fails(self) -> None:
        self.assertFails(self.build(body="<p>Ru&shy;b&#x200B;&#105;k</p>"), "Rubik")

    def test_rubik_with_character_references_in_attributes_fails(self) -> None:
        for attr in ("title", "alt", "aria-label"):
            with self.subTest(attr=attr):
                self.assertFails(self.build(body=f'<p {attr}="Ru&shy;b&#8203;ik">x</p>'), attr)

    def test_rubik_split_in_title_and_meta_description_fails(self) -> None:
        site = self.build(head='<meta name="description" content="A R&#x75;&#xAD;bik tutor">')
        self.assertFails(site, "meta")
        site = self.build()
        path = os.path.join(site, "index.html")
        with open(path, encoding="utf-8") as handle:
            content = handle.read().replace("<title>Cubentia</title>", "<title>Ru&#8288;bik tutor</title>")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        self.assertFails(site, "Rubik")

    def test_rubik_after_url_in_separate_element_fails(self) -> None:
        self.assertFails(self.build(body="<p>see x.com/</p><p>Rubik cube</p>"), "Rubik")

    def test_rubik_in_href_passes(self) -> None:
        self.assertPasses(self.build(body='<a href="https://github.com/hebelmx/RubikXplorer/issues">Issues</a>'))

    def test_rubik_in_url_text_passes(self) -> None:
        self.assertPasses(self.build(body="<p>https://github.com/hebelmx/RubikXplorer</p>"))


if __name__ == "__main__":
    unittest.main()
