# cubentia-site

Public website and privacy policy for Cubentia, an offline Android cube tutor published by ExxerPro.

The site is plain static HTML and one CSS file. It has no JavaScript, no cookies, no tracking and no resource from another origin. GitHub Pages publishes it from GitHub Actions.

## URL

- Site: <https://hebelmx.github.io/cubentia-site/>
- No custom domain. Checked on 2026-11-13: the Pages API reports `cname: null` with HTTPS enforced, and the account has no user site (`hebelmx/hebelmx.github.io` does not exist), so nothing sets a domain for project sites. If a custom domain is added later, GitHub redirects the `github.io` URLs to it.
- The privacy policy will live at <https://hebelmx.github.io/cubentia-site/privacy/> (Story 13.4). Play Console registers that URL, so it must never change.

## Rules

- **Never rename this repository.** A GitHub Pages project URL contains the repository name, and GitHub does not redirect Pages URLs after a rename. Renaming `cubentia-site` would break the privacy-policy URL registered in Play Console.
- **Do not call it `cubentia`.** The app repository will be renamed to `cubentia`, and repository names are case-insensitive, so the two would collide. The app repository can be renamed without affecting this site.
- **No personal data in this repository.** No tester names, emails, group addresses or opt-in links, in files, commits, issues or pull requests. ExxerPro is named as the publisher.
- **Nothing from another origin.** Every `src`, `<link href>`, CSS `url()` and `@import` is same-origin. Links (`<a href>`) to other sites are fine. Fonts use a system font stack; IBM Plex Sans is used only if already installed.
- **Paths under `/cubentia-site/`.** Pages at fixed paths use relative links. `404.html` is served at any missing path, so it uses absolute `/cubentia-site/...` paths. A root-relative path outside `/cubentia-site/` fails the check.
- Use LF line endings and Conventional Commits.

## Layout

```text
site/                  published as-is
  index.html           landing page
  404.html             custom "Page not found" page
  styles.css           the one stylesheet (design tokens, light and dark)
scripts/
  check.sh             the check CI runs; run it locally before a PR
  check_site.py        link, third-party, script/cookie, root-path and wording checks (stdlib Python)
  test_check_site.py   self-tests for check_site.py
  preview.sh           local preview under /cubentia-site/
.github/workflows/
  pages.yml            check, then deploy to GitHub Pages
```

## Check locally

Needs Python 3, Node.js (for `npx`) and Java 11 or later.

```sh
bash scripts/check.sh
```

It runs the checker's self-tests, then `scripts/check_site.py site`, then the Nu Html Checker (`vnu-jar`, version pinned in `check.sh`) on the HTML and CSS. It exits non-zero on any problem and prints each one as `file: message`.

## Preview locally

```sh
bash scripts/preview.sh        # or: bash scripts/preview.sh 8080
```

Open <http://localhost:8000/cubentia-site/>. The script serves `site/` under `/cubentia-site/` (from a temporary `.preview/` directory with a symlink), so links resolve exactly as on GitHub Pages. The local server does not serve `404.html` for missing paths; open `/cubentia-site/404.html` directly.

## Deploy

`.github/workflows/pages.yml` has two jobs:

- `check` runs `scripts/check.sh` on every pull request, on every push to `main` and on manual dispatch. It has only `contents: read`. On `main` (not on pull requests) it also uploads `site/` as the Pages artifact.
- `deploy` runs only on `main`, after `check` passes (`needs: check`). It alone has `pages: write` and `id-token: write`, uses the `github-pages` environment and publishes with `configure-pages` and `deploy-pages`.

A pull request runs `check` only: no artifact is uploaded and nothing is deployed. Merging to `main` publishes the site. Every action is pinned to a full commit SHA. No secrets are used.

## Licence

- Code (HTML and CSS markup, workflows, scripts): MIT, Copyright (c) 2026 ExxerPro.
- Site content (text, logo, screenshots, the Cubentia name and branding): © 2026 ExxerPro, all rights reserved.

See [LICENSE](LICENSE).
