from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter
from .linkedin import LinkedInAdapter
from .web import WebAdapter

ADAPTERS = {a.source: a for a in (GreenhouseAdapter, LeverAdapter, LinkedInAdapter, WebAdapter)}
