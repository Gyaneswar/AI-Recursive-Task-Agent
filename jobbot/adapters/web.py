import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import quote_plus, urlparse

import requests
from selenium.common.exceptions import WebDriverException

from ..browser import human_delay
from ..db import DB
from ..forms import _click
from ..models import ApplyResult, Job
from .base import Adapter, external_form_for
from .boards import fetch_board, find_ats, probe_company
from .feeds import FEEDS, WWR_CATEGORIES

LINKS_JS = "return [...document.querySelectorAll('a[href]')].map(a => a.href).concat([document.body.innerText]);"
APPLY_JS = r"""
const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const text = e => (e.innerText || e.getAttribute('aria-label') || '').trim();
const els = [...document.querySelectorAll('a, button')].filter(e => vis(e) && /^apply\b/i.test(text(e)) && text(e).length < 40);
return els.find(e => e.tagName === 'A' && e.href && !e.href.startsWith(location.origin)) || els[0] || null;
"""
DORK_URL = "https://www.google.com/search?hl=en&num=50&q={q}"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def harvest_search(driver, url_tpl: str, query: str) -> list[tuple[str, str]] | None:
    main, texts = driver.current_window_handle, []
    try:
        driver.switch_to.new_window("tab")
        driver.get(url_tpl.format(q=quote_plus(query)))
        time.sleep(3)
        if "/sorry/" in driver.current_url or "captcha" in driver.current_url.lower():
            print("[web] search engine CAPTCHA - solve it once in the browser; skipping searches this cycle")
            return None
        texts = driver.execute_script(LINKS_JS) or []
    except WebDriverException as e:
        print(f"[web] search '{query}': {getattr(e, 'msg', e)}")
    finally:
        try:
            if driver.current_window_handle != main:
                driver.close()
            driver.switch_to.window(main)
        except WebDriverException:
            pass
    return find_ats(" ".join(texts))


def resolve_apply_url(driver, url: str, wait: float = 10) -> str:
    driver.get(url)
    time.sleep(3)
    host = urlparse(driver.current_url).netloc
    el = driver.execute_script(APPLY_JS)
    if el is None:
        return ""
    href = el.get_attribute("href") or ""
    if href.startswith("mailto:"):
        return href
    handles, main = set(driver.window_handles), driver.current_window_handle
    driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'instant'});", el)
    _click(driver, el)
    end = time.time() + wait
    while time.time() < end:
        time.sleep(0.5)
        new = [h for h in driver.window_handles if h not in handles]
        if new:
            driver.switch_to.window(new[0])
            while time.time() < end and urlparse(driver.current_url).netloc in ("", host):
                time.sleep(0.5)
            final = driver.current_url
            driver.close()
            driver.switch_to.window(main)
            return final if urlparse(final).netloc not in ("", host) else ""
        if urlparse(driver.current_url).netloc not in ("", host):
            time.sleep(1)
            return driver.current_url
    return ""


class WebAdapter(Adapter):
    source = "web"

    def __init__(self, cfg: dict, profile: dict, llm, driver=None):
        super().__init__(cfg, profile, llm, driver)
        self.opts = cfg.get("discovery") or {}
        self.db = DB(cfg["paths"]["db"]) if cfg.get("paths") else None

    def _title_ok(self, title: str) -> bool:
        words = self.opts.get("title_any") or []
        return not words or any(w.lower() in (title or "").lower() for w in words)

    def register(self, hits, origin: str):
        for ats, slug in hits:
            if self.db.add_board(ats, slug, origin):
                print(f"[web] + board {ats}:{slug} ({origin})")

    def seed(self):
        src = self.cfg.get("sources") or {}
        for ats, key in (("greenhouse", "greenhouse_boards"), ("lever", "lever_companies"), ("ashby", "ashby_boards")):
            for slug in src.get(key) or []:
                self.db.add_board(ats, str(slug), "config")

    def feed_jobs(self) -> list[Job]:
        eligible = self.opts.get("eligible_regions") or []
        raw = []
        for name in self.opts.get("feeds") or []:
            fn = FEEDS.get(name)
            if fn is None:
                print(f"[web] unknown feed '{name}'")
                continue
            for arg in (WWR_CATEGORIES if name == "wwr" else self.opts.get("keywords") or []):
                try:
                    raw += fn(arg, eligible)
                except (requests.RequestException, ValueError, ET.ParseError) as e:
                    print(f"[web] {name} '{arg}': {e}")
        worldwide_only = (self.cfg.get("filters") or {}).get("require_remote_worldwide")
        seen, keep, dropped = set(), [], 0
        for j in raw:
            key = (_norm(j.company), _norm(j.title))
            if j.id in seen or key in seen:
                continue
            seen |= {j.id, key}
            self.register(j.extra.get("ats") or [], f"feed:{j.extra.get('feed')}")
            geo_ok = j.extra.get("geo_ok", True) and not (worldwide_only and j.extra.get("remote_scope") != "worldwide")
            if not self._title_ok(j.title) or not geo_ok:
                dropped += 1
                continue
            keep.append(j)
        print(f"[web] feeds: {len(raw)} raw, {len(keep)} candidates ({dropped} dropped by title/geo)")
        return keep

    def probe(self, companies: list[str]):
        limit = int(self.opts.get("probe_per_run", 30))
        for company in self.db.unprobed_companies(limit, companies) if limit > 0 else []:
            found = probe_company(company)
            self.db.add_probe(company, found)
            self.register(found, f"probe:{company}")

    def dorks(self):
        queries = self.opts.get("search_queries") or []
        last = self.db.get_meta("search_at")
        every = timedelta(hours=float(self.opts.get("search_every_hours", 24)))
        if not queries or self.driver is None or (last and datetime.fromisoformat(last) > datetime.now() - every):
            return
        self.db.set_meta("search_at", datetime.now().isoformat(timespec="seconds"))
        for q in queries:
            hits = harvest_search(self.driver, self.opts.get("search_url", DORK_URL), q)
            if hits is None:
                return
            print(f"[web] search '{q}': {len(hits)} board links")
            self.register(hits, "search")
            human_delay(self.cfg["limits"]["delay_seconds"])

    def board_jobs(self):
        due = self.db.due_boards(float(self.opts.get("board_poll_hours", 6)))
        print(f"[web] polling {len(due)} boards")
        for ats, slug in due:
            try:
                jobs = fetch_board(ats, slug)
            except (requests.RequestException, ValueError) as e:
                print(f"[web] {ats}:{slug}: {e}")
                continue
            self.db.mark_board(ats, slug, "dead" if jobs is None else "active")
            if jobs is None:
                print(f"[web] {ats}:{slug}: not found -> dead")
                continue
            yield from (j for j in jobs if self._title_ok(j.title))

    def discover(self):
        self.seed()
        feed = self.feed_jobs() if self.opts.get("feeds") else []
        self.probe([j.company for j in feed])
        self.dorks()
        seen = set()
        for j in self.board_jobs():
            seen.add((_norm(j.company), _norm(j.title)))
            yield j
        yield from (j for j in feed if (_norm(j.company), _norm(j.title)) not in seen)

    def apply(self, driver, job: Job) -> ApplyResult:
        url = job.apply_url
        if not external_form_for(url):
            url = resolve_apply_url(driver, job.url) if job.url else ""
            if not url:
                return ApplyResult("needs_manual", "apply link not found", self._shot(driver, job))
            job.apply_url = url
            self.register(find_ats(url), "resolved")
        spec = external_form_for(url)
        if not spec:
            return ApplyResult("needs_manual", f"external apply (unsupported site: {urlparse(url).netloc or url[:60]})")
        return self.apply_form_page(driver, job, url, *spec)
