import re
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import requests

from ..models import Job
from .base import html_to_text
from .boards import UA, find_ats

WORLDWIDE = re.compile(r"worldwide|anywhere|global|^remote$", re.I)
WWR_CATEGORIES = ["remote-back-end-programming-jobs", "remote-full-stack-programming-jobs"]


def regions(value) -> list[str]:
    items = value if isinstance(value, list) else re.split(r"[,;/|]|\band\b", str(value or ""))
    return [str(x).strip() for x in items if x and str(x).strip()]


def remote_geo(regs: list[str], eligible: list[str]) -> tuple[str, str, bool]:
    if not regs or any(WORLDWIDE.search(r) for r in regs):
        return "Remote (Worldwide)", "worldwide", True
    ok = [r for r in regs if any(re.search(rf"\b{re.escape(e)}\b", r, re.I) for e in eligible)]
    if ok:
        return f"Remote ({', '.join(ok[:3])})", "restricted", True
    return f"Remote ({', '.join(regs[:4])})", "restricted", False


def _job(feed, jid, company, title, url, regs, eligible, raw_desc, pay="") -> Job:
    loc, scope, ok = remote_geo(regs, eligible)
    return Job(id=f"{feed}:{jid}", source="web", company=(company or "").strip(), title=(title or "").strip(),
               url=url, location=loc, description=f"{pay}\n{html_to_text(raw_desc)}".strip(),
               extra={"feed": feed, "remote_scope": scope, "geo_ok": ok, "ats": find_ats(raw_desc)})


def _get(url: str):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r


def remotive(q: str, eligible: list[str]) -> list[Job]:
    data = _get(f"https://remotive.com/api/remote-jobs?search={quote_plus(q)}").json()
    return [_job("remotive", j["id"], j.get("company_name"), j.get("title"), j.get("url", ""),
                 regions(j.get("candidate_required_location")), eligible, j.get("description", ""),
                 j.get("salary") or "")
            for j in data.get("jobs", [])]


def remoteok(q: str, eligible: list[str]) -> list[Job]:
    data = _get(f"https://remoteok.com/api?tag={quote_plus(q.replace(' ', '-'))}").json()
    out = []
    for j in (x for x in data if isinstance(x, dict) and x.get("id") and x.get("position")):
        lo, hi = j.get("salary_min"), j.get("salary_max")
        pay = f"Salary: USD {lo}-{hi}" if lo or hi else ""
        out.append(_job("remoteok", j["id"], j.get("company"), j.get("position"), j.get("url", ""),
                        regions(j.get("location")), eligible, j.get("description", ""), pay))
    return out


def himalayas(q: str, eligible: list[str]) -> list[Job]:
    data = _get(f"https://himalayas.app/jobs/api/search?q={quote_plus(q)}").json()
    out = []
    for j in data.get("jobs", []):
        lo, hi = j.get("minSalary"), j.get("maxSalary")
        pay = f"Salary: {j.get('currency') or ''} {lo}-{hi} {j.get('salaryPeriod') or ''}" if lo or hi else ""
        url = (j.get("guid") or j.get("applicationLink") or "").split("?")[0]
        out.append(_job("himalayas", url.rsplit("/", 1)[-1], j.get("companyName"), j.get("title"), url,
                        regions(j.get("locationRestrictions") or []), eligible, j.get("description", ""), pay))
    return out


def jobicy(q: str, eligible: list[str]) -> list[Job]:
    data = _get(f"https://jobicy.com/api/v2/remote-jobs?count=50&tag={quote_plus(q)}").json()
    out = []
    for j in data.get("jobs", []):
        lo, hi = j.get("salaryMin"), j.get("salaryMax")
        pay = f"Salary: {j.get('salaryCurrency') or ''} {lo}-{hi} {j.get('salaryPeriod') or ''}" if lo or hi else ""
        out.append(_job("jobicy", j["id"], j.get("companyName"), j.get("jobTitle"), j.get("url", ""),
                        regions(j.get("jobGeo")), eligible, j.get("jobDescription", ""), pay))
    return out


def wwr(category: str, eligible: list[str]) -> list[Job]:
    root = ET.fromstring(_get(f"https://weworkremotely.com/categories/{category}.rss").content)
    out = []
    for it in root.iter("item"):
        company, _, title = (it.findtext("title") or "").partition(": ")
        link = (it.findtext("link") or "").strip()
        regs = regions(it.findtext("country")) or regions(it.findtext("region"))
        out.append(_job("wwr", link.rstrip("/").rsplit("/", 1)[-1], company, title or company, link,
                        regs, eligible, it.findtext("description") or ""))
    return out


FEEDS = {"remotive": remotive, "remoteok": remoteok, "himalayas": himalayas, "jobicy": jobicy, "wwr": wwr}
