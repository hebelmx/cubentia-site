# cubentia-site

Public website and privacy policy for Cubentia, an offline Android cube tutor from Exxerpro Solutions, S.A. de C.V. (published on Google Play as Abel Briones).

The site is plain static HTML and one CSS file. It has no JavaScript, no cookies, no tracking and no resource from another origin. GitHub Pages publishes it from GitHub Actions.

## URL

- Site: <https://hebelmx.github.io/cubentia-site/>
- No custom domain. Checked on 2026-11-13: the Pages API reports `cname: null` with HTTPS enforced, and the account has no user site (`hebelmx/hebelmx.github.io` does not exist), so nothing sets a domain for project sites. If a custom domain is added later, GitHub redirects the `github.io` URLs to it.
- Privacy policy: <https://hebelmx.github.io/cubentia-site/privacy/>. Play Console registers that URL, so it must never change.

## Rules

- **Never rename this repository.** A GitHub Pages project URL contains the repository name, and GitHub does not redirect Pages URLs after a rename. Renaming `cubentia-site` would break the privacy-policy URL registered in Play Console.
- **Do not call it `cubentia`.** The app repository will be renamed to `cubentia`, and repository names are case-insensitive, so the two would collide. The app repository can be renamed without affecting this site.
- **No tester or pilot data in this repository.** No tester names, emails, group addresses or opt-in links, in files, commit messages, issues or pull requests. Exxerpro Solutions, S.A. de C.V. (published on Google Play as Abel Briones) is named as the publisher, and the published contact address is the only email on the site. The maintainer's own git author identity, which is the same as in the app repository, is not covered by this rule.
- **Nothing from another origin.** Every `src`, `<link href>`, CSS `url()` and `@import` is same-origin. Links (`<a href>`) to other sites are fine. Fonts use a system font stack; IBM Plex Sans is used only if already installed.
- **Deny by default.** `scripts/check_site.py` accepts only allowlisted elements, attributes, `<link rel>` values (stylesheet, icon, canonical, manifest), CSS at-rules, CSS functions and file types. CSS may not generate text (`content` is only `none`, `normal` or `""`), and `<base>`, `<meta http-equiv>`, scripts and embeds are rejected. If a new page needs something that is refused, narrow the rule in the checker and add a test.
- **Paths under `/cubentia-site/`.** Pages at fixed paths use relative links. `404.html` is served at any missing path, so it uses absolute `/cubentia-site/...` paths. A root-relative path outside `/cubentia-site/` fails the check.
- Use LF line endings and Conventional Commits.

## Layout

```text
site/                  published as-is
  index.html           landing page
  privacy/index.html   privacy policy (statements checked against the app's privacy facts)
  support/index.html   support page: the contact email for help and problem reports (the same
                       address as privacy-policy.json contactEmail; test_support_page.py checks it)
  404.html             custom "Page not found" page
  styles.css           the one stylesheet (design tokens, light and dark)
scripts/
  check.sh             the check CI runs; run it locally before a PR
  check_site.py        link, third-party, script/cookie, root-path and wording checks (stdlib Python)
  test_check_site.py   self-tests for check_site.py
  check_a11y.py        accessibility structure checks on every page (stdlib Python)
  test_check_a11y.py   self-tests for check_a11y.py
  check_privacy.py     privacy-policy check against the app's facts and the policy record
  test_check_privacy.py  self-tests for check_privacy.py
  test_support_page.py  self-test: the support page's only email is privacy-policy.json contactEmail
  fixtures/privacy-facts.json  copy of the app's v1.0 facts, the local default
  preview.sh           local preview under /cubentia-site/
.github/workflows/
  pages.yml            check, then deploy to GitHub Pages
privacy-policy.json    policy record: publisher, contact, effective date, text hash (not published)
```

## Check locally

Needs Python 3, Node.js (for `npx`) and Java 11 or later.

```sh
bash scripts/check.sh
```

It runs the checkers' self-tests, then `scripts/check_site.py site`, then `scripts/check_a11y.py site`, then `scripts/check_privacy.py` (below), then the Nu Html Checker (`vnu-jar`, version pinned in `check.sh`) on the HTML and CSS. It exits non-zero on any problem. `check_site.py`, `check_a11y.py` and `check_privacy.py` print each problem as `file: message`; the Nu Html Checker uses its own format.

`check_a11y.py` checks the structure of every page for WCAG 2.2 AA: `<html lang="en">`, a non-empty title, a viewport that allows zoom, one `header`, `main id="main"` and `footer`, a skip link as the first focusable element, one `h1` with heading levels that never skip, `alt` on every image, an `href` and text on every link, no `tabindex` other than `0` or `-1`, exactly one stylesheet, `styles.css`, and no `<style>` element or `style=""` attribute, so all of each page's CSS is in the stylesheet the contrast check reads. No text may take its color from anywhere else (deny by default): on every page (`.html`, `.htm`, `.xhtml`) only the elements `check_site.py` allows and SVG elements that cannot render text are accepted, so SVG `text`, `tspan`, `textPath`, `foreignObject`, MathML and the like fail, as do the legacy color attributes (`color`, `bgcolor`, `text`, `link`, `vlink`, `alink`), `placeholder` and `data:` URLs other than raster images. Every `.svg` file may hold only SVG elements that cannot render text. Keyboard flow, 200% zoom, 320 CSS px reflow and the rendered light and dark themes are checked by hand and recorded in the pull request.

`PRIVACY_BASE` names the git revision the privacy check compares the policy with (see [Changing the policy](#changing-the-policy)); it must be set on CI and is optional locally.

`PRIVACY_FACTS` names the app's `docs/privacy-facts.json`. On CI (`GITHUB_ACTIONS=true`) it must be set and the file must exist. Locally, when it is unset, `check.sh` uses `scripts/fixtures/privacy-facts.json` (a copy of the app's v1.0 file) and says so, so no network is needed. To check against a local app checkout:

```sh
PRIVACY_FACTS=../RubikXplorer/docs/privacy-facts.json bash scripts/check.sh
```

## Privacy facts check

The app's build exports what it can prove about privacy to `docs/privacy-facts.json` in the app repository. `scripts/check_privacy.py` holds the policy page to that file:

- Every key except `schemaVersion` is a fact. The page states each one on an element with `data-fact="<key>"`, `data-value="<value>"` and plain-language text. `<value>` is the raw string for a JSON string and compact JSON otherwise (`false`, `[]`). `schemaVersion` must be `1`.
- The check fails when a fact has no statement with an equal value, when the page states a fact the file lacks, when statements conflict or are empty, or when the file is missing, invalid or has another `schemaVersion`.
- `data-policy` markers name the publisher, the Play developer, the contact (`<a href="mailto:…">`) and the effective date (`<time datetime="YYYY-MM-DD">`). They must match `privacy-policy.json`, and the contact must be the only email on the page.
- Each supported fact value has one approved sentence (`APPROVED_WORDING` in `scripts/check_privacy.py`), and the statement's text must equal it (whitespace is normalized). A value with no approved sentence yet, such as `internetPermission: true`, fails with an error naming the fact: add the sentence to `APPROVED_WORDING` and the page together. A statement must be a `<p>` or `<li>` inside `<main>`, with no attribute besides `data-fact` and `data-value`, no hiding element or attribute around it (only `id`, `lang`, `dir` on `div`, `section`, `article`, `ul`, `ol`, `main`), and only attribute-free `<code>`, `<em>` or `<strong>` inside it. The page may not have a `<style>` or `<base>` element.
- Every stylesheet the policy page loads (each `<link rel="stylesheet">`, then each `@import`, as plain relative `.css` paths) and every `style=""` attribute on it may hold only allowlisted CSS (`CSS_PROPERTIES` and `CSS_TOKENS` in `scripts/check_privacy.py`), whatever the selector, so no rule can hide a statement: no `display: none`, `visibility`, `opacity`, positioning, `transform`, `clip`, `width`/`height`, `overflow`, `text-indent`, `content`, or font sizes under `0.875rem`. Colors are only the design tokens, set on `:root` (and overridden only in `@media (prefers-color-scheme: dark)`), and in both schemes every text color (the ink tokens and the browser's default text colors) must reach 4.5:1 against every background, and every color token used in a `border` or `outline` must reach 3:1 (WCAG 1.4.11). The browser's default link colors are checked only when no rule with selector `a` sets `color`. CSS escapes, `!important`, nested rules and other at-rules are refused. Because the page loads `site/styles.css`, this applies to the whole site's stylesheet; the skip link is therefore always shown rather than moved off-screen. To use new CSS, add it to the allowlist with a test.
- `privacy-policy.json` records the effective date and a hash of all the page's text (head, header, nav, main, footer), every element's tag and attributes, the facts, the `<title>`, the meta description, every link target and the full text of every stylesheet the page loads. Changing any of it, a class or a line of `site/styles.css` included, without a new effective date fails.

### Changing the policy

1. Edit `site/privacy/index.html`, or a stylesheet it loads: **any change to `site/styles.css` (even for another page) is a policy change and needs a new effective date.**
2. Set a new, later effective date in the `<time data-policy="effective-date" datetime="…">` marker and its visible text, written as `13 November 2026` (the check requires the text to match the `datetime`).
3. Run `python3 scripts/check_privacy.py --update`. It rewrites `effectiveDate` and `textSha256` in `privacy-policy.json`, and refuses unless the page's date is later than the recorded one.
4. The self-tests in `scripts/test_check_privacy.py` copy the shipped page and pin its effective date (`DATE_MARKER`, `2026-11-14`) and some sentences. Update those literals to the new page.
5. Run `bash scripts/check.sh` and commit the page, the record and the tests together.

The record in the same change is not trusted on its own: recomputing `textSha256` while keeping the date would satisfy it. So `check_privacy.py --base REV` also compares with a trusted previous git revision. It hashes `REV`'s page with the current checker, and if this page's hash differs, the effective date must be strictly later than `REV`'s recorded `effectiveDate`. If `REV` has no `privacy-policy.json` yet (the first publish), the comparison is skipped and the output says so. Any other failure to read `REV` (unknown or unfetched revision, a shallow clone without it, a record without a page, an invalid record) fails the check.

`check.sh` passes `PRIVACY_BASE` as `--base`. On CI it must be set: `pages.yml` checks out two commits and sets it to the pull request's base commit (fetched explicitly) on a pull request, and to `HEAD^1` (the first parent, the previous `main` for a merge commit) on push, schedule and manual runs. Locally it is optional and needs no network; to compare with your local `main`:

```sh
PRIVACY_BASE=main bash scripts/check.sh
```

To change the publisher, the Play developer name or the contact, edit the marker on the page and the matching field (`publisher`, `playDeveloper` or `contactEmail`) in `privacy-policy.json` by hand, then follow the steps above. `--update` rewrites only `effectiveDate` and `textSha256`.

When the app's facts change, the daily scheduled run fails until the page states the new facts (and gets a new date). Copy the app's new `docs/privacy-facts.json` over `scripts/fixtures/privacy-facts.json` and update `test_fixture_is_the_app_v1_file` in the same change, or the self-tests fail against the old fixture.

GitHub disables scheduled workflows in a public repository after 60 days without repository activity, and emails the failure of a scheduled run only to the user who last changed the `cron` line. If the Actions tab shows the Pages workflow as disabled, re-enable it there. Pull requests and pushes run the same facts check, so drift is also caught on the next change.

## Preview locally

```sh
bash scripts/preview.sh        # or: bash scripts/preview.sh 8080
```

Open <http://localhost:8000/cubentia-site/>. The script serves `site/` under `/cubentia-site/` (from a temporary `.preview.*/` directory with a symlink), so links resolve exactly as on GitHub Pages. The local server does not serve `404.html` for missing paths; open `/cubentia-site/404.html` directly.

## Deploy

`.github/workflows/pages.yml` has two jobs:

- `check` runs `scripts/check.sh` on every pull request, on every push to `main`, daily (`schedule`) and on manual dispatch. It has only `contents: read`. It first fails if the repository variable `APP_REPO` (the app repository, `owner/name`) is empty, then reads only `docs/privacy-facts.json` from `APP_REPO` at `main` with a sparse checkout into `app-facts/` (the app repository is public; no token beyond the default), and passes it as `PRIVACY_FACTS`. It checks out two commits and sets `PRIVACY_BASE` to the pull request's base commit or to `HEAD^1`, and fails if that revision cannot be read. For a push or a manual dispatch on `main` it also uploads `site/` as the Pages artifact.
- `deploy` runs only for a push or a manual dispatch on `main`, after `check` passes (`needs: check`). It alone has `pages: write` and `id-token: write`, uses the `github-pages` environment and publishes with `configure-pages` and `deploy-pages`.

A pull request or a scheduled run runs `check` only: no artifact is uploaded and nothing is deployed. The daily run catches drift between the app's facts and the policy. Merging to `main` publishes the site. Every action is pinned to a full commit SHA. No secrets are used.

## Licence

- Code (HTML and CSS markup, workflows, scripts): MIT, Copyright (c) 2026 Exxerpro Solutions, S.A. de C.V.
- Site content (text, logo, screenshots, the Cubentia name and branding): © 2026 Exxerpro Solutions, S.A. de C.V., all rights reserved.

See [LICENSE](LICENSE).
