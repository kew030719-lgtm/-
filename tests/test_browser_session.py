import pytest

from career_radar.browser_session import BossLoginSession, BrowserSessionError


class FakePage:
    def __init__(self, url):
        self.url = url

    def is_closed(self):
        return False


class FakeContext:
    def __init__(self, cookies):
        self.cookies = cookies

    async def storage_state(self):
        return {"cookies": self.cookies, "origins": []}


class FakeClosable:
    async def close(self):
        return None

    async def stop(self):
        return None


@pytest.mark.asyncio
async def test_login_must_leave_login_page_before_confirmation():
    session = BossLoginSession()
    session._page = FakePage("https://www.zhipin.com/web/user/?ka=header-login")
    session._context = FakeContext([{"domain": ".zhipin.com", "name": "wt2", "value": "secret"}])
    with pytest.raises(BrowserSessionError, match="尚未跳转"):
        await session.confirm()


@pytest.mark.asyncio
async def test_confirmed_login_state_is_kept_only_in_memory():
    session = BossLoginSession()
    session._page = FakePage("https://www.zhipin.com/web/geek/job")
    session._context = FakeContext([{"domain": ".zhipin.com", "name": "wt2", "value": "secret"}])
    session._browser = FakeClosable()
    session._playwright = FakeClosable()
    status = await session.confirm()
    assert status["window_open"] is False
    assert status["confirmed"] is True
    assert status["session_in_memory"] is True
    assert status["mode"] in {"window", "preview"}
    assert session.storage_state()["cookies"][0]["name"] == "wt2"
    await session.clear()
    assert session.storage_state() is None
