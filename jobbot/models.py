from dataclasses import dataclass, field


@dataclass
class Job:
    id: str
    source: str
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""
    apply_url: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class MatchResult:
    score: int
    reasons: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    remote_scope: str = "unknown"
    pay_currency: str = "unknown"


@dataclass
class ApplyResult:
    status: str
    reason: str = ""
    screenshot: str = ""
