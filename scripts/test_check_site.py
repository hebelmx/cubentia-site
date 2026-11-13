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


class GeneratedText(SiteCase):
    """Problem 1: CSS-generated text is not page text the checker can read, so it is refused."""

    def test_content_string_in_stylesheet_fails(self) -> None:
        self.assertFails(self.build(css_extra='h1::after { content: "Rubik"; }'), "styles.css", "content")

    def test_content_string_in_style_element_and_attribute_fails(self) -> None:
        self.assertFails(self.build(head='<style>h1::after { content: "Cube"; }</style>'), "index.html", "content")
        self.assertFails(self.build(body='<p style="content: \'x\'">x</p>'), "index.html", "content")

    def test_escaped_content_property_fails(self) -> None:
        self.assertFails(self.build(css_extra=r'h1::after { \63 ontent: "x"; }'), "content")
        self.assertFails(self.build(css_extra=r'h1::after { content: "R\75 bik"; }'), "content", '"Rubik" in CSS')

    def test_content_from_var_attr_and_counters_fails(self) -> None:
        for value in ("var(--x)", "attr(title)", "counter(x)", 'counters(x, ".")', "open-quote", "url(a.png)"):
            with self.subTest(value=value):
                self.assertFails(self.build(css_extra=f"h1::after {{ content: {value}; }}",
                                            extra_files={"a.png": "x"}), "content")

    def test_attr_counter_functions_anywhere_fail(self) -> None:
        for name in ("attr", "counter", "counters"):
            with self.subTest(name=name):
                self.assertFails(self.build(css_extra=f"p {{ width: {name}(x); }}"), f"{name}()")

    def test_marker_content_fails(self) -> None:
        self.assertFails(self.build(css_extra='li::marker { content: "R"; }'), "content")

    def test_allowed_content_values_pass(self) -> None:
        self.assertPasses(self.build(css_extra='a::before { content: none; } b::after { content: normal; }'
                                               ' i::before { content: "" !important; }'))

    def test_rubik_in_any_css_string_fails(self) -> None:
        for css in (':root { --x: "Rubik"; }', 'p { font-family: "Rubik", sans-serif; }',
                    'a[title="rubik"] { color: red; }', 'p { font: 1rem "Ru\\62 ik"; }'):
            with self.subTest(css=css):
                self.assertFails(self.build(css_extra=css), '"Rubik" in CSS')

    def test_strings_outside_font_properties_fail(self) -> None:
        for css in ('p { text-overflow: "x"; }', 'p { quotes: "a" "b"; }', 'li { list-style-type: "x"; }',
                    'p { hyphenate-character: "x"; }', 'p { text-emphasis: "x"; }'):
            with self.subTest(css=css):
                self.assertFails(self.build(css_extra=css), "styles.css")

    def test_string_rendering_properties_via_var_fail(self) -> None:
        for prop in ("text-overflow", "hyphenate-character", "quotes", "list-style-type", "text-emphasis-style"):
            with self.subTest(prop=prop):
                self.assertFails(self.build(css_extra=f':root {{ --t: "x"; }} p {{ {prop}: var(--t); }}'), prop)

    def test_counters_and_counter_styles_fail(self) -> None:
        self.assertFails(self.build(css_extra="ol { counter-reset: list-item 17; }"), "counter-reset")
        self.assertFails(self.build(css_extra="li { counter-increment: list-item 3; }"), "counter-increment")
        self.assertFails(self.build(css_extra="@counter-style r { system: cyclic; symbols: R; }"), "@counter-style")
        self.assertFails(self.build(css_extra="ol { list-style-type: lower-alpha; }"), "list-style-type")

    def test_plain_list_styles_pass(self) -> None:
        self.assertPasses(self.build(css_extra="ul { list-style: none; } ol { list-style-type: decimal; }"))

    def test_font_stack_in_custom_property_passes(self) -> None:
        self.assertPasses(self.build(css_extra=':root { --font: "IBM Plex Sans", system-ui; } '
                                               'body { font-family: var(--font); }'))


class BaseElement(SiteCase):
    """Problem 2: <base> changes how every URL resolves, so fragment checks would lie."""

    def test_base_element_fails(self) -> None:
        self.assertFails(self.build(head='<base href="/cubentia-site/styles.css">'), "index.html", "<base>")

    def test_base_without_href_fails(self) -> None:
        self.assertFails(self.build(head='<base target="_blank">'), "<base>")


class DenyByDefault(SiteCase):
    """Problem 3: what a static HTML/CSS site never needs is refused outright."""

    def test_embedding_and_scripting_elements_fail(self) -> None:
        for tag in ("iframe", "frame", "frameset", "object", "embed", "applet", "portal", "script",
                    "noscript", "template", "slot"):
            with self.subTest(tag=tag):
                self.assertFails(self.build(body=f"<{tag}></{tag}>"), f"<{tag}>")

    def test_unknown_elements_fail(self) -> None:
        for tag in ("bdo", "marquee", "math", "plaintext", "xmp", "video", "audio", "canvas", "dialog"):
            with self.subTest(tag=tag):
                self.assertFails(self.build(body=f"<{tag}>x</{tag}>"), f"<{tag}>")

    def test_svg_foreign_object_fails(self) -> None:
        self.assertFails(self.build(body="<svg><foreignObject><p>x</p></foreignObject></svg>"), "<foreignobject>")

    def test_any_meta_http_equiv_fails(self) -> None:
        for equiv in ("refresh", "content-security-policy", "x-ua-compatible", "content-type", "default-style"):
            with self.subTest(equiv=equiv):
                self.assertFails(self.build(head=f'<meta http-equiv="{equiv}" content="0">'),
                                 f'<meta http-equiv="{equiv}">')

    def test_srcdoc_fails(self) -> None:
        self.assertFails(self.build(body='<p srcdoc="&lt;p&gt;x">x</p>'), "srcdoc")

    def test_same_origin_ping_fails(self) -> None:
        self.assertFails(self.build(body='<a href="#main" ping="/cubentia-site/">x</a>'), "ping")

    def test_srcset_parsing_keeps_commas_in_urls(self) -> None:
        self.assertFails(self.build(body='<img src="a.png" alt="A" srcset="data:image/png;base64,AA== 1x,'
                                         'https://x.com/b.png 2x">', extra_files={"a.png": "x"}),
                         "third-party", "https://x.com/b.png")

    def test_same_origin_srcset_passes(self) -> None:
        self.assertPasses(self.build(body='<img src="a.png" srcset="a.png 1x, b.png 2x" alt="A">',
                                     extra_files={"a.png": "x", "b.png": "x"}))

    def test_link_rels_outside_allowlist_fail(self) -> None:
        for rel in ("preload", "prefetch", "preconnect", "dns-prefetch", "modulepreload", "alternate",
                    "stylesheet preload", "import", ""):
            with self.subTest(rel=rel):
                self.assertFails(self.build(head=f'<link rel="{rel}" href="styles.css">'), "<link rel=")

    def test_allowed_link_rels_pass(self) -> None:
        self.assertPasses(self.build(head='<link rel="icon" href="a.png">'
                                          '<link rel="canonical" href="https://hebelmx.github.io/cubentia-site/">'
                                          '<link rel="manifest" href="m.webmanifest">',
                                     extra_files={"a.png": "x", "m.webmanifest": '{"name": "Cubentia"}'}))

    def test_event_handlers_fail(self) -> None:
        for attr in ("onload", "onerror", "onfocus", "onanimationstart", "ONCLICK"):
            with self.subTest(attr=attr):
                self.assertFails(self.build(body=f'<p {attr}="x">x</p>'), "event-handler")

    def test_javascript_url_in_any_attribute_fails(self) -> None:
        self.assertFails(self.build(body='<svg><a href="javascript:alert(1)"><text>x</text></a></svg>'), "script URL")
        self.assertFails(self.build(body='<p data-x=" java&#9;script:x">x</p>'), "script URL")

    def test_font_face_remote_src_fails(self) -> None:
        self.assertFails(self.build(css_extra='@font-face { font-family: X; src: local("X"), '
                                              'url(https://x.com/f.woff2) format("woff2"); }'), "third-party")
        self.assertFails(self.build(css_extra='@font-face { font-family: X; src: "https://x.com/f.woff2"; }'),
                         "CSS string in src")

    def test_font_face_local_passes(self) -> None:
        self.assertPasses(self.build(css_extra='@font-face { font-family: X; src: local("IBM Plex Sans"), '
                                               'url("styles.css") format("woff2"); }'))

    def test_css_at_rules_outside_allowlist_fail(self) -> None:
        for rule in ("@namespace url(http://www.w3.org/1999/xhtml);", "@document url(x) { a { color: red } }",
                     "@-moz-document url-prefix() { a { color: red } }", "@property --x { syntax: '*'; }",
                     "@font-feature-values X { @swash { a: 1; } }", "@page { margin: 0 }"):
            with self.subTest(rule=rule):
                self.assertFails(self.build(css_extra=rule), "at-rule")

    def test_css_functions_outside_allowlist_fail(self) -> None:
        for value in ('src("https://x.com/a.png")', 'image("https://x.com/a.png")',
                      "cross-fade(url(a.png), url(b.png))", "element(#main)", "paint(x)"):
            with self.subTest(value=value):
                self.assertFails(self.build(css_extra=f"body {{ background-image: {value}; }}"), "CSS function")

    def test_forbidden_word_in_text_attributes_fails(self) -> None:
        for attr in ("alt", "title", "aria-label", "aria-description", "placeholder", "value", "class", "id"):
            with self.subTest(attr=attr):
                self.assertFails(self.build(body=f'<form action="/cubentia-site/"><input {attr}="Rubik"></form>'),
                                 f"in {attr} of <input>")

    def test_forbidden_word_in_title_and_meta_fails(self) -> None:
        for meta in ('name="description"', 'property="og:title"', 'property="og:description"',
                     'name="twitter:title"', 'name="twitter:description"'):
            with self.subTest(meta=meta):
                self.assertFails(self.build(head=f'<meta {meta} content="A Rubik tutor">'), "content of <meta>")
        self.assertFails(self.build(head="<title>Rubik</title>"), "Rubik")

    def test_unknown_attributes_fail(self) -> None:
        for attr in ('attributionsrc="https://x.com/"', 'xml:base="https://x.com/"', 'lowsrc="https://x.com/a"',
                     'longdesc="https://x.com/"', 'manifest="x.appcache"', 'is="x-y"', 'popover'):
            with self.subTest(attr=attr):
                self.assertFails(self.build(body=f'<p {attr}>x</p>'), "attribute", "is not allowed")

    def test_unlisted_file_types_fail(self) -> None:
        for name in ("app.js", "feed.xml", "page.shtml", "a.swf"):
            with self.subTest(name=name):
                self.assertFails(self.build(extra_files={name: "x"}), name, "file type")


class SelfAttack(SiteCase):
    """Problem 4: bypasses tried against the checker after the deny-by-default rewrite."""

    def test_duplicate_attribute_fails(self) -> None:
        # Browsers use the first value; a dict of attributes keeps the last.
        self.assertFails(self.build(body='<img src="https://x.com/a.png" src="a.png" alt="A">',
                                    extra_files={"a.png": "x"}), "duplicate attribute src")

    def test_backslash_protocol_relative_url_fails(self) -> None:
        self.assertFails(self.build(body='<img src="/\\x.com/a.png" alt="A">'), "third-party")

    def test_markup_in_svg_title_fails(self) -> None:
        # In <svg>, <title> is not raw text: the browser parses the <img>, which breaks out.
        self.assertFails(self.build(body="<svg><title><img src=//x.com/a.png></title></svg>"), "markup inside")

    def test_markup_in_svg_style_fails(self) -> None:
        self.assertFails(self.build(body="<svg><style><img src=//x.com/a.png></style></svg>"), "'<' in CSS")

    def test_non_css_stylesheet_fails(self) -> None:
        # Quirks-mode pages accept same-origin stylesheets of any MIME type.
        self.assertFails(self.build(head='<link rel="stylesheet" href="x.txt">',
                                    extra_files={"x.txt": "@import url(https://x.com/a.css);"}), "must be a .css file")
        self.assertFails(self.build(css_extra="@import url(x.txt);", extra_files={"x.txt": "a{}"}),
                         "must be a .css file")

    def test_xml_stylesheet_processing_instruction_fails(self) -> None:
        svg = '<?xml-stylesheet href="https://x.com/a.css"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'
        self.assertFails(self.build(extra_files={"logo.svg": svg}), "logo.svg", "processing instruction")

    def test_xml_declaration_passes(self) -> None:
        svg = '<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'
        self.assertPasses(self.build(extra_files={"logo.svg": svg}))

    def test_dtd_entity_fails(self) -> None:
        svg = ('<!DOCTYPE svg [<!ENTITY r "Rubik">]><svg xmlns="http://www.w3.org/2000/svg">'
               '<text>&r;</text></svg>')
        self.assertFails(self.build(extra_files={"logo.svg": svg}), "logo.svg", "<!DOCTYPE")

    def test_cdata_text_is_checked(self) -> None:
        self.assertFails(self.build(body="<svg><text><![CDATA[Rubik]]></text></svg>"), "Rubik")

    def test_combining_marks_and_look_alikes_fail(self) -> None:
        for word in ("Rúbik", "Rubіk", "Ꭱubik", "Rubiк", "Ｒｕｂｉｋ",
                     "\U0001d411\U0001d42e\U0001d41b\U0001d422\U0001d424", "RUBİK"):
            with self.subTest(word=ascii(word)):
                self.assertFails(self.build(body=f"<p>{word}</p>"), "Rubik")

    def test_bidi_reversal_fails(self) -> None:
        self.assertFails(self.build(body="<p>‮kibuR‬</p>"), "bidi")
        self.assertFails(self.build(body='<p title="‮kibuR">x</p>'), "bidi")
        self.assertFails(self.build(body="<p>kibuR</p>"), "Rubik")
        self.assertFails(self.build(css_extra=".a { unicode-bidi: bidi-override; }"), "unicode-bidi")
        self.assertFails(self.build(css_extra=".a { direction: rtl; }"), "direction")

    def test_url_mask_does_not_hide_word(self) -> None:
        self.assertFails(self.build(body="<p>the //Rubik method</p>"), "Rubik")
        self.assertFails(self.build(body="<p>see rubik.com/cube</p>"), "Rubik")

    def test_word_split_by_hidden_element_fails(self) -> None:
        self.assertFails(self.build(body="<p>Ru<span hidden>xx</span>bik</p>"), "split across elements")
        self.assertFails(self.build(body="<p>R<span hidden>1</span>u<span hidden>2</span>bik</p>"),
                         "split across elements")

    def test_word_split_in_reverse_or_any_order_fails(self) -> None:
        self.assertFails(self.build(body='<svg><text x="30">bik</text><text x="0">Ru</text></svg>'), "split across")
        self.assertFails(self.build(body="<p><span>ik</span> <span>Ru</span> <span>b</span></p>"), "split across")

    def test_reordering_css_fails(self) -> None:
        self.assertFails(self.build(css_extra=".a { order: 2; }"), "order")
        self.assertFails(self.build(css_extra=".a { flex-direction: row-reverse; }"), "flex-direction")
        self.assertFails(self.build(css_extra=".a { flex-flow: wrap-reverse; }"), "flex-flow")

    def test_per_glyph_svg_positions_fail(self) -> None:
        self.assertFails(self.build(body='<svg><text x="10 0 20 30 40">uRbik</text></svg>'), "per-glyph x")

    def test_word_from_alt_or_field_beside_text_fails(self) -> None:
        self.assertFails(self.build(body='<p>Ru<img src="a.png" alt="bik"></p>', extra_files={"a.png": "x"}), "Rubik")
        self.assertFails(self.build(body='<form action="/cubentia-site/"><label>Ru<input value="bik">'
                                         '</label></form>'), "Rubik")

    def test_non_utf8_charset_fails(self) -> None:
        self.assertFails(self.build(head='<meta charset="iso-2022-jp">'), "charset")

    def test_data_url_outside_image_contexts_fails(self) -> None:
        self.assertFails(self.build(css_extra='p { filter: url("data:image/svg+xml,x#f"); }'), "data: URL in CSS filter")
        self.assertFails(self.build(body='<svg><use href="data:image/svg+xml,x#a"/></svg>'), "data: URL in href")
        self.assertFails(self.build(head='<link rel="stylesheet" href="data:image/png,x">'), "non-image data:")

    def test_manifest_strings_are_checked(self) -> None:
        self.assertFails(self.build(extra_files={"m.webmanifest": '{"icons": [{"src": "https://x.com/i.png"}]}'}),
                         "m.webmanifest", "third-party")
        self.assertFails(self.build(extra_files={"m.webmanifest": '{"name": "Rubik tutor"}'}), "m.webmanifest", "Rubik")

    def test_unterminated_comment_in_style_element_still_checks_next_block(self) -> None:
        self.assertFails(self.build(head='<style>a{color:red}/* </style><style>h1::after{content:"x"}</style>'),
                         "content")

    def test_ordinary_text_and_css_pass(self) -> None:
        self.assertPasses(self.build(body='<svg viewBox="0 0 10 10"><text x="1" y="5">Cube</text></svg>'
                                          "<p>A cube &lt;b&gt; tutor, one step at a time.</p>",
                                     css_extra='a > b { color: red; } a[title="x"] { color: blue; }'))


if __name__ == "__main__":
    unittest.main()
