from taskagent.core.llm import parse_json
from taskagent.skills.jobs.adapters.base import external_form_for, html_to_text
from taskagent.skills.jobs.adapters.linkedin import LinkedInAdapter, search_url
from taskagent.skills.jobs.db import DB
from taskagent.skills.jobs.forms import deterministic_value, match_option
from taskagent.skills.jobs.matcher import passes_filters, passes_requirements, worth_scope_search
from taskagent.skills.jobs.models import Job, MatchResult
from taskagent.skills.jobs.websearch import WebSearch

PROFILE = {"personal": {"first_name": "Jane", "last_name": "Doe", "email": "a@b.com",
                        "phone": "+91 1", "linkedin": "https://linkedin.com/in/x", "resume_pdf": "C:/r.pdf"}}
FILTERS = {"title_include": ["software engineer", "backend"], "title_exclude": ["intern", "manager"],
           "locations": ["remote", "india"]}


def job(title="Senior Software Engineer", location="Bengaluru, India"):
    return Job(id="x", source="t", company="c", title=title, url="u", location=location)


def test_parse_json_plain_fenced_and_embedded():
    assert parse_json('{"score": 80}') == {"score": 80}
    assert parse_json('```json\n{"score": 80}\n```') == {"score": 80}
    assert parse_json('Sure! {"score": 80} hope it helps') == {"score": 80}


def test_filters():
    assert passes_filters(job(), FILTERS)[0]
    assert not passes_filters(job("Software Engineer Intern"), FILTERS)[0]
    assert not passes_filters(job("Data Scientist"), FILTERS)[0]
    assert passes_filters(job("Data Scientist"), {**FILTERS, "title_include": []})[0]
    assert not passes_filters(job("Engineering Manager"), {**FILTERS, "title_include": []})[0]
    assert not passes_filters(job(location="Berlin, Germany"), FILTERS)[0]
    assert passes_filters(job(location=""), FILTERS)[0]
    f = {**FILTERS, "remote_exclude": ["us", "united states", "canada"]}
    assert passes_filters(job(location="Remote"), f)[0]
    assert not passes_filters(job(location="US-Remote"), f)[0]
    assert not passes_filters(job(location="Remote, United States"), f)[0]
    assert not passes_filters(job(location="Toronto Canada, Remote in US, Remote in Canada"), f)[0]
    assert passes_filters(job(location="Remote in US, Bengaluru, India"), f)[0]


def test_requirements_remote_worldwide_and_currency():
    m = lambda scope, cur: MatchResult(score=80, remote_scope=scope, pay_currency=cur)
    assert passes_requirements(m("restricted", "INR"), {})[0]
    req = {"require_remote_worldwide": True, "pay_currencies": ["usd"]}
    assert passes_requirements(m("worldwide", "USD"), req)[0]
    assert passes_requirements(m("Worldwide", "usd"), req)[0]
    assert not passes_requirements(m("restricted", "USD"), req)[0]
    assert not passes_requirements(m("onsite", "USD"), req)[0]
    assert not passes_requirements(m("worldwide", "EUR"), req)[0]
    assert not passes_requirements(m("worldwide", "unknown"), req)[0]
    assert passes_requirements(m("worldwide", "unknown"), {**req, "allow_unknown_pay": True})[0]
    assert not passes_requirements(m("worldwide", "INR"), {**req, "allow_unknown_pay": True})[0]
    assert not passes_requirements(m("unknown", "USD"), req)[0]
    assert passes_requirements(m("unknown", "USD"), {**req, "allow_unknown_scope": True})[0]
    assert not passes_requirements(m("restricted", "USD"), {**req, "allow_unknown_scope": True})[0]


def test_worth_scope_search():
    m = lambda scope, cur: MatchResult(score=80, remote_scope=scope, pay_currency=cur)
    req = {"require_remote_worldwide": True, "pay_currencies": ["USD"]}
    scopes = ["unknown", "restricted"]
    assert worth_scope_search(m("unknown", "USD"), req, scopes)
    assert worth_scope_search(m("Restricted", "USD"), req, scopes)
    assert not worth_scope_search(m("worldwide", "USD"), req, scopes)
    assert not worth_scope_search(m("onsite", "USD"), req, scopes)
    assert not worth_scope_search(m("unknown", "EUR"), req, scopes)
    assert not worth_scope_search(m("unknown", "USD"), {**req, "require_remote_worldwide": False}, scopes)
    assert worth_scope_search(m("restricted", "USD"), {**req, "allow_unknown_scope": True}, scopes)


def test_websearch_remote_scope():
    class FakeLLM:
        def json(self, system, user):
            assert "Acme hires from anywhere" in user
            return {"remote_scope": "Worldwide", "evidence": "hires anywhere"}
    ws = WebSearch(None, FakeLLM(), {"query": "{company} remote"})
    queries = []
    ws.results = lambda q: queries.append(q) or "Acme hires from anywhere"
    assert ws.remote_scope(job()) == ("worldwide", "hires anywhere")
    assert queries == ["c remote"]
    ws.results = lambda q: ""
    assert ws.remote_scope(job())[0] == ""


def test_linkedin_details_read_from_pane_without_navigation():
    info = {"title": "Senior Java Developer | RekNomics | LinkedIn",
            "lines": ["RekNomics", "Senior Java Developer", "Chennai, Tamil Nadu, India · 1 day ago · Over 100 applicants",
                      "2M INR/yr - 3.5M INR/yr", "On-site", "Full-time", "Easy Apply", "Save"],
            "header": ["RekNomics", "Senior Java Developer", "2M INR/yr - 3.5M INR/yr", "On-site"],
            "description": "About the job\nBuild backend services", "easy": True, "applied": False}

    class FakeDriver:
        def execute_script(self, js, *args):
            return info

        def get(self, url):
            raise AssertionError("must not navigate")

    j = LinkedInAdapter({}, {}, None, FakeDriver())._job_details("123")
    assert (j.id, j.title, j.company) == ("li:123", "Senior Java Developer", "RekNomics")
    assert j.location == "Chennai, Tamil Nadu, India (On-site)"
    assert "2M INR/yr" in j.description and "Build backend services" in j.description
    assert j.extra == {"easy_apply": True, "applied": False}


def test_linkedin_search_url_from_config():
    base = "https://www.linkedin.com/jobs/search-results/?keywords="
    assert search_url("senior java backend engineer", "92000000", 24, True) == \
        base + "senior%20java%20backend%20engineer&geoId=92000000&f_TPR=r86400&f_AL=true"
    assert search_url("java", None, None, False) == base + "java"
    assert "f_TPR=r604800" in search_url("java", None, 168, True)
    cfg = {"sources": {"linkedin_search_urls": ["https://raw"],
                       "linkedin_search": {"keywords": ["java", "backend"], "location": [92000000, "102713980"],
                                           "posted_within_hours": 24}}}
    urls = LinkedInAdapter(cfg, {}, None, None).search_urls()
    assert urls[0] == "https://raw" and len(urls) == 5
    assert urls[1] == base + "java&geoId=92000000&f_TPR=r86400&f_AL=true"
    assert urls[4] == base + "backend&geoId=102713980&f_TPR=r86400&f_AL=true"
    assert LinkedInAdapter({"sources": {}}, {}, None, None).search_urls() == []
    assert search_url("java", None, None, False, remote=True) == base + "java&f_SAL=f_SA_id_225001%3A272001"


def test_linkedin_remote_only_skips_onsite_and_hybrid_cards():
    cfg = {"sources": {"linkedin_search": {"remote_only": True}}, "limits": {"delay_seconds": [0, 0]}}
    a = LinkedInAdapter(cfg, {}, None, None)
    a.search_urls = lambda: ["u"]

    def load(url, page):
        a._cards = {"1": ("A", "On-site"), "2": ("B", "Remote"), "3": ("C", "Hybrid"), "4": ("D", "")}
        return list(a._cards)

    opened = []
    a._load_results = load
    a._open_card = lambda jid, url, page: opened.append(jid) or True
    a._job_details = lambda jid: Job(id=f"li:{jid}", source="linkedin", company="c", title="t", url="u", extra={})
    assert [j.id for j in a.discover()] == ["li:2", "li:4"]
    assert opened == ["2", "4"]


def test_deterministic_values():
    f = lambda label, kind="text": {"label": label, "kind": kind}
    assert deterministic_value(f("First Name *"), PROFILE) == "Jane"
    assert deterministic_value(f("Last name"), PROFILE) == "Doe"
    assert deterministic_value(f("Full Name"), PROFILE) == "Jane Doe"
    assert deterministic_value(f("Email", "email"), PROFILE) == "a@b.com"
    assert deterministic_value(f("LinkedIn Profile", "url"), PROFILE) == "https://linkedin.com/in/x"
    assert deterministic_value(f("Resume/CV", "file"), PROFILE) == "C:/r.pdf"
    assert deterministic_value(f("Cover Letter", "file"), PROFILE) is None
    assert deterministic_value({"label": "Attach", "kind": "file", "hint": "resume resume"}, PROFILE) == "C:/r.pdf"
    assert deterministic_value({"label": "Attach", "kind": "file", "hint": "cover_letter "}, PROFILE) is None
    assert deterministic_value(f("Why do you want to join?", "textarea"), PROFILE) is None


def test_match_option():
    opts = ["Yes", "No", "Prefer not to say"]
    assert match_option(opts, "yes") == 0
    assert match_option(opts, "No") == 1
    assert match_option(opts, "prefer not") == 2
    assert match_option(opts, "maybe") == -1


def test_html_to_text_and_routing():
    assert "Hello world" in html_to_text("&lt;p&gt;Hello <b>world</b>&lt;/p&gt;")
    assert external_form_for("https://job-boards.greenhouse.io/x/jobs/1") is not None
    assert external_form_for("https://jobs.lever.co/x/1/apply") is not None
    assert external_form_for("https://myworkdayjobs.com/x") is None


def test_db_seen_and_reprocess(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    j = job()
    assert not db.seen(j.id)
    db.upsert(j, "dry_run", 80)
    assert not db.seen(j.id)
    db.upsert(j, "submitted")
    assert db.seen(j.id)
    assert db.count_today("t") == 1
    twin = Job(id="y", source="t", company="c", title="Senior Software Engineer", url="u2")
    assert db.applied_same_role(twin)
    assert not db.applied_same_role(job(title="Backend Engineer"))
    db.upsert(Job(id="z", source="t", company="D", title="Senior Software Engineer - Backend", url="u3"), "submitted")
    assert db.applied_same_role(Job(id="w", source="t", company="D", title="Senior Software Engineer (Backend)", url="u4"))
    assert db.company_count("c") == 1 and db.company_count("d") == 1 and db.company_count("x") == 0
