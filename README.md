# Maine Democratic U.S. Senate Primary — Delegate Tracker

A public, auto-updating tracker of **pledged convention delegates** won by each candidate
(Troy Jackson, Shenna Bellows, Nirav Shah) across Maine's 16 counties.

**Live:** https://maine-delegate-results.tuckergordon.dev

## How it works

- `index.html` is a single self-contained page. All data lives in one JSON island
  (`<script type="application/json" id="data">`); everything else (the county map, styles,
  render logic) is static.
- `scripts/update.py` scrapes the live sources, attributes each winning delegate to a
  candidate, and rewrites that JSON island in place.
- A GitHub Actions cron (`.github/workflows/update.yml`) runs the scraper hourly and commits
  any change. GitHub Pages redeploys the page automatically on each commit to `main`.

## Data sources

- **Results** (which counties reported, who won each seat): the ElectionBuddy PDFs linked from
  https://mainedems.org/senate-race/delegate-nomination-results/ — only `Delegate` rows are
  counted; `Alternate` rows are excluded.
- **Candidate slates** (used to attribute each delegate): the three campaigns' published slates
  (jacksonformaine.com, shahformaine.com, bellowsformaine.com).

## Attribution & its limits

Attribution is **inferred, not official.** Each winning delegate's name is matched against the
three published slates for their county:

- on exactly one slate → credited to that candidate
- on two or more slates → **Ambiguous** (credited to no one)
- on no slate → **Unmatched**

**Ambiguous** and **Unmatched** are always shown as their own columns and never folded into a
candidate's total.

## Run locally

```bash
pip install -r scripts/requirements.txt
python scripts/update.py   # rewrites index.html in place
open index.html
```
