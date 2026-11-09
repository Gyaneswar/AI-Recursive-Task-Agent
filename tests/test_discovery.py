from taskagent.skills.jobs.adapters import web
from taskagent.skills.jobs.adapters.boards import _ashby, _lever, find_ats, parse_ats_url, slug_candidates
from taskagent.skills.jobs.adapters.feeds import regions, remote_geo
from taskagent.skills.jobs.db import DB
from taskagent.skills.jobs.models import ApplyResult, Job

ELIGIBLE = ["india", "asia", "apac"]


def test_find_ats_urls_and_search_breadcrumbs():
    assert parse_ats_url("https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=1") == ("greenhouse", "stripe")
    assert parse_ats_url("https://boards.greenhouse.io/gitlab/jobs/123") == ("greenhouse", "gitlab")
    assert parse_ats_url("https://jobs.lever.co/Onehouse/3531e786/apply?source=LinkedIn") == ("lever", "Onehouse")
    assert parse_ats_url("https://jobs.ashbyhq.com/ramp/34413f8d/application") == ("ashby", "ramp")
    assert parse_ats_url("https://boards-api.greenhouse.io/v1/boards/x/jobs") is None
    assert parse_ats_url("https://careers.ibm.com/job/1") is None
    assert find_ats("//duckduckgo.com/l/?uddg=https%3A%2F%2Fjobs.lever.co%2Facme%2F123") == [("lever", "acme")]
    assert find_ats("https://jobs.ashbyhq.com\u00a0›\u00a0Linear › abc") == [("ashby", "Linear")]
    text = "apply at https://jobs.lever.co/acme/1 or https://jobs.lever.co/acme/2 and boards.greenhouse.io/beta"
    assert find_ats(text) == [("greenhouse", "beta"), ("lever", "acme")]


def test_slug_candidates():
    assert slug_candidates("Clearwater Analytics") == ["clearwateranalytics", "clearwater-analytics",
                                                       "ClearwaterAnalytics"]
    assert slug_candidates("Onehouse") == ["onehouse", "Onehouse"]
    assert slug_candidates("Acme Technologies Pvt Ltd")[0] == "acme"
    assert slug_candidates("Inc.") == []


def test_regions_and_remote_geo():
    assert regions("Europe, Americas") == ["Europe", "Americas"]
    assert regions("Canada and USA") == ["Canada", "USA"]
    assert regions(["Brazil"]) == ["Brazil"]
    assert remote_geo([], ELIGIBLE) == ("Remote (Worldwide)", "worldwide", True)
    assert remote_geo(["Anywhere in the World"], ELIGIBLE)[1:] == ("worldwide", True)
    assert remote_geo(["USA", "India"], ELIGIBLE) == ("Remote (India)", "restricted", True)
    assert remote_geo(["Southeast Asia"], ELIGIBLE)[2]
    assert remote_geo(["USA"], ELIGIBLE) == ("Remote (USA)", "restricted", False)
    assert not remote_geo(["Indiana"], ELIGIBLE)[2]


def test_ats_parsers():
    lv = _lever("Acme", [{"id": "1", "text": "Backend Engineer", "hostedUrl": "https://jobs.lever.co/Acme/1",
                          "categories": {"location": "Bengaluru"}, "workplaceType": "remote",
                          "lists": [{"text": "Req", "content": "<li>Java</li>"}], "descriptionPlain": "Build"}])
    assert lv[0].id == "lever:Acme:1" and lv[0].location == "Bengaluru (Remote)"
    assert lv[0].apply_url == "https://jobs.lever.co/Acme/1/apply" and "Java" in lv[0].description
    ab = _ashby("ramp", {"jobs": [
        {"id": "a", "title": " Backend Engineer ", "location": "Remote", "workplaceType": "Remote",
         "secondaryLocations": [{"location": "India"}], "jobUrl": "https://jobs.ashbyhq.com/ramp/a",
         "applyUrl": "https://jobs.ashbyhq.com/ramp/a/application", "descriptionPlain": "Java",
         "compensation": {"compensationTierSummary": "$100K"}},
        {"id": "b", "title": "Hidden", "isListed": False}]})
    assert len(ab) == 1 and ab[0].title == "Backend Engineer" and ab[0].location == "Remote; India"
    assert ab[0].description.startswith("$100K") and ab[0].apply_url.endswith("/application")


def test_db_board_registry(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    assert db.add_board("lever", "Acme", "config") and not db.add_board("lever", "Acme", "search")
    db.add_board("greenhouse", "beta", "probe")
    assert set(db.due_boards(6)) == {("lever", "Acme"), ("greenhouse", "beta")}
    db.mark_board("lever", "Acme")
    db.mark_board("greenhouse", "beta", "dead")
    assert db.due_boards(6) == [] and db.due_boards(-1) == [("lever", "Acme")]
    db.upsert(Job(id="1", source="t", company="Gamma", title="x", url="u"), "low_match")
    db.upsert(Job(id="2", source="t", company="gamma", title="y", url="u"), "low_match")
    assert db.unprobed_companies(10, ["Delta", "delta"]) == ["Delta", "gamma"]
    db.add_probe("Gamma", [("lever", "gamma")])
    assert db.unprobed_companies(10, ["Delta"]) == ["Delta"]
    assert db.get_meta("k", "none") == "none"
    db.set_meta("k", "v")
    assert db.get_meta("k") == "v"


def _cfg(tmp_path, **filters):
    return {"paths": {"db": str(tmp_path / "t.db")}, "limits": {"delay_seconds": [0, 0]}, "filters": filters,
            "sources": {"lever_companies": ["Seed"]},
            "discovery": {"feeds": ["fake"], "keywords": ["java"], "title_any": ["backend", "java"],
                          "eligible_regions": ELIGIBLE, "probe_per_run": 5}}


def _feed_job(jid, title, scope="worldwide", ok=True, desc_ats=()):
    return Job(id=f"fake:{jid}", source="web", company=f"Co{jid}", title=title, url=f"https://agg/{jid}",
               extra={"feed": "fake", "remote_scope": scope, "geo_ok": ok, "ats": list(desc_ats)})


def test_web_discover_orchestration(tmp_path, monkeypatch):
    feed = [_feed_job(1, "Senior Java Engineer", desc_ats=[("ashby", "co1")]),
            _feed_job(1, "Senior Java Engineer"),
            _feed_job(2, "Designer"),
            _feed_job(3, "Backend Engineer", "restricted", ok=False),
            _feed_job(4, "Backend Engineer", "restricted", ok=True),
            Job(id="fake:5", source="web", company="CO4", title="Java Backend Dev", url="https://agg/5",
                extra={"feed": "fake", "remote_scope": "worldwide", "geo_ok": True})]
    monkeypatch.setitem(web.FEEDS, "fake", lambda q, eligible: feed)
    probed = []
    monkeypatch.setattr(web, "probe_company", lambda c: probed.append(c) or ([("greenhouse", "co4")] if c == "Co4" else []))
    polled = []

    def fetch(ats, slug):
        polled.append((ats, slug))
        if slug == "co1":
            return None
        return [Job(id=f"{ats}:{slug}:1", source=ats, company=slug, title="Java Backend Dev", url="u"),
                Job(id=f"{ats}:{slug}:2", source=ats, company=slug, title="Sales Lead", url="u")]
    monkeypatch.setattr(web, "fetch_board", fetch)

    a = web.WebAdapter(_cfg(tmp_path), {}, None, None)
    ids = [j.id for j in a.discover()]
    assert ids == ["lever:Seed:1", "greenhouse:co4:1", "fake:1", "fake:4"]
    assert polled == [("lever", "Seed"), ("ashby", "co1"), ("greenhouse", "co4")]
    assert a.db.due_boards(-1) == [("lever", "Seed"), ("greenhouse", "co4")]
    assert probed == ["Co1", "Co4"]

    a2 = web.WebAdapter(_cfg(tmp_path, require_remote_worldwide=True), {}, None, None)
    assert [j.id for j in a2.feed_jobs()] == ["fake:1", "fake:5"]


def test_web_apply_routing(tmp_path, monkeypatch):
    a = web.WebAdapter(_cfg(tmp_path), {}, None, None)
    calls = []
    a.apply_form_page = lambda driver, job, url, form, submit: calls.append(url) or ApplyResult("dry_run")
    a._shot = lambda driver, job: ""
    reviewed = []
    a.leave_for_review = lambda driver, job, reason, url="": reviewed.append(url) or ApplyResult("needs_manual", reason)
    direct = Job(id="gh:x:1", source="greenhouse", company="x", title="t", url="u",
                 apply_url="https://job-boards.greenhouse.io/embed/job_app?for=x&token=1")
    assert a.apply(None, direct).status == "dry_run" and calls == [direct.apply_url]

    monkeypatch.setattr(web, "resolve_apply_url", lambda d, u: "https://jobs.lever.co/Newco/9/apply")
    feed = _feed_job(5, "Backend Engineer")
    assert a.apply(None, feed).status == "dry_run" and feed.apply_url.endswith("/apply")
    assert ("lever", "Newco") in a.db.due_boards(1000)

    monkeypatch.setattr(web, "resolve_apply_url", lambda d, u: "https://careers.example.com/job/1")
    res = a.apply(None, _feed_job(6, "Backend Engineer"))
    assert res.status == "needs_manual" and "careers.example.com" in res.reason
    assert reviewed == ["https://careers.example.com/job/1"]

    monkeypatch.setattr(web, "resolve_apply_url", lambda d, u: "")
    assert a.apply(None, _feed_job(7, "Backend Engineer")).reason == "apply link not found"
