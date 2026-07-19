# Delegate Tracker — Fact-Check Handoff (run locally)

The cloud session that generated this **could not reach the primary sources**
(`mainedems.org` PDFs + the three campaign slate pages are blocked by that
environment's egress proxy). Your own machine can reach them, so run the
verification there. This file gives you everything to do that with an agent
team — sonnet gatherers + fable checkers, exactly as intended.

Two paths below. Do **Path A** for a 5-minute answer; do **Path B** for the
independent, name-by-name verification.

---

## What's already been confirmed (don't redo)

Verified from the cloud session via WebSearch + a local consistency script —
treat as done:

- **Names/roles/spellings** of Troy Jackson, Shenna Bellows, Nirav Shah — correct.
- **Race framing**: July 25 2026 nominating **convention** (601 delegates)
  replacing Graham Platner; opponent Susan Collins; Maine's 16 counties;
  county meetings July 18–19. All confirmed by Maine Public / BDN / Press Herald / NPR.
- **Structure**: the 8 "reporting" counties match BDN's Day-1 (July 18) caucus
  list exactly; the **319** Day-1 delegate total and seat counts
  (Cumberland 149, Penobscot 44, Androscoggin 31) match press reporting.
- **Internal consistency**: both JSON islands are self-consistent
  (`seats == #records == sum of buckets` per county; all 319 records obey the
  attribution logic; totals = J 286 / B 4 / S 5 / Amb 9 / Unm 15 = 319).

**What is NOT yet verified — this is the whole job for the local run:**
1. Each per-candidate county **count** (e.g. Jackson 143 in Cumberland) vs the official PDF.
2. Each of the **319 delegate names** actually appears (as a `Delegate`, not `Alternate`) in that county's PDF.
3. Each **attribution** matches the actual campaign slates.
4. The **286/319** split (no news source publishes candidate-level tallies).
5. One known copy nit: the page `<title>`/header says **"Primary"** but the
   mechanism is a **convention** — decide whether to reword.

---

## Path A — fastest (re-run the repo's own scraper and diff)

```bash
cd maine-delegate-tracker
python3 -m venv .venv && source .venv/bin/activate
pip install -r scripts/requirements.txt

# Re-scrape live sources WITHOUT writing the file, capture the proposed island:
python3 scripts/update.py --dry-run | tee /tmp/dryrun.txt

# Compare the numbers it prints now against the committed baseline:
python3 - <<'PY'
import json
snap = json.load(open('expected_snapshot.json'))
print("Committed baseline totals:", snap["totals"])
for c in snap["counties"]:
    if c["reporting"]:
        print(f'{c["name"]:12} J={c["jackson"]:>3} B={c["bellows"]} S={c["shah"]} Amb={c["ambiguous"]} Unm={c["unmatched"]} seats={c["seats"]}')
PY
```

Then eyeball the scraper's per-county line (`Androscoggin: seats=31 jackson=26 …`)
against that table. **Caveat:** this re-runs the site's *own* parser, so a bug in
`update.py` would reproduce itself and still "match." Path A confirms the live
sources haven't changed and the pipeline is deterministic — it does **not**
independently prove the parser is right. For that, do Path B.

---

## Path B — independent agent team (the real verification)

Open **Claude Code in this repo on your machine** and paste the prompt below. It
spins up the sonnet-gatherer / fable-checker team you asked for, but this time
the gatherers can actually fetch the PDFs and slates. Crucially, it tells them to
**re-derive from scratch** (own parsing), not to import `update.py`'s functions —
so a scraper bug can't hide.

### Paste this to your local Claude Code lead agent:

> You are the lead of a fact-check team verifying `maine-delegate-tracker`. The
> site's committed data lives in two JSON islands in `index.html`
> (`id="data"` = per-county counts, `id="delegates"` = per-delegate detail), and a
> clean baseline is in `expected_snapshot.json`. Your job: independently verify
> the per-county counts, the 319 delegate names, and the slate attributions
> against the PRIMARY sources. Do NOT import or call functions from
> `scripts/update.py` — re-derive independently so a scraper bug can't hide.
>
> Spawn **one sonnet sub-agent per reporting county** (8 total: Androscoggin,
> Cumberland, Franklin, Hancock, Kennebec, Lincoln, Penobscot, Washington). Give
> each its PDF URL (from `expected_snapshot.json`, field `pdf`) and this task:
>   1. Download the county results PDF. Extract every row. Keep only rows whose
>      order column says `Delegate N` (drop `Alternate N`). Count them → that is
>      `seats`. List the delegate names.
>   2. Fetch that county's three campaign slates:
>      Jackson `https://jacksonformaine.com/news/<county-lowercase>`,
>      Shah `https://shahformaine.com/vote/` (one page, find the county block),
>      Bellows `https://bellowsformaine.com/delegates` (one page, find the county block).
>   3. For each winning delegate, match by name (case/accent/period-insensitive;
>      compare first word + last word, ignoring middle names; allow a 1-char typo
>      on the first name only if the last name matches exactly). On exactly one
>      slate → that candidate; on ≥2 → Ambiguous; on none → Unmatched.
>   4. Tally J/B/S/Amb/Unm and compare to that county's row in
>      `expected_snapshot.json`. Report every discrepancy: wrong seat count, a
>      name in the PDF missing from the site's list (or vice-versa), or a delegate
>      bucketed differently than your independent match.
>
> Then spawn **fable sub-agents as checkers**: for any county a sonnet agent flags
> (or a random 2 counties if all clean), have a fable agent re-open the same PDF +
> slates and independently re-check the disputed names, to guard against a sonnet
> parsing mistake. Fable also advises you on whether the aggregate story is sound.
>
> Finally, produce a verdict table: per county, does the site match the primary
> sources (counts ✓/✗, names ✓/✗, attribution ✓/✗), listing every mismatch.
> Only claim a number "verified" if it was re-derived from the PDF/slate, not from
> the site's own code.

### Primary sources (for reference / manual spot-check)

County results index: https://mainedems.org/senate-race/delegate-nomination-results/

County results PDFs (only `Delegate` rows count; `Alternate` rows are excluded):

- **Androscoggin**: https://mainedems.org/wp-content/uploads/2026/07/EXTERNAL_-Androscoggin-Delegate-Selection-ElectionBuddyReport.483589.Results.260718220132.xlsx-ElectionBuddyReport.483589.Resu_.pdf
- **Cumberland**: https://mainedems.org/wp-content/uploads/2026/07/EXTERNAL_-Cumberland-Results.260718234718.xlsx-Cumberland-Results.260718234718.pdf
- **Franklin**: https://mainedems.org/wp-content/uploads/2026/07/EXTERNAL-Franklin-County-Results-ElectionBuddyReport.483583.Results.260718163107.xlsx-Franklin-County-Results.pdf
- **Hancock**: https://mainedems.org/wp-content/uploads/2026/07/EXTERNAL_-Hancock-Results-ElectionBuddyReport.483602.Results.260718172622-1.xlsx-Hancock-Results-ElectionBuddy-1.pdf
- **Kennebec**: https://mainedems.org/wp-content/uploads/2026/07/EXTERNAL_-Kennebec-Delegate-Selection-ElectionBuddyReport.483603.Results.260718182013.xlsx-ElectionBuddyReport.483603.Resu_.pdf
- **Lincoln**: https://mainedems.org/wp-content/uploads/2026/07/EXTERNAL_-Lincoln-Delegate-Selection-ElectionBuddyReport.483614.Results.260718203043.xlsx-ElectionBuddyReport.483614.Resu_.pdf
- **Penobscot**: https://mainedems.org/wp-content/uploads/2026/07/EXTERNAL_-Penobscot-County-Results-ElectionBuddyReport.483651.Results.260718195456-1.xlsx-Penobscot-County-Results-Electi-1.pdf
- **Washington**: https://mainedems.org/wp-content/uploads/2026/07/EXTERNAL_-Washington-Results-ElectionBuddyReport.483660.Results.260718180313.xlsx-ElectionBuddyReport.483660.Resu_.pdf

Campaign slates:
- Jackson: `https://jacksonformaine.com/news/<county-slug>` (e.g. `/news/cumberland`), names as `Last, First Middle`
- Shah: https://shahformaine.com/vote/ (single page, one block per county)
- Bellows: https://bellowsformaine.com/delegates (single page; `Last, First, Town` except Kennebec which is `First Last, Town`)

---

## Expected baseline (what the site currently claims)

Source: committed `index.html`, `updated = 2026-07-19T01:40:20Z`.
Full per-delegate list (name → bucket → matched slates) is in `expected_snapshot.json`.

| County | Seats | Jackson | Bellows | Shah | Amb. | Unm. |
|---|--:|--:|--:|--:|--:|--:|
| Androscoggin | 31 | 26 | 1 | 0 | 2 | 2 |
| Cumberland | 149 | 143 | 0 | 0 | 1 | 5 |
| Franklin | 9 | 6 | 0 | 0 | 0 | 3 |
| Hancock | 23 | 22 | 0 | 0 | 0 | 1 |
| Kennebec | 40 | 37 | 0 | 0 | 3 | 0 |
| Lincoln | 15 | 7 | 1 | 3 | 0 | 4 |
| Penobscot | 44 | 44 | 0 | 0 | 0 | 0 |
| Washington | 8 | 1 | 2 | 2 | 3 | 0 |
| **TOTAL** | **319** | **286** | **4** | **5** | **9** | **15** |

Not reporting (should have zero delegate records): Aroostook, Knox, Oxford,
Piscataquis, Sagadahoc, Somerset, Waldo, York.

> Note: county meetings ran July 18–19, so by the time you run this the results
> index may list **more than 8** reporting counties. That's expected — new
> counties reporting is new data, not an error.
