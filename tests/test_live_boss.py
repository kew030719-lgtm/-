import os

import pytest

from career_radar.sites import Transport
from career_radar.sites.boss import BossAdapter


pytestmark = pytest.mark.skipif(
    os.getenv("CAREER_RADAR_LIVE_TEST") != "1",
    reason="真实站点测试必须显式设置 CAREER_RADAR_LIVE_TEST=1",
)

BOSS = BossAdapter()


@pytest.mark.asyncio
async def test_public_boss_search_page_live():
    transport = Transport(BOSS, delay_seconds=10)
    try:
        result = await transport.fetch(BOSS.search_url("Python", "北京", 1), "search")
        assert BOSS.parse_search_page(result.text)
    finally:
        await transport.close()