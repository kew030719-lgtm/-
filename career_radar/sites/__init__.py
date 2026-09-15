"""Job-site adapters.

`scoring.py` and `database.py` are site-neutral; everything that knows about a
particular job board lives in this package.
"""

from .base import (
    CrawlError, NeedsManualInput, SiteAdapter, build_snapshot, clean_text, job_identity, parse_salary,
)
from .registry import SITES, all_sites, cities_for, get_site, site_for_url
from .transport import FetchResult, SiteCrawler, Transport

__all__ = [
    "SITES", "CrawlError", "FetchResult", "NeedsManualInput", "SiteAdapter", "SiteCrawler",
    "Transport", "all_sites", "build_snapshot", "cities_for", "clean_text", "get_site",
    "job_identity", "parse_salary", "site_for_url",
]