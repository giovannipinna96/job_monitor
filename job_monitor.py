"""
Job Monitor v4 - Career page monitor + Telegram
Strategy: API-first (Greenhouse/Lever/Ashby/Workday/Oracle/MS/Amazon/Eightfold),
fallback Playwright per siti custom. Vedi commento iniziale dettagliato.
"""

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("job_monitor.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

DIR = Path(__file__).parent
SITES_PATH = DIR / "sites.json"
SETTINGS_PATH = DIR / "settings.json"
STATE_PATH = DIR / "seen_jobs.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
DEFAULT_HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}


def safe_get(url, *, headers=None, timeout=20, **kw):
    h = {**DEFAULT_HEADERS, **(headers or {})}
    return requests.get(url, headers=h, timeout=timeout, **kw)


def safe_post(url, *, json_body=None, headers=None, timeout=20, **kw):
    h = {**DEFAULT_HEADERS, "Content-Type": "application/json", **(headers or {})}
    return requests.post(url, json=json_body, headers=h, timeout=timeout, **kw)


def make_id(title, url):
    return hashlib.sha256(
        f"{title.strip().lower()}|{url.strip().lower()}".encode()
    ).hexdigest()[:16]


def site_key(url, name):
    return hashlib.sha256(f"{name}|{url}".encode()).hexdigest()[:12]


def detect_handler(url, hint=None):
    if hint:
        return hint
    u = url.lower()

    if ("boards.greenhouse.io" in u or "job-boards.greenhouse.io" in u
            or "job-boards.eu.greenhouse.io" in u):
        return "greenhouse"
    if "lever.co" in u and "/revolut" not in u:
        return "lever"
    if "ashbyhq.com" in u:
        return "ashby"
    if "myworkdayjobs.com" in u:
        return "workday"
    if "oraclecloud.com" in u or "taleo.net" in u:
        return "oracle"
    if "eightfold.ai" in u:
        return "eightfold"

    custom_map = {
        "amazon.jobs": "amazon",
        "careers.microsoft.com": "microsoft",
        "metacareers.com": "playwright",
        "google.com/about/careers": "playwright",
        "uber.com": "playwright",
        "stripe.com/jobs": "stripe_native",
        "jobs.netflix.com": "playwright",
        "jobs.sap.com": "sap",
        "higher.gs.com": "playwright",
        "careers.bcg.com": "phenom",
        "careers.snowflake.com": "phenom",
        "careers.allianz.com": "phenom",
        "search.jobs.barclays": "phenom",
        "jobs.booking.com": "phenom_booking",
        "careers.bain.com": "playwright",
        "career.bayer.com": "playwright",
        "samsung.com/us/careers": "playwright",
        "jobs.ubs.com": "playwright",
        "jobs.unicredit.eu": "playwright",
        "jobs.zalando.com": "playwright",
        "careers.blackrock.com": "blackrock_workday",
        "morganstanley.eightfold.ai": "eightfold",
        "janestreet.com": "playwright",
        "generali.com": "playwright",
        "lever.co/revolut": "playwright",
        "databricks.com/company/careers": "playwright",
        "optiver.com": "playwright",
        "mckinsey.com": "playwright",
    }
    for key, handler in custom_map.items():
        if key in u:
            return handler

    return "generic"


def handle_greenhouse(url, name):
    path = urlparse(url).path.strip("/").split("/")
    token = path[0] if path else ""
    if not token:
        return []
    api_urls = [
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
        f"https://api.greenhouse.io/v1/boards/{token}/jobs",
    ]
    for api in api_urls:
        try:
            r = safe_get(api, headers={"Accept": "application/json"})
            if r.status_code == 200:
                data = r.json()
                jobs = []
                for j in data.get("jobs", []):
                    jobs.append({
                        "title": (j.get("title") or "").strip(),
                        "url": j.get("absolute_url") or url,
                        "location": (j.get("location") or {}).get("name", ""),
                    })
                return jobs
            else:
                log.info(f"  Greenhouse {api} -> {r.status_code}")
        except Exception as e:
            log.warning(f"  Greenhouse {api}: {e}")
    return []


def handle_lever(url, name):
    path = urlparse(url).path.strip("/").split("/")
    company = path[0] if path else ""
    if not company:
        return []
    try:
        r = safe_get(f"https://api.lever.co/v0/postings/{company}?mode=json",
                     headers={"Accept": "application/json"})
        if r.status_code == 200:
            jobs = []
            for j in r.json():
                jobs.append({
                    "title": (j.get("text") or "").strip(),
                    "url": j.get("hostedUrl") or j.get("applyUrl") or url,
                    "location": (j.get("categories") or {}).get("location", ""),
                })
            return jobs
        log.info(f"  Lever -> {r.status_code}")
    except Exception as e:
        log.warning(f"  Lever: {e}")
    return []


ASHBY_QUERY = """
query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {
  jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) {
    teams { id name parentTeamId }
    jobPostings { id title teamId locationName employmentType
                  secondaryLocations { locationName } }
  }
}
"""


def handle_ashby(url, name):
    path = urlparse(url).path.strip("/").split("/")
    company = path[0] if path else ""
    if not company:
        return []
    try:
        r = safe_post(
            "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams",
            json_body={
                "operationName": "ApiJobBoardWithTeams",
                "variables": {"organizationHostedJobsPageName": company},
                "query": ASHBY_QUERY,
            },
        )
        if r.status_code != 200:
            log.warning(f"  Ashby: status {r.status_code}")
            return []
        board = (r.json().get("data") or {}).get("jobBoard") or {}
        return [{
            "title": (p.get("title") or "").strip(),
            "url": f"https://jobs.ashbyhq.com/{company}/{p.get('id', '')}",
            "location": p.get("locationName", "") or "",
        } for p in board.get("jobPostings", [])]
    except Exception as e:
        log.warning(f"  Ashby: {e}")
    return []


def workday_parts(url):
    p = urlparse(url)
    host = p.hostname or ""
    m = re.match(r"^([a-z0-9-]+)\.(wd[0-9]+)\.myworkdayjobs\.com$", host)
    if not m:
        return None
    tenant = m.group(1)
    parts = p.path.strip("/").split("/")
    site = None
    for part in parts:
        if part and not re.match(r"^[a-z]{2}-[A-Z]{2}$", part):
            site = part
            break
    if not site and parts:
        site = parts[-1]
    return host, tenant, site


def handle_workday(url, name, *, tenant=None, site=None, wd=None):
    parts = workday_parts(url)
    if parts:
        host, t, s = parts
        tenant = tenant or t
        site = site or s
    else:
        host = f"{tenant}.{wd or 'wd1'}.myworkdayjobs.com"
    if not tenant or not site:
        log.warning(f"  Workday parts mancanti: {url}")
        return []

    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}

    all_jobs, seen = [], set()
    try:
        for offset in range(0, 200, 20):
            body["offset"] = offset
            r = safe_post(api, json_body=body, headers={"Accept": "application/json"})
            if r.status_code != 200:
                if offset == 0:
                    log.warning(f"  Workday: status {r.status_code} body={r.text[:120]}")
                break
            d = r.json()
            postings = d.get("jobPostings", [])
            if not postings:
                break
            for j in postings:
                title = (j.get("title") or "").strip()
                if not title:
                    continue
                ext = j.get("externalPath", "")
                full_url = f"https://{host}{ext}" if ext else url
                key = title.lower()
                if key in seen:
                    continue
                seen.add(key)
                all_jobs.append({
                    "title": title, "url": full_url,
                    "location": j.get("locationsText", "") or j.get("locationCountry", ""),
                })
            if offset + 20 >= d.get("total", 0):
                break
    except Exception as e:
        log.warning(f"  Workday: {e}")
    return all_jobs


def handle_blackrock_workday(url, name):
    return handle_workday(
        "https://blackrock.wd1.myworkdayjobs.com/en-US/BlackRock_Professional",
        name, tenant="blackrock", site="BlackRock_Professional",
    )


def handle_oracle(url, name):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    m = re.search(r"/sites/([A-Z0-9_]+)", url)
    site_number = m.group(1) if m else "CX_1001"
    api = (f"{base}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
           f"?onlyData=true&expand=requisitionList.secondaryLocations,flexFieldsFacet.values"
           f"&finder=findReqs;siteNumber={site_number},"
           f"facetsList=LOCATIONS%3BWORK_LOCATIONS%3BWORKPLACE_TYPES%3BTITLES%3BCATEGORIES%3BORGANIZATIONS%3BPOSTING_DATES%3BFLEX_FIELDS,"
           f"limit=200,sortBy=POSTING_DATES_DESC")
    try:
        r = safe_get(api, headers={"Accept": "application/json", "REST-Framework-Version": "1"})
        if r.status_code != 200:
            log.warning(f"  Oracle: status {r.status_code}")
            return []
        items = r.json().get("items", [])
        if not items:
            return []
        req_list = items[0].get("requisitionList", [])
        return [{
            "title": (j.get("Title") or "").strip(),
            "url": (f"{base}/hcmUI/CandidateExperience/en/sites/{site_number}/"
                    f"requisitions/preview/{j.get('Id', '')}"),
            "location": j.get("PrimaryLocation", "") or j.get("PrimaryLocationCountry", ""),
        } for j in req_list]
    except Exception as e:
        log.warning(f"  Oracle: {e}")
    return []


def handle_microsoft(url, name):
    """Microsoft: prova l'API gcsservices, fallback Playwright."""
    api = "https://gcsservices.careers.microsoft.com/search/api/v1/search"
    params = {"q": "", "l": "en_us", "pg": 1, "pgSz": 50, "o": "Recent", "flt": "true"}
    all_jobs = []
    try:
        try:
            r = safe_get(api, headers={"Accept": "application/json",
                                       "Origin": "https://jobs.careers.microsoft.com",
                                       "Referer": "https://jobs.careers.microsoft.com/"},
                         params=params)
        except requests.exceptions.SSLError:
            import urllib3; urllib3.disable_warnings()
            r = safe_get(api, headers={"Accept": "application/json",
                                       "Origin": "https://jobs.careers.microsoft.com",
                                       "Referer": "https://jobs.careers.microsoft.com/"},
                         params=params, verify=False)
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            result = r.json().get("operationResult", {}).get("result", {})
            all_jobs = [{
                "title": (j.get("title") or "").strip(),
                "url": f"https://jobs.careers.microsoft.com/global/en/job/{j.get('jobId', '')}",
                "location": ", ".join((j.get("properties") or {}).get("locations", []) or []),
            } for j in result.get("jobs", [])]
        else:
            log.info(f"  Microsoft API: status {r.status_code}, fallback Playwright")
    except Exception as e:
        log.warning(f"  Microsoft API err: {e}, fallback Playwright")

    if all_jobs:
        return all_jobs
    # Fallback Playwright sulla SPA
    return handle_playwright(
        "https://jobs.careers.microsoft.com/global/en/search", name,
        profile={
            "wait": 12,
            "selectors": [
                "h2.MZGzlrn8gfgSs8TZHhv2", # Microsoft careers job title class
                "[data-test='job-title']",
                "[role='link'] [class*='title']",
                "a[href*='/job/'] h2",
                "a[href*='/job/'] h3",
                "div[class*='ms-DocumentCard'] a",
                "[class*='JobCard'] [class*='title']",
            ],
            "location_sels": ["[class*='location']", "span[aria-label*='location']"],
            "paginate": "scroll", "max_pages": 4,
        })


def handle_amazon(url, name):
    api = "https://www.amazon.jobs/en/search.json"
    all_jobs, seen = [], set()
    try:
        for offset in range(0, 200, 100):
            params = {"result_limit": 100, "offset": offset, "sort": "recent"}
            r = safe_get(api, headers={"Accept": "application/json"}, params=params)
            if r.status_code != 200:
                break
            jobs_raw = r.json().get("jobs", [])
            if not jobs_raw:
                break
            for j in jobs_raw:
                title = (j.get("title") or "").strip()
                if not title:
                    continue
                key = title.lower() + "|" + (j.get("id_icims") or "")
                if key in seen:
                    continue
                seen.add(key)
                all_jobs.append({
                    "title": title,
                    "url": "https://www.amazon.jobs" + (j.get("job_path") or ""),
                    "location": j.get("location") or j.get("normalized_location", ""),
                })
            if len(jobs_raw) < 100:
                break
    except Exception as e:
        log.warning(f"  Amazon: {e}")
    return all_jobs


def handle_eightfold(url, name, *, domain=None):
    p = urlparse(url)
    host = p.hostname or ""
    api = f"{p.scheme}://{host}/api/apply/v2/jobs"
    # Domain candidates: passed-in, or auto-derive
    candidates = []
    if domain:
        candidates.append(domain)
    # Pattern eightfold.ai -> .com
    if "eightfold.ai" in host:
        candidates.append(host.replace(".eightfold.ai", ".com"))
        candidates.append(host.split(".")[0] + ".com")
    # Pattern explore.jobs.X.net -> X.com
    parts = host.split(".")
    if len(parts) >= 2:
        candidates.append(parts[-2] + ".com")
        candidates.append(".".join(parts[-2:]))
    # Dedup ordered
    domain_guesses = list(dict.fromkeys(candidates))
    all_jobs, seen = [], set()
    headers = {"Accept": "application/json", "Referer": f"{p.scheme}://{host}/careers"}
    chosen_domain = None
    try:
        # Find a working domain on first call
        for dg in domain_guesses:
            params = {"start": 0, "num": 25, "domain": dg,
                      "Job_Posting_Display_Type__c": "Public",
                      "triggerGoButton": "false"}
            r = safe_get(api, headers=headers, params=params)
            if r.status_code == 200:
                chosen_domain = dg
                break
            elif r.status_code == 403:
                log.info(f"  Eightfold: 403 con domain={dg}")
            else:
                log.info(f"  Eightfold: {r.status_code} con domain={dg}")
        if not chosen_domain:
            log.warning(f"  Eightfold: nessun domain valido (provati: {domain_guesses})")
            raise RuntimeError("no working domain")

        for start in range(0, 500, 25):
            params = {"start": start, "num": 25, "domain": chosen_domain,
                      "Job_Posting_Display_Type__c": "Public",
                      "triggerGoButton": "false"}
            r = safe_get(api, headers=headers, params=params)
            if r.status_code != 200:
                break
            d = r.json()
            positions = d.get("positions", [])
            if not positions:
                break
            for pos in positions:
                title = (pos.get("name") or "").strip()
                if not title:
                    continue
                key = title.lower() + str(pos.get("id", ""))
                if key in seen:
                    continue
                seen.add(key)
                locs = pos.get("locations", []) or []
                location = ", ".join(locs[:3]) if isinstance(locs, list) else str(locs)
                all_jobs.append({
                    "title": title,
                    "url": pos.get("canonicalPositionUrl") or
                           f"{p.scheme}://{host}/careers?pid={pos.get('id', '')}",
                    "location": location,
                })
            if len(positions) < 25:
                break
    except Exception as e:
        log.warning(f"  Eightfold: {e}")
    if not all_jobs:
        log.info("  Eightfold API vuoto -> Playwright")
        return handle_playwright(url, name, profile={
            "wait": 12,
            "selectors": ["[data-test='position-card-title']", ".position-card h3",
                          ".position-title", ".position-card .position-name"],
            "location_sels": [".position-location",
                              "[data-test='position-card-location']"],
            "paginate": "scroll",
        })
    return all_jobs


def handle_sap(url, name):
    all_jobs, seen = [], set()
    base = "https://jobs.sap.com"
    try:
        for startrow in range(0, 200, 25):
            r = safe_get(f"{base}/search/?startrow={startrow}")
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, "lxml")
            new_count = 0
            for a in soup.select("a[href*='/job/']"):
                title = a.get_text(strip=True)
                href = a.get("href", "")
                if not title or len(title) < 5 or len(title) > 200:
                    continue
                full_url = urljoin(base, href)
                key = title.lower()
                if key in seen:
                    continue
                seen.add(key)
                loc = ""
                row = a.find_parent(["tr", "li", "div"])
                if row:
                    loc_el = row.select_one(".jobLocation, [class*=location]")
                    if loc_el:
                        loc = loc_el.get_text(strip=True)
                all_jobs.append({"title": title, "url": full_url, "location": loc})
                new_count += 1
            if new_count == 0:
                break
    except Exception as e:
        log.warning(f"  SAP: {e}")
    return all_jobs


def handle_phenom_booking(url, name):
    api = "https://jobs.booking.com/api/jobs"
    all_jobs, seen = [], set()
    try:
        for from_ in range(0, 200, 50):
            r = safe_get(api, headers={
                "Accept": "application/json",
                "Referer": "https://jobs.booking.com/careers/jobs",
            }, params={"from": from_, "size": 50})
            if r.status_code != 200:
                break
            jobs_raw = r.json().get("jobs", [])
            if not jobs_raw:
                break
            for j in jobs_raw:
                jd = j.get("data") if isinstance(j, dict) and "data" in j else j
                if not isinstance(jd, dict):
                    continue
                title = (jd.get("title") or "").strip()
                if not title:
                    continue
                key = title.lower()
                if key in seen:
                    continue
                seen.add(key)
                loc = jd.get("location", "")
                if not loc and isinstance(jd.get("multi_location"), list):
                    loc = ", ".join(jd["multi_location"][:2])
                all_jobs.append({
                    "title": title,
                    "url": jd.get("applyUrl") or jd.get("jobUrl") or url,
                    "location": loc or "",
                })
            if len(jobs_raw) < 50:
                break
    except Exception as e:
        log.warning(f"  Phenom Booking: {e}")
    return all_jobs


def handle_phenom(url, name):
    return handle_playwright(url, name, profile={
        "wait": 12,
        "selectors": [
            "[data-ph-at-job-title-text]", "a.au-target.job-title",
            ".job-tile .job-title", ".job-title a", "h3.job-title",
            ".au-target .job-title", ".jobs-list-item a",
            "[data-ph-at-id*='job-title']", ".job-card-title",
        ],
        "title_attr": "data-ph-at-job-title-text",
        "location_sels": ["[data-ph-at-job-location-text]", ".job-location",
                          ".au-target .location", "[class*=location]"],
        "next_sel": ("button.next, [aria-label='Next'], "
                     "[aria-label='Go to next page'], .pagination-next, "
                     "a.next-page, button[aria-label='next']"),
        "paginate": "click_next",
        "max_pages": 5,
    })


def handle_stripe_native(url, name):
    try:
        r = safe_get(url)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")
        nd = soup.find("script", id="__NEXT_DATA__")
        if nd:
            try:
                data = json.loads(nd.string)
                page_props = data.get("props", {}).get("pageProps", {})
                jobs_raw = (page_props.get("jobs") or page_props.get("results")
                            or page_props.get("openings") or [])
                if jobs_raw:
                    out = []
                    for j in jobs_raw:
                        loc = j.get("location")
                        if isinstance(loc, dict):
                            loc = loc.get("name", "")
                        out.append({
                            "title": (j.get("title") or j.get("name") or "").strip(),
                            "url": j.get("url") or j.get("absolute_url") or url,
                            "location": loc or "",
                        })
                    if out:
                        return out
            except Exception:
                pass
        out, seen = [], set()
        for a in soup.select("a[href*='/jobs/listing/']"):
            t = a.get_text(strip=True)
            if not t or t.lower() in seen:
                continue
            seen.add(t.lower())
            out.append({"title": t, "url": urljoin(url, a.get("href", "")),
                        "location": ""})
        if out:
            return out
        return handle_greenhouse("https://boards.greenhouse.io/stripe", name)
    except Exception as e:
        log.warning(f"  Stripe native: {e}")
    return []


GENERIC_PROFILE = {
    "wait": 10,
    "selectors": [
        "a[href*='/job/'] h3", "a[href*='/job/'] h2", "a[href*='/jobs/'] h3",
        "a[href*='/career'] h3", "a[href*='/position'] h3",
        ".job-card h3", ".job-title a", ".job-listing-title a", "h3.job-title",
        ".role-card h3", ".vacancy-card h3", ".career-item h3",
        ".position-title a", "li a h3", "li h3 a", "article h3 a",
        "article a h3", ".opening a",
        "[data-testid*='job'] h3", "[data-testid*='job'] [class*='title']",
        "[class*='JobCard'] [class*='title']",
        "[class*='job-card'] [class*='title']",
        "[class*='job-list'] a",
    ],
    "location_sels": [".job-location", ".location", "[class*='location']",
                      "span.location"],
    "paginate": "scroll",
    "next_sel": None,
    "max_pages": 4,
}


def handle_generic(url, name):
    return handle_playwright(url, name, profile=GENERIC_PROFILE)


_browser = None
_playwright = None


def get_browser():
    global _browser, _playwright
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"],
        )
        log.info("Browser Playwright avviato")
    return _browser


def close_browser():
    global _browser, _playwright
    if _browser:
        try: _browser.close()
        except: pass
        _browser = None
    if _playwright:
        try: _playwright.stop()
        except: pass
        _playwright = None


COOKIE_DISMISS_SELECTORS = [
    "#onetrust-accept-btn-handler", "button#onetrust-accept-btn-handler",
    "button[id*='accept']",
    "[aria-label='Accept all']", "[aria-label='Accept All Cookies']",
    "[aria-label='Accept all cookies']",
    "button:has-text('Accept all')", "button:has-text('Accept All')",
    "button:has-text('Accept Cookies')", "button:has-text('Accept')",
    "button:has-text('Accetta')", "button:has-text('Accetta tutti')",
    "button:has-text('Accetta tutto')", "button:has-text('Accetto')",
    "button:has-text('I agree')", "button:has-text('I Agree')",
    "button:has-text('Got it')", "button:has-text('OK')",
    "button:has-text('Allow all')", "button:has-text('Agree')",
    "[id*='cookie'] button:has-text('OK')",
    "[id*='cookie'] button:has-text('Accept')",
    "[class*='cookie'] button:has-text('OK')",
    "[class*='cookie'] button:has-text('Accept')",
    "[class*='Cookie'] button:has-text('Accept')",
    "#truste-consent-button", ".cmp-banner button.cmp-button-accept",
    ".cookie-banner button", "[data-test='cookie-banner-accept']",
    "button[data-cy='accept-cookies']",
]


def dismiss_cookie_banner(page):
    for sel in COOKIE_DISMISS_SELECTORS:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click(timeout=2000)
                time.sleep(0.6)
                return True
        except Exception:
            continue
    try:
        for f in page.frames:
            for txt in ("Accept all", "Accept All", "Accept",
                        "Agree", "Accetta", "Allow"):
                try:
                    btn = f.get_by_role("button", name=re.compile(txt, re.I))
                    if btn and btn.count() > 0:
                        btn.first.click(timeout=2000)
                        time.sleep(0.5)
                        return True
                except: continue
    except: pass
    return False


def handle_playwright(url, name, *, profile=None):
    profile = profile or GENERIC_PROFILE
    wait_s = profile.get("wait", 10)
    paginate = profile.get("paginate")
    next_sel = profile.get("next_sel")
    max_pages = profile.get("max_pages", 5)

    browser = get_browser()
    context = browser.new_context(
        user_agent=UA, viewport={"width": 1920, "height": 1080}, locale="en-US",
    )
    page = context.new_page()
    page.add_init_script(
        'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
    )

    pages_html = []
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(1.5)
        if dismiss_cookie_banner(page):
            time.sleep(1.0)
        time.sleep(wait_s)
        for _ in range(4):
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            time.sleep(0.7)
        pages_html.append(page.content())

        if paginate == "click_next" and next_sel:
            for _ in range(max_pages - 1):
                clicked = False
                for sel in next_sel.split(","):
                    sel = sel.strip()
                    try:
                        btn = page.query_selector(sel)
                        if btn and btn.is_visible() and btn.is_enabled():
                            try: btn.scroll_into_view_if_needed(timeout=2000)
                            except: pass
                            btn.click(timeout=3000)
                            clicked = True
                            time.sleep(3)
                            for _ in range(2):
                                page.evaluate("window.scrollBy(0, window.innerHeight)")
                                time.sleep(0.5)
                            pages_html.append(page.content())
                            break
                    except Exception:
                        continue
                if not clicked:
                    break
        elif paginate == "scroll":
            prev_h = 0
            stale = 0
            for _ in range(max_pages * 4):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)
                cur_h = page.evaluate("document.body.scrollHeight")
                if cur_h == prev_h:
                    stale += 1
                    if stale >= 3:
                        break
                else:
                    stale = 0
                    prev_h = cur_h
            pages_html = [page.content()]
    except Exception as e:
        log.warning(f"  Playwright {name}: {e}")
    finally:
        try: context.close()
        except: pass

    if not pages_html:
        return []
    return extract_with_profile(pages_html, url, profile)


def _find_container(el):
    c = el.parent
    while c and c.name not in ("li", "div", "article", "tr", "section"):
        c = c.parent
    return c or el.parent


def extract_with_profile(html_pages, base_url, profile):
    selectors = profile.get("selectors", GENERIC_PROFILE["selectors"])
    location_sels = profile.get("location_sels", GENERIC_PROFILE["location_sels"])
    title_attr = profile.get("title_attr")
    all_jobs, seen = [], set()
    for html in html_pages:
        soup = BeautifulSoup(html, "lxml")
        for sel in selectors:
            elements = soup.select(sel)
            if not elements:
                continue
            page_jobs = []
            for el in elements:
                title = ""
                if title_attr:
                    title = (el.get(title_attr) or "").strip()
                if not title:
                    title = el.get_text(strip=True)
                if not title or len(title) < 3 or len(title) > 250:
                    continue
                ahref = ""
                if el.name == "a" and el.get("href"):
                    ahref = el["href"]
                else:
                    parent_a = el.find_parent("a")
                    if parent_a:
                        ahref = parent_a.get("href", "")
                    else:
                        cont = _find_container(el)
                        if cont:
                            a = cont.find("a", href=True)
                            if a:
                                ahref = a["href"]
                if ahref and not ahref.startswith("http"):
                    ahref = urljoin(base_url, ahref)
                location = ""
                cont = _find_container(el)
                if cont:
                    for ls in location_sels:
                        le = cont.select_one(ls)
                        if le:
                            location = le.get_text(strip=True)
                            break
                page_jobs.append({"title": title, "url": ahref or base_url,
                                  "location": location})
            if page_jobs:
                for j in page_jobs:
                    k = j["title"].lower()
                    if k not in seen:
                        seen.add(k)
                        all_jobs.append(j)
                break
    return all_jobs


HANDLERS = {
    "greenhouse": handle_greenhouse,
    "lever": handle_lever,
    "ashby": handle_ashby,
    "workday": handle_workday,
    "blackrock_workday": handle_blackrock_workday,
    "oracle": handle_oracle,
    "microsoft": handle_microsoft,
    "amazon": handle_amazon,
    "eightfold": handle_eightfold,
    "sap": handle_sap,
    "phenom": handle_phenom,
    "phenom_booking": handle_phenom_booking,
    "stripe_native": handle_stripe_native,
    "playwright": handle_generic,
    "generic": handle_generic,
}


def fetch_jobs(site):
    name = site["name"]
    url = site["url"]
    handler_name = detect_handler(url, site.get("type"))
    handler = HANDLERS.get(handler_name, handle_generic)
    log.info(f"  Handler: {handler_name}")
    try:
        jobs = handler(url, name) or []
    except Exception as e:
        log.error(f"  Errore handler {handler_name}: {e}", exc_info=True)
        jobs = []
    seen = set()
    out = []
    for j in jobs:
        k = (j.get("title") or "").lower().strip()
        if k and k not in seen:
            seen.add(k)
            out.append(j)
    return out


def send_telegram(token, chat_id, message):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
        if r.status_code != 200:
            log.warning(f"Telegram errore: {r.text[:200]}")
    except requests.RequestException as e:
        log.warning(f"Telegram errore: {e}")


def format_message(company, job):
    parts = [
        "Nuova offerta!", "",
        f"Azienda: {company}",
        f"Ruolo: {job['title']}",
    ]
    if job.get("location"):
        parts.append(f"Location: {job['location']}")
    parts.append(f'Link: {job["url"]}')
    parts.append(f"\n{datetime.now().strftime('%d/%m/%Y %H:%M')}")
    return "\n".join(parts)


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def cleanup_state(state, days=90):
    now = datetime.now()
    for key in list(state.keys()):
        jobs = state[key].get("jobs", {})
        old = [
            jid for jid, info in jobs.items()
            if (now - datetime.fromisoformat(info.get("seen", now.isoformat()))).days > days
        ]
        for jid in old:
            del jobs[jid]


def load_settings():
    if not SETTINGS_PATH.exists():
        log.error(f"File {SETTINGS_PATH} non trovato!")
        sys.exit(1)
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        s = json.load(f)
    if not s.get("telegram_token") or "IL_TUO" in s.get("telegram_token", ""):
        log.error("Inserisci il token Telegram in settings.json!")
        sys.exit(1)
    return s


def load_sites():
    if not SITES_PATH.exists():
        log.error(f"File {SITES_PATH} non trovato!")
        sys.exit(1)
    with open(SITES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def check_site(site, state, settings, *, test_mode=False):
    name = site["name"]
    url = site["url"]
    log.info("=" * 50)
    log.info(f"Sito: {name}")
    log.info(f"  URL: {url}")
    jobs = fetch_jobs(site)
    log.info(f"  -> {len(jobs)} offerte trovate")
    if test_mode:
        for j in jobs[:3]:
            log.info(f"    - {j['title']} - {j.get('location','')}")
        return len(jobs)
    if not jobs:
        return 0
    sk = site_key(url, name)
    if sk not in state:
        state[sk] = {"name": name, "jobs": {}}
        for j in jobs:
            state[sk]["jobs"][make_id(j["title"], j["url"])] = {
                "title": j["title"], "seen": datetime.now().isoformat()
            }
        log.info(f"  Prima esecuzione: {len(jobs)} offerte salvate")
        return 0
    new_count = 0
    silent_count = 0
    token = settings["telegram_token"]
    chat_id = settings["telegram_chat_id"]
    # Filtro per parole chiave nel titolo. Se la lista e' vuota o assente,
    # tutte le offerte nuove generano una notifica (comportamento storico).
    # Altrimenti notifica solo quelle il cui titolo contiene almeno una
    # keyword (case-insensitive). Le altre vengono comunque salvate in
    # seen_jobs.json per evitare di rivalutarle ad ogni ciclo.
    raw_keywords = settings.get("title_keyword") or []
    keywords_lower = [k.lower().strip() for k in raw_keywords if k and k.strip()]
    for j in jobs:
        jid = make_id(j["title"], j["url"])
        if jid in state[sk]["jobs"]:
            continue
        title_l = (j.get("title") or "").lower()
        # se la lista keywords e' vuota -> notifica tutto; altrimenti
        # cerca match parziale (case-insensitive)
        notify = (not keywords_lower) or any(kw in title_l for kw in keywords_lower)
        state[sk]["jobs"][jid] = {
            "title": j["title"], "seen": datetime.now().isoformat()
        }
        if notify:
            log.info(f"  NEW [notify]: {j['title']}")
            send_telegram(token, chat_id, format_message(name, j))
            new_count += 1
            time.sleep(1)
        else:
            log.info(f"  NEW [silent, no keyword match]: {j['title']}")
            silent_count += 1
    if new_count == 0 and silent_count == 0:
        log.info("  Nessuna novità")
    elif new_count == 0:
        log.info(f"  {silent_count} nuove offerte salvate ma nessuna matcha le keyword")
    return new_count


def run_cycle(settings, *, test_mode=False, only_site=None):
    sites = load_sites()
    if only_site:
        sites = [s for s in sites if s["name"].lower() == only_site.lower()]
    state = {} if test_mode else load_state()
    total = 0
    log.info(f"\nCiclo: {len(sites)} siti da controllare")
    for site in sites:
        try:
            total += check_site(site, state, settings, test_mode=test_mode)
        except Exception as e:
            log.error(f"  Errore {site.get('name', '?')}: {e}", exc_info=True)
        time.sleep(2)
    if not test_mode:
        cleanup_state(state)
        save_state(state)
    log.info("-" * 50)
    log.info(f"Ciclo completato: {total} nuove offerte\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true",
                        help="Un singolo ciclo, no notifiche, no stato (per debug)")
    parser.add_argument("--once", action="store_true",
                        help="Un singolo ciclo, manda notifiche e salva stato (per cron/CI)")
    parser.add_argument("--site", default=None,
                        help="Limita a un singolo sito")
    args = parser.parse_args()

    if args.test:
        settings = {"telegram_token": "test", "telegram_chat_id": "test"}
    else:
        settings = load_settings()

    interval = settings.get("check_interval_minutes", 30)
    sites = load_sites()
    log.info("=" * 60)
    log.info(f"Job Monitor v4 - {len(sites)} siti - ogni {interval} min")
    log.info("=" * 60)

    if args.test:
        try:
            run_cycle(settings, test_mode=True, only_site=args.site)
        finally:
            close_browser()
        return

    if args.once:
        log.info("Modalita --once: un solo ciclo poi exit")
        try:
            run_cycle(settings, only_site=args.site)
        finally:
            close_browser()
        return

    send_telegram(
        settings["telegram_token"],
        settings["telegram_chat_id"],
        f"<b>Job Monitor v4 avviato!</b>\nMonitoraggio {len(sites)} siti ogni {interval} min.",
    )
    try:
        while True:
            try:
                run_cycle(settings)
            except Exception as e:
                log.error(f"Errore ciclo: {e}", exc_info=True)
            log.info(f"Prossimo ciclo tra {interval} min...")
            time.sleep(interval * 60)
    except KeyboardInterrupt:
        log.info("Arresto.")
    finally:
        close_browser()


if __name__ == "__main__":
    main()
