#!/usr/bin/env python3
"""Scrape Maine Democratic Senate-primary delegate-nomination results and campaign
delegate slates, attribute each winning delegate to a candidate, and rewrite the
JSON data island embedded in index.html in place.

Sources (pulled fresh every run -- nothing about "who is reporting" is hardcoded):
  - Results index:  https://mainedems.org/senate-race/delegate-nomination-results/
    Each county section on this page either shows "Results Coming Soon" or links a
    per-county PDF (an ElectionBuddy report) once that county has reported. The set
    of reporting counties is derived from which sections currently have a PDF link.
  - Per-county PDF: "Delegate Candidate (Town) | Votes | % | Order | Notes". Only
    rows whose Order column reads "Delegate N" are counted; "Alternate N" rows are
    dropped entirely.
  - Candidate delegate slates (re-pulled every run, matched per reporting county):
      Jackson:  https://jacksonformaine.com/news/<county-slug>   ("Last, First Middle")
      Shah:     https://shahformaine.com/vote/                   (one page, all counties)
      Bellows:  https://bellowsformaine.com/delegates             (one page, all counties)

Attribution: normalize each winning delegate's name and match it against the three
candidates' slates for that county. On exactly one slate -> credited to that
candidate. On two or more -> "ambiguous" (credited to no one). On none -> "unmatched".

Usage:
    python3 scripts/update.py [--dry-run]

Run from anywhere; index.html is located relative to this file, not the cwd.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    print("ERROR: pdfplumber is required. Install with: pip install -r scripts/requirements.txt", file=sys.stderr)
    sys.exit(1)

import io

# --------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML_PATH = REPO_ROOT / "index.html"

RESULTS_INDEX_URL = "https://mainedems.org/senate-race/delegate-nomination-results/"
SHAH_VOTE_URL = "https://shahformaine.com/vote/"
BELLOWS_DELEGATES_URL = "https://bellowsformaine.com/delegates"
JACKSON_NEWS_URL_TMPL = "https://jacksonformaine.com/news/{slug}"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 MaineDelegateTracker/1.0"
)
FETCH_TIMEOUT = 30

# Fixed 16-county order the data island must preserve.
COUNTIES = [
    "Androscoggin", "Aroostook", "Cumberland", "Franklin", "Hancock", "Kennebec",
    "Knox", "Lincoln", "Oxford", "Penobscot", "Piscataquis", "Sagadahoc",
    "Somerset", "Waldo", "Washington", "York",
]

CANDIDATES = {"jackson": "Troy Jackson", "bellows": "Shenna Bellows", "shah": "Nirav Shah"}

# Manual fallback for counties whose results PDF has been published but is not (yet)
# linked from the results index page in a form parse_results_index() recognizes.
# Applied only when the index page yields no PDF for that county, so a later index
# fix (or a different, correct link) always takes precedence. Aroostook's PDF was
# posted directly to wp-content without a matching index link.
MANUAL_PDF_URLS = {
    "Aroostook": (
        "https://mainedems.org/wp-content/uploads/2026/07/EXTERNAL_-Aroostook-Delegates-"
        "Selection-Results-ElectionBuddyReport.483599.Results.260719213254.xlsx-"
        "ElectionBuddyReport.483599.Resu_.pdf"
    ),
}


# --------------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------------

def fetch_bytes(url: str, timeout: int = FETCH_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_text(url: str, timeout: int = FETCH_TIMEOUT) -> str:
    return fetch_bytes(url, timeout=timeout).decode("utf-8", errors="ignore")


# --------------------------------------------------------------------------------
# Name normalization + fuzzy matching
# --------------------------------------------------------------------------------

def norm(s: str) -> str:
    """lowercase, strip accents/periods, collapse whitespace, normalize apostrophes."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace(".", "")
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def endpoints(full_name: str) -> tuple[str, str] | None:
    """(first_word, last_word) of a normalized name, ignoring middle names/initials."""
    toks = full_name.split()
    if not toks:
        return None
    return toks[0], toks[-1]


def match_pdf_to_slate(pdf_first: str, pdf_last: str, slate_pairs: list[tuple[str, str]]) -> bool:
    """slate_pairs: list of normalized (last_name, first_and_middle_names) tuples.

    Exact match: first word of the slate's first/middle field == pdf_first AND the
    slate's last-name field's last word == pdf_last (both sides ignore any middle
    names/initials entirely).

    Fuzzy fallback: if the last name matches exactly but the first name is off by a
    single-character edit (observed in the wild: a slate typo like "Abigal" for
    "Abigail"), still count it as a match. Last name must match exactly for the
    fuzzy path to avoid false positives.
    """
    fuzzy_hit = False
    for last, first_full in slate_pairs:
        fwords = first_full.split()
        lwords = last.split()
        if not fwords or not lwords:
            continue
        slate_first, slate_last = fwords[0], lwords[-1]
        if slate_first == pdf_first and slate_last == pdf_last:
            return True
        if slate_last == pdf_last and levenshtein(slate_first, pdf_first) <= 1:
            fuzzy_hit = True
    return fuzzy_hit


# --------------------------------------------------------------------------------
# Results index -> per-county reporting status + PDF URL
# --------------------------------------------------------------------------------

HEADING_RE = re.compile(
    r'<h2 class="elementor-heading-title elementor-size-default">([^<]+?)\s*County\s*</h2>'
)
PDF_HREF_RE = re.compile(r'href="(https://mainedems\.org/[^"]+?\.pdf)"')


def parse_results_index(html: str) -> dict[str, str | None]:
    """Return {county_name: pdf_url_or_None} by scanning each county's section for a
    results PDF link. Does not assume any fixed list of "currently reporting" counties.
    """
    headings = list(HEADING_RE.finditer(html))
    by_name: dict[str, str | None] = {}
    for i, m in enumerate(headings):
        name = m.group(1).strip()
        start = m.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(html)
        section = html[start:end]
        pdf_m = PDF_HREF_RE.search(section)
        by_name[name] = pdf_m.group(1) if pdf_m else None
    return by_name


# --------------------------------------------------------------------------------
# Per-county PDF parsing
# --------------------------------------------------------------------------------

PDF_ROW_RE = re.compile(
    r"^(?P<name>.+?)\s+\((?P<town>[^)]+)\)\s+(?P<votes>[\d,]+)\s+(?P<pct>[\d.]+)%\s+"
    r"(?P<kind>Delegate|Alternate)\s+(?P<num>\d+)(?P<notes>.*)$"
)


def parse_pdf_bytes(data: bytes) -> list[dict]:
    rows = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    for line in text.splitlines():
        line = line.strip()
        m = PDF_ROW_RE.match(line)
        if not m:
            continue
        rows.append({
            "name": m.group("name").strip(),
            "town": m.group("town").strip(),
            "kind": m.group("kind"),
            "num": int(m.group("num")),
        })
    return rows


# --------------------------------------------------------------------------------
# Candidate slate parsing
# --------------------------------------------------------------------------------

JACKSON_LI_RE = re.compile(r"<li><p>([^<]+)</p></li>")


def parse_jackson_slate(html: str, county: str) -> list[tuple[str, str]]:
    """jacksonformaine.com/news/<county> lists names as <li><p>Last, First Middle</p></li>
    beneath a "... DELEGATE SLATE:" heading. The page also embeds a JSON-escaped copy of
    every county's slate (hydration data) where the tags read `\\u003cli\\u003e...` -- those
    must NOT be parsed as literal HTML.

    We scan each "delegate slate" occurrence in order and pull the real, unescaped
    <li><p> items from its window. We deliberately do NOT try to first locate a literal
    <ul>/<ol> opening tag (some county pages -- e.g. Aroostook, Jackson's home county --
    have page layouts where that boundary hunt fails), and instead bound the list at the
    first list close following the first real item so we never spill into unrelated
    <li><p> content later on the page. The escaped hydration copies contain no literal
    <li><p>, so they simply yield nothing and are skipped.
    """
    for m in re.finditer(r"delegate slate", html, flags=re.IGNORECASE):
        window = html[m.start(): m.start() + 8000]
        first_li = JACKSON_LI_RE.search(window)
        if not first_li:
            continue
        end = len(window)
        for close in ("</ul>", "</ol>"):
            pos = window.find(close, first_li.start())
            if pos != -1:
                end = min(end, pos)
        chunk = window[:end]
        slate = []
        for it in JACKSON_LI_RE.findall(chunk):
            if "," not in it:
                continue
            last, first_full = it.split(",", 1)
            slate.append((norm(last), norm(first_full)))
        if slate:
            return slate
    return []


def parse_shah_slate(html: str, county: str) -> list[tuple[str, str]]:
    """shahformaine.com/vote/ has one <details class="county"> block per county with
    <span class="county__name">X County</span> followed by an <ol><li>Last, First</li></ol>.
    """
    m = re.search(r'<span class="county__name">' + re.escape(county) + r"\s+County</span>", html)
    if not m:
        return []
    ol_start = html.find("<ol>", m.end())
    if ol_start == -1:
        return []
    ol_end = html.find("</ol>", ol_start)
    chunk = html[ol_start:ol_end]
    items = re.findall(r"<li>([^<]+)</li>", chunk)
    slate = []
    for it in items:
        if "," not in it:
            continue
        last, first_full = it.split(",", 1)
        slate.append((norm(last), norm(first_full)))
    return slate


def parse_bellows_slate(html: str, county: str) -> list[tuple[str, str]]:
    """bellowsformaine.com/delegates is a Squarespace accordion, one item per county,
    titled just the county name (no "County" suffix). Each entry is either:
      - "Last, First Middle, Town"           (all counties except Kennebec)
      - "First Middle Last, Town"            (Kennebec only, per the campaign's page)
    Detect the format per-item by comma count rather than hardcoding on county name,
    so a future formatting fix on their end doesn't silently break this.
    """
    m = re.search(r'accordion-item__title">' + re.escape(county) + r"</span>", html)
    if not m:
        return []
    ul_start = html.find('<ul data-rte-list="default">', m.end())
    if ul_start == -1:
        return []
    ul_end = html.find("</ul>", ul_start)
    chunk = html[ul_start:ul_end]
    items = re.findall(r"<li>(.*?)</li>", chunk, flags=re.S)
    slate = []
    for it in items:
        text = strip_tags(it).replace("&amp;", "&")
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) == 2:
            full_name, _town = parts
            words = full_name.split()
            if len(words) < 2:
                continue
            last, first_full = words[-1], " ".join(words[:-1])
        elif len(parts) >= 3:
            last, first_full = parts[0], parts[1]
        else:
            continue
        slate.append((norm(last), norm(first_full)))
    return slate


# --------------------------------------------------------------------------------
# Data island formatting + in-place replacement
# --------------------------------------------------------------------------------

DATA_ISLAND_RE = re.compile(
    r'(<script type="application/json" id="data">\n)(.*?)(\n</script>)', re.DOTALL
)


def format_county_line(c: dict) -> str:
    if not c.get("reporting"):
        return '    { "name": "%s", "reporting": false }' % c["name"]
    return (
        '    { "name": "%s", "reporting": true, "jackson": %d, "bellows": %d, '
        '"shah": %d, "ambiguous": %d, "unmatched": %d, "seats": %d }'
    ) % (c["name"], c["jackson"], c["bellows"], c["shah"], c["ambiguous"], c["unmatched"], c["seats"])


def build_data_island(updated_iso: str, counties: list[dict]) -> str:
    lines = ["{"]
    lines.append('  "updated": "%s",' % updated_iso)
    lines.append(
        '  "candidates": { "jackson": "%s", "bellows": "%s", "shah": "%s" },'
        % (CANDIDATES["jackson"], CANDIDATES["bellows"], CANDIDATES["shah"])
    )
    lines.append('  "counties": [')
    county_lines = [format_county_line(c) for c in counties]
    lines.append(",\n".join(county_lines))
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines)


def replace_data_island(html: str, new_island: str) -> str:
    matches = list(DATA_ISLAND_RE.finditer(html))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one data island in index.html, found {len(matches)}. "
            "Refusing to touch the file."
        )
    m = matches[0]
    return html[: m.start()] + m.group(1) + new_island + m.group(3) + html[m.end():]


# A second, independent JSON island holding per-delegate detail for the Sources view.
# Kept entirely separate from the id="data" island above: the counts view and the cron
# depend on that contract being untouched, so this only ADDS a parallel island.
DELEGATES_ISLAND_RE = re.compile(
    r'(<script type="application/json" id="delegates">\n)(.*?)(\n</script>)', re.DOTALL
)


def build_delegates_island(updated_iso: str, delegates: list[dict]) -> str:
    """Emit the per-delegate island. Each record carries the delegate's name, county,
    bucket (a candidate key, or "ambiguous"/"unmatched"), the county results PDF URL,
    and the slate URL(s) whose match produced the attribution (empty for unmatched).

    One compact line per delegate (mirrors the id="data" island's one-line-per-county
    style) so the every-15-min cron rewrites a tidy diff rather than thousands of lines.
    """
    lines = ["{", '  "updated": "%s",' % updated_iso]
    if delegates:
        lines.append('  "delegates": [')
        lines.append(",\n".join(
            "    " + json.dumps(d, ensure_ascii=False) for d in delegates
        ))
        lines.append("  ]")
    else:
        lines.append('  "delegates": []')
    lines.append("}")
    return "\n".join(lines)


def replace_delegates_island(html: str, new_island: str) -> str:
    matches = list(DELEGATES_ISLAND_RE.finditer(html))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one delegates island in index.html, found {len(matches)}. "
            "Refusing to touch the file."
        )
    m = matches[0]
    return html[: m.start()] + m.group(1) + new_island + m.group(3) + html[m.end():]


# --------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------

def process_county(name: str, pdf_url: str, shah_html: str | None, bellows_html: str | None) -> dict | None:
    """Returns a full reporting-county record, or None if the county should be
    treated as non-reporting this run (fetch/parse failure somewhere in the chain).
    """
    try:
        pdf_bytes = fetch_bytes(pdf_url)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"WARNING: {name}: failed to download results PDF ({e}); leaving non-reporting")
        return None

    try:
        rows = parse_pdf_bytes(pdf_bytes)
    except Exception as e:
        print(f"WARNING: {name}: failed to parse results PDF ({e}); leaving non-reporting")
        return None

    delegates = [r for r in rows if r["kind"] == "Delegate"]
    if not delegates:
        print(f"WARNING: {name}: PDF parsed but found zero Delegate rows; leaving non-reporting")
        return None

    if shah_html is None or bellows_html is None:
        print(f"WARNING: {name}: a candidate-slate source is unavailable this run; leaving non-reporting")
        return None

    slug = name.lower()
    try:
        jackson_html = fetch_text(JACKSON_NEWS_URL_TMPL.format(slug=slug))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"WARNING: {name}: failed to fetch Jackson slate page ({e}); leaving non-reporting")
        return None

    jslate = parse_jackson_slate(jackson_html, name)
    sslate = parse_shah_slate(shah_html, name)
    bslate = parse_bellows_slate(bellows_html, name)

    if not jslate:
        print(f"WARNING: {name}: could not parse a Jackson slate; leaving non-reporting")
        return None
    if not sslate:
        print(f"WARNING: {name}: could not parse a Shah slate; leaving non-reporting")
        return None
    if not bslate:
        print(f"WARNING: {name}: could not parse a Bellows slate; leaving non-reporting")
        return None

    # Per-candidate slate URL used to source (verify) each attribution. Jackson's is
    # county-specific; Shah's and Bellows's are single pages covering all counties.
    slate_urls = {
        "jackson": JACKSON_NEWS_URL_TMPL.format(slug=slug),
        "bellows": BELLOWS_DELEGATES_URL,
        "shah": SHAH_VOTE_URL,
    }

    jackson = bellows = shah = ambiguous = unmatched = 0
    # Per-delegate detail captured for the Sources view. Counting semantics below are
    # identical to the original counts-only loop; we only additionally record, for each
    # winning delegate, its bucket and the slate URL(s) it matched on.
    details = []
    for r in delegates:
        ends = endpoints(norm(r["name"]))
        if ends is None:
            on_j = on_b = on_s = False
        else:
            pdf_first, pdf_last = ends
            on_j = match_pdf_to_slate(pdf_first, pdf_last, jslate)
            on_b = match_pdf_to_slate(pdf_first, pdf_last, bslate)
            on_s = match_pdf_to_slate(pdf_first, pdf_last, sslate)
        matched = [c for c, on in (("jackson", on_j), ("bellows", on_b), ("shah", on_s)) if on]
        hits = len(matched)
        if hits == 0:
            unmatched += 1
            bucket = "unmatched"
        elif hits > 1:
            ambiguous += 1
            bucket = "ambiguous"
        elif on_j:
            jackson += 1
            bucket = "jackson"
        elif on_b:
            bellows += 1
            bucket = "bellows"
        else:
            shah += 1
            bucket = "shah"
        details.append({
            "name": r["name"],
            "county": name,
            "bucket": bucket,
            "pdf": pdf_url,
            "slates": [{"candidate": c, "url": slate_urls[c]} for c in matched],
        })

    seats = len(delegates)
    print(
        f"{name}: seats={seats} jackson={jackson} bellows={bellows} shah={shah} "
        f"ambiguous={ambiguous} unmatched={unmatched}"
    )
    return {
        "name": name,
        "reporting": True,
        "jackson": jackson,
        "bellows": bellows,
        "shah": shah,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
        "seats": seats,
        "delegates": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute and print results but do not write index.html",
    )
    args = parser.parse_args()

    print(f"Fetching results index: {RESULTS_INDEX_URL}")
    try:
        index_html = fetch_text(RESULTS_INDEX_URL)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"ERROR: could not reach the results index ({e}); aborting.", file=sys.stderr)
        return 1

    pdf_by_county = parse_results_index(index_html)
    missing = [c for c in COUNTIES if c not in pdf_by_county]
    if missing:
        print(f"WARNING: results index did not list these expected counties at all: {missing}")

    # Apply manual PDF fallbacks only where the index page provided no link, so an
    # index-page fix always wins over the hardcoded URL.
    for county, url in MANUAL_PDF_URLS.items():
        if not pdf_by_county.get(county):
            print(f"Using manual PDF override for {county} (not linked on index page): {url}")
            pdf_by_county[county] = url

    reporting_names = [c for c in COUNTIES if pdf_by_county.get(c)]
    print(f"Counties currently reporting a PDF link: {reporting_names or 'none'}")

    shah_html: str | None
    bellows_html: str | None
    try:
        shah_html = fetch_text(SHAH_VOTE_URL)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"WARNING: failed to fetch Shah slate page ({e}); all reporting counties will be skipped")
        shah_html = None
    try:
        bellows_html = fetch_text(BELLOWS_DELEGATES_URL)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"WARNING: failed to fetch Bellows slate page ({e}); all reporting counties will be skipped")
        bellows_html = None

    counties_out = []
    delegates_out = []
    totals = {"jackson": 0, "bellows": 0, "shah": 0, "ambiguous": 0, "unmatched": 0, "seats": 0}
    reporting_count = 0

    for name in COUNTIES:
        pdf_url = pdf_by_county.get(name)
        if not pdf_url:
            counties_out.append({"name": name, "reporting": False})
            continue
        record = process_county(name, pdf_url, shah_html, bellows_html)
        if record is None:
            counties_out.append({"name": name, "reporting": False})
            continue
        delegates_out.extend(record.pop("delegates"))
        counties_out.append(record)
        reporting_count += 1
        for k in totals:
            totals[k] += record[k]

    print(
        f"TOTAL ({reporting_count}/{len(COUNTIES)} counties reporting): "
        f"jackson={totals['jackson']} bellows={totals['bellows']} shah={totals['shah']} "
        f"ambiguous={totals['ambiguous']} unmatched={totals['unmatched']} seats={totals['seats']}"
    )

    updated_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_island = build_data_island(updated_iso, counties_out)
    new_delegates_island = build_delegates_island(updated_iso, delegates_out)

    if args.dry_run:
        print("--dry-run: not writing index.html. New data island would be:")
        print(new_island)
        print(f"--dry-run: delegates island would carry {len(delegates_out)} delegate records.")
        return 0

    html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    try:
        new_html = replace_data_island(html, new_island)
        new_html = replace_delegates_island(new_html, new_delegates_island)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    INDEX_HTML_PATH.write_text(new_html, encoding="utf-8")
    print(f"Updated {INDEX_HTML_PATH} (updated={updated_iso}, {len(delegates_out)} delegate records)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
