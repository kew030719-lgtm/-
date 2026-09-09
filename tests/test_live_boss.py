import os

import pytest

from career_radar.crawler import BossTransport, parse_search_page


pytestmark = pytest.mark.skipif(
    os.getenv("CAREER_RADAR_LIVE_TEST") != "1",
    reason="真实站点测试必须显式设置 CAREER_RADAR_LIVE_TEST=1",
)


@pytest.mark.asyncio
async def test_public_boss_search_page_live():
    transport = BossTransport(delay_seconds=10)
    try:
        result = await transport.fetch(
            "https://www.zhipin.com/web/geek/job?query=Python&city=101010100&page=1",
            "search",
        )
        assert parse_search_page(result.text)
    finally:
        await transport.close()
