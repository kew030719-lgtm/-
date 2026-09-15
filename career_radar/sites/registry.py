"""The site registry.

Adding a site is a one-file job: write a SiteAdapter and list it here. Nothing
in web.py, services.py, database.py or scoring.py needs to change.

猎聘 (liepin.com) is deliberately absent. It did not render at all to an
automated browser here (39 bytes returned), so its selectors could not be read
off a real page — and an adapter built from guessed selectors would silently
produce empty or wrong results. It needs a captured search page and detail page
from a signed-in browser first.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from .base import SiteAdapter
from .boss import BossAdapter
from .job51 import Job51Adapter
from .zhaopin import ZhaopinAdapter

SITES: dict[str, SiteAdapter] = {
    adapter.key: adapter
    for adapter in (BossAdapter(), ZhaopinAdapter(), Job51Adapter())
}


def get_site(key: str) -> SiteAdapter:
    adapter = SITES.get(key)
    if adapter is None:
        raise KeyError(f"未知站点：{key}")
    return adapter


def all_sites() -> list[SiteAdapter]:
    return list(SITES.values())


def site_for_url(url: str) -> SiteAdapter:
    """The adapter that owns a URL's host.

    The Chrome helper only reports back the URL it visited, so host matching is
    how a captured page is routed to the right parser.
    """
    host = urlsplit(url).hostname or ""
    for adapter in SITES.values():
        if host in adapter.hosts:
            return adapter
    raise KeyError(f"未知站点：{host or url[:60]}")


def cities_for(sites: list[str]) -> list[str]:
    """City names offered by at least one of the given sites, in stable order."""
    names: list[str] = []
    for key in sites:
        for city in get_site(key).cities:
            if city not in names:
                names.append(city)
    return names