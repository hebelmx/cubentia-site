# cubentia-site

Public website and privacy policy for Cubentia, an offline Android cube tutor published by ExxerPro.

The site is plain static HTML and one CSS file. It has no JavaScript, no cookies, no tracking and no resource from another origin. GitHub Pages publishes it from GitHub Actions.

## URL

- Site: <https://hebelmx.github.io/cubentia-site/>
- No custom domain. Checked on 2026-11-13: the Pages API reports `cname: null` with HTTPS enforced, and the account has no user site (`hebelmx/hebelmx.github.io` does not exist), so nothing sets a domain for project sites. If a custom domain is added later, GitHub redirects the `github.io` URLs to it.
- Privacy policy: <https://hebelmx.github.io/cubentia-site/privacy/>. Play Console registers that URL, so it must never change.

## Rules

- **Never rename this repository.** A GitHub Pages project URL contains the repository name, and GitHub does not redirect Pages URLs after a rename. Renaming `cubentia-site` would break the privacy-policy URL registered in Play Console.
- **Do not call it `cubentia`.** The app repository will be renamed to `cubentia`, and repository names are case-insensitive, so the two would collide. The app repository can be renamed without affecting this site.
- **No tester or pilot data in this repository.** No tester names, emails, group addresses or opt-in links, in files, commit messages, issues or pull requests. ExxerPro is named as the publisher, and the published contact address is the only email on the site. The maintainer's own git author identity, which is the same as in the app repository, is not covered by this rule.
- **Nothing from another origin.** Every `src`, `<link href>`, CSS `url()` and `@import` is same-origin. Links (`<a href>`) to other sites are fine. Fonts use a system font stack; IBM Plex Sans is used only if already installed.
- **Deny by default.** `scripts/check_site.py` accepts only allowlisted elements, attributes, `<link rel>` values (stylesheet, icon, canonical, manifest), CSS at-rules, CSS functions and file types. CSS may not generate text (`content` is only `none`, `normal` or `""`), and `<base>`, `<meta http-equiv>`, scripts and embeds are rejected. If a new page needs something that is refused, narrow the rule in the checker and add a test.
- **Paths under `/cubentia-site/`.** Pages at fixed paths use relative links. `404.html` is served at any missing path, so it uses absolute `/cubentia-site/...` paths. A root-relative path outside `/cubentia-site/` fails the check.
- Use LF line endings and Conventional Commits.

## Layout

```text
site/                  published as-is
  index.html           landing page
  privacy/index.html   privacy policy (statements checked against the app's privacy facts)
  404.html             custom "Page not found" page
  styles.css           the one stylesheet (design tokens, light and dark)
scripts/
  check.sh             the check CI runs; run it locally before a PR
  check_site.py        link, third-party, script/cookie, root-path and wording checks (stdlib Python)
  test_check_site.py   self-tests for check_site.py
  check_privacy.py     privacy-policy check against the app's facts and the policy record
  test_check_privacy.py  self-tests for check_privacy.py
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

It runs the checkers' self-tests, then `scripts/check_site.py site`, then `scripts/check_privacy.py` (below), then the Nu Html Checker (`vnu-jar`, version pinned in `check.sh`) on the HTML and CSS. It exits non-zero on any problem. `check_site.py` and `check_privacy.py` print each problem as `file: message`; the Nu Html Checker uses its own format.

`PRIVACY_FACTS` names the app's `docs/privacy-facts.json`. On CI (`GITHUB_ACTIONS=true`) it must be set and the file must exist. Locally, when it is unset, `check.sh` uses `scripts/fixtures/privacy-facts.json` (a copy of the app's v1.0 file) and says so, so no network is needed. To check against a local app checkout:

```sh
PRIVACY_FACTS=../RubikXplorer/docs/privacy-facts.json bash scripts/check.sh
```

## Privacy facts check

The app's build exports what it can prove about privacy to `docs/privacy-facts.json` in the app repository. `scripts/check_privacy.py` holds the policy page to that file:

- Every key except `schemaVersion` is a fact. The page states each one on an element with `data-fact="<key>"`, `data-value="<value>"` and plain-language text. `<value>` is the raw string for a JSON string and compact JSON otherwise (`false`, `[]`). `schemaVersion` must be `1`.
- The check fails when a fact has no statement with an equal value, when the page states a fact the file lacks, when statements conflict or are empty, or when the file is missing, invalid or has another `schemaVersion`.
- `data-policy` markers name the publisher, the Play developer, the contact (`<a href="mailto:…">`) and the effective date (`<time datetime="YYYY-MM-DD">`). They must match `privacy-policy.json`, and the contact must be the only email on the page.
- `privacy-policy.json` records the effective date and a hash of the text of `<main>`, the facts, the `<title>`, the meta description and every link target. Changing the text without a new effective date fails.

### Changing the policy

1. Edit `site/privacy/index.html`.
2. Set a new, later effective date in the `<time data-policy="effective-date" datetime="…">` marker and its visible text, written as `13 November 2026` (the check requires the text to match the `datetime`).
3. Run `python3 scripts/check_privacy.py --update`. It rewrites `effectiveDate` and `textSha256` in `privacy-policy.json`, and refuses unless the page's date is later than the recorded one.
4. Run `bash scripts/check.sh` and commit the page and the record together.

When the app's facts change, the daily scheduled run fails until the page states the new facts (and gets a new date).

GitHub disables scheduled workflows in a public repository after 60 days without repository activity, and emails the failure of a scheduled run only to the user who last changed the `cron` line. If the Actions tab shows the Pages workflow as disabled, re-enable it there. Pull requests and pushes run the same facts check, so drift is also caught on the next change.

## Preview locally

```sh
bash scripts/preview.sh        # or: bash scripts/preview.sh 8080
```

Open <http://localhost:8000/cubentia-site/>. The script serves `site/` under `/cubentia-site/` (from a temporary `.preview.*/` directory with a symlink), so links resolve exactly as on GitHub Pages. The local server does not serve `404.html` for missing paths; open `/cubentia-site/404.html` directly.

## Deploy

`.github/workflows/pages.yml` has two jobs:

- `check` runs `scripts/check.sh` on every pull request, on every push to `main`, daily (`schedule`) and on manual dispatch. It has only `contents: read`. It first fails if the repository variable `APP_REPO` (the app repository, `owner/name`) is empty, then reads only `docs/privacy-facts.json` from `APP_REPO` at `main` with a sparse checkout into `app-facts/` (the app repository is public; no token beyond the default), and passes it as `PRIVACY_FACTS`. For a push or a manual dispatch on `main` it also uploads `site/` as the Pages artifact.
- `deploy` runs only for a push or a manual dispatch on `main`, after `check` passes (`needs: check`). It alone has `pages: write` and `id-token: write`, uses the `github-pages` environment and publishes with `configure-pages` and `deploy-pages`.

A pull request or a scheduled run runs `check` only: no artifact is uploaded and nothing is deployed. The daily run catches drift between the app's facts and the policy. Merging to `main` publishes the site. Every action is pinned to a full commit SHA. No secrets are used.

## Licence

- Code (HTML and CSS markup, workflows, scripts): MIT, Copyright (c) 2026 ExxerPro.
- Site content (text, logo, screenshots, the Cubentia name and branding): © 2026 ExxerPro, all rights reserved.

See [LICENSE](LICENSE).
