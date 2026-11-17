"""
When was a web page published? Read from the page itself, not the model.

The rotating web search (10_web_price_search.py) asks the model for each
price's source date, and the model sometimes gets it wrong. Found 2 Nov 2026:
a Daily Trust article of 8 Jan 2023 (wheat N65-70K per 100kg) had been
promoted as "30 Oct 2026" and opened price alert #6, and a May 2025 Daily
Trust beef article was dated October 2026. Both pages state their real date
in machine-readable form, so 14_promote_web_prices.py (what enters the price
history) and 16_cross_check.py (what drives the alerts) check it here.

Which date counts: the page's own article:published_time / modified_time
meta tags; else the JSON-LD entry for THIS article (matched by URL or
headline - Daily Trust pages also carry a JSON-LD NewsArticle for an
unrelated "latest news" story dated today, so "any date on the page" would
have passed both misdated articles); else the first <time datetime>.

A price may not be dated later than the page was last updated (plus
TOLERANCE_DAYS). A page we can't open, or with no readable date, is not
confirmed - the price waits for human review rather than being trusted.
"""

import json, re
from datetime import timedelta

import requests

HEADERS = {"User-Agent": "Agrolinking-Intelligence/1.0 (daily price reader; contact: john@agrolinking.com)"}
TIMEOUT = 15
TOLERANCE_DAYS = 3     # a price may be dated a few days after the page's last update (time zones, weekly lists)

PUBLISHED_META = ("article:published_time", "og:published_time", "datepublished",
                  "pubdate", "publishdate", "dc.date.issued", "dc.date")
MODIFIED_META = ("article:modified_time", "og:updated_time", "datemodified")

_cache = {}


def _to_date(text):
    if not text:
        return None
    try:
        from dateutil import parser
        return parser.parse(str(text), fuzzy=True).date()
    except Exception:
        return None


def _meta(html, names):
    """First content= of a <meta> whose property/name/itemprop is one of names, in names order."""
    found = {}
    for tag in re.findall(r"<meta\b[^>]*>", html, re.I):
        key = re.search(r"\b(?:property|name|itemprop)\s*=\s*[\"']([^\"']+)[\"']", tag, re.I)
        val = re.search(r"\bcontent\s*=\s*[\"']([^\"']+)[\"']", tag, re.I)
        if key and val:
            found.setdefault(key.group(1).strip().lower(), val.group(1))
    for n in names:
        d = _to_date(found.get(n))
        if d:
            return d
    return None


def _norm_url(u):
    u = re.sub(r"^https?://(www\.)?", "", str(u or "").strip().lower())
    return u.split("#")[0].split("?")[0].rstrip("/")


def _jsonld_entries(html):
    out = []
    for blk in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        try:
            data = json.loads(blk.strip())
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            it = stack.pop(0)
            if isinstance(it, list):
                stack.extend(it)
            elif isinstance(it, dict):
                if "@graph" in it:
                    stack.extend(it["@graph"] if isinstance(it["@graph"], list) else [it["@graph"]])
                if "datePublished" in it or "dateModified" in it:
                    out.append(it)
    return out


def _this_article(entries, url, title):
    """The JSON-LD entry describing this page: same URL, else same headline, else the first."""
    want = _norm_url(url)
    for e in entries:
        ids = [e.get("url"), e.get("@id")]
        mep = e.get("mainEntityOfPage")
        ids += [mep.get("@id"), mep.get("url")] if isinstance(mep, dict) else [mep]
        if want and any(_norm_url(i) == want for i in ids if isinstance(i, str)):
            return e
    t = re.sub(r"\W+", " ", str(title or "")).strip().lower()
    for e in entries:
        h = re.sub(r"\W+", " ", str(e.get("headline") or e.get("name") or "")).strip().lower()
        if t and len(h) >= 15 and (h[:40] in t or t[:40] in h):
            return e
    return entries[0] if entries else None


def page_dates(html, url=""):
    """(published, modified) as dates or None, read from the page's own markup."""
    published = _meta(html, PUBLISHED_META)
    modified = _meta(html, MODIFIED_META)
    if published is None:
        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        e = _this_article(_jsonld_entries(html), url, title.group(1) if title else "")
        if e:
            published = _to_date(e.get("datePublished"))
            modified = modified or _to_date(e.get("dateModified"))
    if published is None and modified is None:
        t = re.search(r"<time\b[^>]*\bdatetime\s*=\s*[\"']([^\"']+)[\"']", html, re.I)
        published = _to_date(t.group(1)) if t else None
    return published, modified


def fetch_page_dates(url):
    """{'published', 'modified', 'error'} for a URL; cached for the run."""
    if url in _cache:
        return _cache[url]
    out = {"published": None, "modified": None, "error": None}
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        ctype = r.headers.get("Content-Type", "")
        if r.status_code != 200:
            out["error"] = f"HTTP {r.status_code}"
        elif "html" not in ctype.lower():
            out["error"] = f"not a web page ({ctype.split(';')[0] or 'unknown type'})"
        else:
            out["published"], out["modified"] = page_dates(r.text, url)
    except requests.RequestException as e:
        out["error"] = type(e).__name__
    _cache[url] = out
    return out


def confirm_source_date(claimed, page):
    """(ok, reason) - is a price dated `claimed` possible on a page with these dates?"""
    if page.get("error"):
        return False, f"could not open the page to confirm its date ({page['error']}) - needs human review"
    pub, mod = page.get("published"), page.get("modified")
    if pub is None and mod is None:
        return False, "no publication date on the page to confirm the price's date - needs human review"
    latest = max(d for d in (pub, mod) if d is not None)
    if claimed > latest + timedelta(days=TOLERANCE_DAYS):
        when = f"{pub or mod}" + (f", last updated {mod}" if pub and mod and mod != pub else "")
        return False, f"page is dated {when} but the search dated the price {claimed} - wrong date"
    return True, "page date confirms it"
