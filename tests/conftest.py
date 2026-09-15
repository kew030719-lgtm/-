"""Shared test setup.

The suite is deliberately hermetic: each test builds its own ``Settings`` rooted
at ``tmp_path`` and never touches the network.

Ambient proxy variables still leak in through httpx, which reads them every time
an ``AsyncClient`` is constructed — and a proxy URL in a scheme httpx does not
accept turns an incidental client construction into a hard failure. Clash Verge
exports ``ALL_PROXY=socks://127.0.0.1:7897/``; httpx only accepts ``socks5`` or
``socks5h``, so every test that builds a client dies on the developer's machine
while passing in CI. Clearing them keeps local runs honest.
"""

import os

import pytest

_PROXY_VARS = (
    "ALL_PROXY", "all_proxy",
    "HTTP_PROXY", "http_proxy",
    "HTTPS_PROXY", "https_proxy",
    "NO_PROXY", "no_proxy",
)


@pytest.fixture(autouse=True)
def _no_ambient_proxy(monkeypatch):
    # A live run may genuinely need the developer's proxy to reach the internet,
    # so only the offline suite is scrubbed.
    if os.getenv("CAREER_RADAR_LIVE_TEST") == "1":
        return
    for name in _PROXY_VARS:
        monkeypatch.delenv(name, raising=False)