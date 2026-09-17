import asyncio
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from test_api_flow import RESUME, settings, wait_task

from career_radar.sites import get_site
from career_radar.web import create_app


def test_auto_discovery_lifecycle(tmp_path):
    asyncio.run(lifecycle(tmp_path))


def test_selected_site_is_the_only_site_in_the_browser_queue(tmp_path):
    asyncio.run(selected_site_queue(tmp_path))


async def selected_site_queue(tmp_path):
    app = create_app(settings(tmp_path))
    async with (
      app.router.lifespan_context(app),
      httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        profile = (await client.post("/api/resumes", json={"text": RESUME})).json()
        pid = profile["profile_id"]
        selected_role = {
            **profile["recommendations"][0],
            "role": "大模型应用开发工程师",
            "keywords": ["AI Agent", "Function Calling", "LangGraph"],
        }
        await client.put(
            f"/api/profiles/{pid}",
            json={"selected_roles": [selected_role], "cities": ["北京"]},
        )
        for site_key in ("boss", "zhaopin", "job51"):
            run = (await client.post(
                "/api/discovery-runs",
                json={"profile_id": pid, "sites": [site_key]},
            )).json()["task_id"]
            plan = (await client.post(f"/api/browser-runs/{run}/start")).json()
            allowed_hosts = get_site(site_key).hosts
            assert plan["queue"]
            assert all(urlsplit(item["url"]).hostname in allowed_hosts for item in plan["queue"])
            queries = {
                next(value[0] for key, value in parse_qs(urlsplit(item["url"]).query).items()
                     if key in {"query", "kw", "keyword"})
                for item in plan["queue"]
            }
            assert queries == {"大模型应用开发工程师", "AI Agent"}


async def lifecycle(tmp_path):
    app = create_app(settings(tmp_path))
    fixtures = Path(__file__).parent / "fixtures"
    async with app.router.lifespan_context(app):
      async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        profile = (await client.post('/api/resumes', json={'text': RESUME})).json()
        pid = profile['profile_id']
        assert (await client.post('/api/discovery-runs', json={'profile_id': pid})).status_code == 409
        await client.put(f'/api/profiles/{pid}', json={'selected_roles': profile['recommendations'][:2], 'cities': ['北京', '上海']})
        run = (await client.post('/api/discovery-runs', json={'profile_id': pid})).json()['task_id']
        assert (await client.get('/api/browser-runs/pending')).json()['task_id'] == run
        older = run
        newer = (await client.post('/api/discovery-runs', json={'profile_id': pid})).json()['task_id']
        assert (await client.get('/api/browser-runs/pending')).json()['task_id'] == newer
        assert (await client.get('/api/browser-runs/pending', params={'preferred_run_id': older})).json()['task_id'] == older
        await client.delete(f'/api/tasks/{newer}')
        plan = (await client.post(f'/api/browser-runs/{run}/start')).json()
        assert len(plan['queue']) == 16 and plan['interval_ms'] >= 10000
        assert {parse_qs(urlsplit(q['url']).query)['page'][0] for q in plan['queue']} == {'1', '2'}
        assert (await client.post(f'/api/browser-runs/{run}/start')).status_code == 409
        body = {'run_id': run, 'url': plan['queue'][0]['url'], 'html': (fixtures/'search.html').read_text(), 'city': '北京'}
        assert (await client.post('/api/browser-search-pages', json=body)).json()['links']
        body['html'] = '<html><head><title>搜索结果</title></head><body>' + '普通搜索页面' * 20 + '</body></html>'
        empty = await client.post('/api/browser-search-pages', json=body)
        assert empty.status_code == 422
        assert '搜索词无结果' in empty.json()['detail'] and '登录' not in empty.json()['detail']
        body['url'] = 'http://127.0.0.1/private'
        assert (await client.post('/api/browser-search-pages', json=body)).status_code == 422
        body['url'] = plan['queue'][0]['url']
        body['html'] = (fixtures/'captcha.html').read_text()
        assert (await client.post('/api/browser-search-pages', json=body)).status_code == 422
        await client.post(f'/api/browser-runs/{run}/progress', json={'status':'NEEDS_MANUAL_INPUT','message':'需要登录'})
        await client.post(f'/api/browser-runs/{run}/progress', json={'status':'RUNNING','message':'恢复'})
        body.update(url='https://www.zhipin.com/job_detail/abc123.html', html=(fixtures/'job.html').read_text())
        for _ in range(2):
            assert (await client.post('/api/browser-captures', json=body)).json()['count'] == 1
        for suffix in ('second', 'third'):
            extra = {**body, 'url': f'https://www.zhipin.com/job_detail/{suffix}.html',
                     'html': body['html'].replace('Python Agent', f'Python {suffix} Agent')}
            assert (await client.post('/api/browser-captures', json=extra)).status_code == 200
        assert (await client.post('/api/browser-captures', json=body)).status_code == 409
        finished = await client.post(f'/api/browser-captures/{run}/finish')
        assert finished.status_code == 200
        cmp = (await client.post('/api/comparisons', json={'profile_id':pid,'run_id':run})).json()
        assert cmp['task_id'] == finished.json()['comparison']['task_id']
        assert (await wait_task(client, cmp['task_id']))['status'] == 'SUCCEEDED'
        assert (await client.post('/api/browser-captures', json=body)).status_code == 409
        run2 = (await client.post('/api/discovery-runs', json={'profile_id': pid})).json()['task_id']
        await client.post(f'/api/browser-runs/{run2}/start')
        await client.delete(f'/api/tasks/{run2}')
        body['run_id'] = run2
        assert (await client.post('/api/browser-captures', json=body)).status_code == 409
        assert (await client.post(f'/api/browser-runs/{run2}/start')).status_code == 409
        assert (await client.post(f'/api/browser-captures/{run2}/finish')).status_code == 409
