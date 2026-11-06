import re
from urllib.parse import quote, unquote

import requests

from ..models import Job
from .base import html_to_text

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130 Safari/537.36"}
APIS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
}
PROBE_APIS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json&limit=1",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}
GH_EMBED = "https://job-boards.greenhouse.io/embed/job_app?for={slug}&token={job_id}"
ATS_PATTERNS = [
    ("greenhouse", re.compile(r"greenhouse\.io/[^\s\"'<>]*?[?&]for=([\w-]+)", re.I)),
    ("greenhouse", re.compile(r"(?<![\w-])(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?!embed\b)([\w-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([^/?#\s\"'<>]+)", re.I)),
]
NAME_NOISE = re.compile(r"\b(inc|llc|ltd|limited|gmbh|corp|corporation|co|company|pvt|private|plc|technologies|"
                        r"technology|labs|hq|group|india)\b", re.I)


def find_ats(text: str) -> list[tuple[str, str]]:
    text = re.sub(r"\s*›\s*", "/", unquote(text or ""))
    hits = []
    for ats, rx in ATS_PATTERNS:
        for m in rx.finditer(text):
            if (ats, m.group(1)) not in hits:
                hits.append((ats, m.group(1)))
    return hits


def parse_ats_url(url: str) -> tuple[str, str] | None:
    hits = find_ats(url)
    return hits[0] if hits else None


def _greenhouse(slug: str, data: dict) -> list[Job]:
    return [Job(id=f"gh:{slug}:{j['id']}", source="greenhouse", company=j.get("company_name") or slug,
                title=j.get("title", ""), url=j.get("absolute_url", ""),
                location=(j.get("location") or {}).get("name", ""),
                description=html_to_text(j.get("content", "")),
                apply_url=GH_EMBED.format(slug=slug, job_id=j["id"]))
            for j in data.get("jobs", [])]


def _lever(slug: str, data: list) -> list[Job]:
    out = []
    for j in data:
        lists = "\n".join(f"{x.get('text', '')}: {html_to_text(x.get('content', ''))}" for x in j.get("lists", []))
        loc = (j.get("categories") or {}).get("location", "") or ""
        if str(j.get("workplaceType", "")).lower() == "remote" and "remote" not in loc.lower():
            loc = f"{loc} (Remote)".strip()
        out.append(Job(id=f"lever:{slug}:{j['id']}", source="lever", company=slug, title=j.get("text", ""),
                       url=j.get("hostedUrl", ""), location=loc,
                       description=f"{j.get('descriptionPlain', '')}\n{lists}\n{j.get('additionalPlain', '')}",
                       apply_url=j.get("applyUrl") or f"{j.get('hostedUrl', '')}/apply"))
    return out


def _ashby(slug: str, data: dict) -> list[Job]:
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [x.get("location", "") for x in j.get("secondaryLocations") or []]
        loc = "; ".join(x for x in locs if x)
        if j.get("workplaceType") == "Remote" and "remote" not in loc.lower():
            loc = f"{loc} (Remote)".strip()
        pay = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        out.append(Job(id=f"ashby:{slug}:{j['id']}", source="ashby", company=slug,
                       title=(j.get("title") or "").strip(), url=j.get("jobUrl", ""), location=loc,
                       description=f"{pay}\n{j.get('descriptionPlain', '')}".strip(),
                       apply_url=j.get("applyUrl") or f"{j.get('jobUrl', '')}/application"))
    return out


PARSERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}


def fetch_board(ats: str, slug: str, timeout: int = 30) -> list[Job] | None:
    r = requests.get(APIS[ats].format(slug=quote(slug, safe="")), headers=UA, timeout=timeout)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return PARSERS[ats](slug, r.json())


def slug_candidates(company: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", NAME_NOISE.sub(" ", (company or "").lower()))
    if not words:
        return []
    return list(dict.fromkeys(["".join(words), "-".join(words), "".join(w.capitalize() for w in words)]))


def probe_company(company: str, timeout: int = 10) -> list[tuple[str, str]]:
    cands, found = slug_candidates(company), []
    for ats, api in PROBE_APIS.items():
        for slug in (cands if ats == "lever" else [c for c in cands if c == c.lower()]):
            try:
                r = requests.get(api.format(slug=quote(slug, safe="")), headers=UA, timeout=timeout)
            except requests.RequestException:
                continue
            if r.ok:
                found.append((ats, slug))
                break
    return found
