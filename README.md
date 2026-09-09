# CareerRadar

CareerRadar 是一个本地单用户的简历驱动求职 Agent。它把简历拆成稳定证据块，由 Hermes 推荐岗位方向；用户确认方向和城市后，自研抓取器读取 BOSS 直聘公开页面，再以固定权重排名并校验所有引用。

## 产品流程

1. 上传 PDF、DOCX 或粘贴文本。
2. 查看候选人画像和 3 个岗位方向，修改并确认最多 2 个方向与 2 个城市。
3. HTTP 优先抓取公开搜索页和详情页，页面依赖 JavaScript 时由 Playwright Chromium 渲染。
4. 查看岗位排名、硬性风险、简历证据和岗位证据。
5. 获取排名前三岗位对应的 7 天准备计划。

扫描版 PDF 暂不支持 OCR。遇到登录页时，任务进入 `NEEDS_MANUAL_INPUT`，用户可以在应用打开的 BOSS 官方浏览器窗口中自行登录，然后确认并重试原任务。应用不接触账号密码，登录状态只保存在当前服务进程内存中，服务停止后自动清除。遇到验证码或未知页面结构时仍可粘贴岗位描述继续。项目不调用未公开接口，也不提供验证码识别或反爬绕过。

## 快速启动

```bash
cp .env.example .env
# 在 .env 中填写 TOKEN_PLAN_API_KEY
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/playwright install chromium
.venv/bin/uvicorn career_radar.web:app --host 127.0.0.1 --port 8000
```

访问 `http://127.0.0.1:8000`。也可以运行 `docker compose up --build`。

默认模型配置：

```dotenv
MODEL_BASE_URL=https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
MODEL_NAME=deepseek-v4-pro-0813
TOKEN_PLAN_API_KEY=
```

应用兼容用户先前在 `../research-workspace/hermes-home/.env` 中配置的 `TOKEN_PLAN_API_KEY`，但不会复制或显示密钥。`.env`、数据库、Cookie 和模型请求转储均被 Git 忽略。

## 代码边界

- `career_radar/resume.py`：格式校验、文本提取、稳定证据块。
- `career_radar/crawler.py`：调度、HTTP、浏览器降级、BOSS 页面解析和去重标识。
- `career_radar/browser_session.py`：打开 BOSS 官方登录窗口，并向抓取器提供仅驻留内存的浏览器会话。
- `career_radar/scoring.py`：40/25/15/5/15 固定权重评分。
- `career_radar/agent.py`：通过未修改的 Hermes 内核调用兼容模型，并在失败时给出确定性降级结果。
- `hermes-plugin/`：独立 Agent Plugins v1 包，包含 `career-analysis` 技能和 6 个固定产品工具。
- `career_radar/database.py`：SQLite 画像、任务、快照、变化和报告存储。

Hermes 是 Agent 内核。抓取队列、网络传输、浏览器控制、解析、去重、快照和评分全部由本仓库实现，没有使用 Scrapy、Crawl4AI 或其他爬虫框架。Playwright 只负责浏览器渲染。

## 测试

```bash
.venv/bin/pytest
```

CI 只读取 `tests/fixtures`，不会访问真实站点。真实站点冒烟测试必须显式设置 `CAREER_RADAR_LIVE_TEST=1`。评测数据目录预留给 10 份脱敏简历和至少 50 个岗位页面；简历中只能写实际跑出的接受率、成功率、字段完整率、引用覆盖率、P50/P95 耗时和 Token 成本。

## 当前限制

首版只支持 BOSS 直聘和北京、上海、深圳、广州、杭州、成都、武汉、南京、西安、苏州。不包含自动投递、自动联系招聘者、多用户权限、OCR、代理池或验证码处理。登录会话不会跨服务重启保留。BOSS 页面结构可能变化，fixture 测试通过并不保证真实站点始终可访问。
