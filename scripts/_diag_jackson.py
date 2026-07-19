#!/usr/bin/env python3
"""TEMPORARY diagnostic: dump the structure of a Jackson campaign county news page
so we can see why parse_jackson_slate() finds no slate for Aroostook. Delete after use.
"""
import re
import sys
import urllib.request

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 MaineDelegateTracker/1.0"
)

slug = sys.argv[1] if len(sys.argv) > 1 else "aroostook"
url = f"https://jacksonformaine.com/news/{slug}"
print(f"FETCH {url}")
req = urllib.request.Request(url, headers={"User-Agent": UA})
with urllib.request.urlopen(req, timeout=30) as resp:
    status = resp.status
    html = resp.read().decode("utf-8", errors="ignore")
print(f"HTTP {status}, {len(html)} bytes")
print("title:", re.search(r"<title>(.*?)</title>", html, re.S | re.I))

occ = list(re.finditer(r"delegate slate", html, flags=re.IGNORECASE))
print(f"'delegate slate' occurrences: {len(occ)}")
for i, m in enumerate(occ):
    idx = m.start()
    window = html[idx: idx + 1200]
    print(f"\n===== occurrence {i} @ {idx} =====")
    print(repr(window))

# Show the variety of <li> shapes present so we can generalize the regex.
li_samples = re.findall(r"<li[^>]*>.{0,120}?</li>", html, flags=re.S)
print(f"\n<li> samples: {len(li_samples)} (showing up to 12)")
for s in li_samples[:12]:
    print(repr(s))
